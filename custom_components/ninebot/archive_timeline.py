"""Bounded, read-only historical selections, executed on the archive worker.

An incomplete month omission is not deletion. A record omitted from every
latest complete membership is retained as evidence, outside this current view.
No JSON extension, cloud transport, raw payload or location is required.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .archive_codec import decoded, restored_month, restored_ride, stamp
from .ride_models import Ride

MAX_TIMELINE_RIDES = 5000
MAX_TIMELINE_BYTES = 8 * 1024 * 1024
MAX_TIMELINE_DAYS = 5 * 366
SELECTION_BASIS = "latest_lists_and_retained_partial_observations"


class TimelineLimitError(Exception):
    """A query budget is exhausted, not an archive corruption or truncation."""


@dataclass(frozen=True)
class ArchivedRide:
    ride: Ride
    received_at: datetime


@dataclass(frozen=True)
class ArchiveTimeline:
    records: tuple[ArchivedRide, ...]
    revision: int
    omitted_invalid_times: int
    selection_basis: str = SELECTION_BASIS


def _complete(data: str) -> bool:
    month = restored_month(decoded(data))
    assert month.summary is not None
    return month.summary.list_complete is True


def current_selection(db: sqlite3.Connection, vehicle: str) -> tuple[str, tuple[str, ...]]:
    partial = tuple(
        month
        for month, data in db.execute("SELECT month,data FROM months WHERE vehicle=?", (vehicle,))
        if not _complete(data)
    )
    retained = f" OR month IN ({','.join('?' for _ in partial)})" if partial else ""
    return (
        f"id IN (SELECT ride FROM membership WHERE vehicle=? AND (latest=1{retained}))",
        (vehicle, *partial),
    )


def valid_timed_ride(ride: Ride) -> bool:
    return (
        ride.started_at is not None
        and ride.ended_at is not None
        and ride.started_at < ride.ended_at
        and not {"reversed_timestamps", "conflicting_time_representations"} & set(ride.issues)
    )


def timeline_query(
    db: sqlite3.Connection, vehicle: str, start: str, end: str, limit: int
) -> ArchiveTimeline:
    selection, arguments = current_selection(db, vehicle)
    cursor = db.execute(
        "SELECT data,observed FROM rides WHERE vehicle=? AND ended>? AND started<? "
        f"AND started<>'' AND ended>started AND {selection} ORDER BY started,id LIMIT ?",
        (vehicle, start, end, *arguments, limit + 1),
    )
    records: list[ArchivedRide] = []
    size = 0
    scanned = 0
    invalid = 0
    for data, observed in cursor:
        scanned += 1
        size += len(data.encode())
        if scanned > limit or size > MAX_TIMELINE_BYTES:
            raise TimelineLimitError
        ride = restored_ride(decoded(data))
        if valid_timed_ride(ride):
            records.append(ArchivedRide(ride, stamp(observed)))
        else:
            invalid += 1
    revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
    return ArchiveTimeline(tuple(records), revision, invalid)


def latest_query(db: sqlite3.Connection, vehicle: str, now: str) -> ArchivedRide | None:
    selection, arguments = current_selection(db, vehicle)
    cursor = db.execute(
        "SELECT data,observed FROM rides WHERE vehicle=? AND ended<=? "
        f"AND started<>'' AND ended>started AND {selection} "
        "ORDER BY ended DESC,id DESC LIMIT ?",
        (vehicle, now, *arguments, MAX_TIMELINE_RIDES + 1),
    )
    size = 0
    for index, (data, observed) in enumerate(cursor):
        size += len(data.encode())
        if index >= MAX_TIMELINE_RIDES or size > MAX_TIMELINE_BYTES:
            raise TimelineLimitError
        ride = restored_ride(decoded(data))
        if valid_timed_ride(ride):
            return ArchivedRide(ride, stamp(observed))
    return None
