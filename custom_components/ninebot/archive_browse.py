"""Bounded local keyset pages sharing Calendar's current selection semantics."""

import secrets
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

from .archive_codec import decoded, restored_month, restored_ride, stamp
from .archive_timeline import (
    MAX_TIMELINE_BYTES,
    MAX_TIMELINE_RIDES,
    ArchivedRide,
    TimelineLimitError,
    current_selection,
    valid_timed_ride,
)


class RecordedCursorMismatch(Exception):
    """A read continuation no longer describes this database/scope revision."""


@dataclass(frozen=True)
class RecordedCursor:
    owner: str
    vehicle: str
    revision: int
    start: str
    end: str
    as_of: str
    after_start: str
    after_id: str


@dataclass(frozen=True)
class RecordedMonth:
    month: str
    received_at: datetime
    list_complete: bool | None
    reported_count: int | None
    returned_count: int


@dataclass(frozen=True)
class RecordedPage:
    records: tuple[ArchivedRide, ...]
    revision: int
    next_cursor: RecordedCursor | None
    months: tuple[RecordedMonth, ...]
    skipped_conflicting_times: int


class RecordedCursors:
    """Tiny entry-local continuations: no rides, raw JSON or durable tokens."""

    def __init__(self) -> None:
        self._values: OrderedDict[str, tuple[datetime, RecordedCursor]] = OrderedDict()

    def get(self, token: str, now: datetime) -> RecordedCursor | None:
        self.expire(now)
        value = self._values.get(token)
        return value[1] if value else None

    def put(self, cursor: RecordedCursor, now: datetime) -> str:
        self.expire(now)
        while len(self._values) >= 8:
            self._values.popitem(last=False)
        token = secrets.token_hex(16)
        self._values[token] = now, cursor
        return token

    def expire(self, now: datetime) -> None:
        for token, (received, _) in list(self._values.items()):
            if not 0 <= (now - received).total_seconds() < 900:
                self._values.pop(token)

    def clear(self) -> None:
        self._values.clear()


def recorded_page(
    db: sqlite3.Connection,
    owner: str,
    vehicle: str,
    start: str,
    end: str,
    as_of: str,
    limit: int,
    first_month: str,
    last_month: str,
    cursor: RecordedCursor | None,
) -> RecordedPage:
    """Single actor read, at most one output page; never load every ride first."""
    revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
    if cursor and (
        cursor.owner != owner
        or cursor.vehicle != vehicle
        or cursor.revision != revision
        or cursor.start != start
        or cursor.end != end
        or cursor.as_of != as_of
    ):
        raise RecordedCursorMismatch
    selection, arguments = current_selection(db, vehicle)
    following = " AND (started,id)>(?,?)" if cursor else ""
    after = (cursor.after_start, cursor.after_id) if cursor else ()
    rows = db.execute(
        "SELECT data,observed,started,id FROM rides "
        "WHERE vehicle=? AND ended>? AND started<? AND ended<=? "
        f"AND started<>'' AND ended>started AND {selection}{following} "
        "ORDER BY started,id LIMIT ?",
        (vehicle, start, end, as_of, *arguments, *after, MAX_TIMELINE_RIDES + 1),
    )
    records: list[ArchivedRide] = []
    position: tuple[str, str] | None = None
    next_cursor = None
    size = 0
    skipped = 0
    for index, (data, observed, started, identity) in enumerate(rows):
        size += len(data.encode())
        if index >= MAX_TIMELINE_RIDES or size > MAX_TIMELINE_BYTES:
            raise TimelineLimitError
        ride = restored_ride(decoded(data))
        if not valid_timed_ride(ride):
            skipped += 1
            continue
        if len(records) == limit:
            assert position is not None
            next_cursor = RecordedCursor(owner, vehicle, revision, start, end, as_of, *position)
            break
        records.append(ArchivedRide(ride, stamp(observed)))
        position = started, identity
    # Metadata uses the same connection/revision, not a second mutable query.
    months = []
    for month, data, observed in db.execute(
        "SELECT month,data,observed FROM months "
        "WHERE vehicle=? AND month>=? AND month<=? ORDER BY month",
        (vehicle, first_month, last_month),
    ):
        size += len(data.encode())
        if size > MAX_TIMELINE_BYTES:
            raise TimelineLimitError
        travel = restored_month(decoded(data))
        assert travel.summary is not None
        summary = travel.summary
        months.append(
            RecordedMonth(
                month,
                stamp(observed),
                summary.list_complete,
                summary.ride_count,
                summary.returned_count,
            )
        )
    return RecordedPage(tuple(records), revision, next_cursor, tuple(months), skipped)
