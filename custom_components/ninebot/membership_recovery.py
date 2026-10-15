"""Prove missing month links from retained page evidence; never infer history.

The caller owns the account actor, recovery backup and write transaction. Existing
links, including complete-list withdrawals, are never changed by this helper.
"""

import sqlite3
from datetime import datetime

from .archive_codec import decoded
from .const import MAX_RESPONSE_BYTES
from .history_store import page, resolve
from .ride_models import Ride
from .travel import MAX_RIDES, opaque_id, parse_rides

MAX_MONTHS = 8
MAX_PAGES = 8
MAX_BYTES = 4 * MAX_RESPONSE_BYTES


def proposal(
    db: sqlite3.Connection,
    vehicle: str,
    current: Ride,
    candidate: Ride | None,
    evidence_at: datetime,
) -> tuple[tuple[str, int], ...] | None:
    # Missing current query-month identity triggers this repair. Healthy rides
    # need one primary-key lookup, not a full vehicle-history scan per ride.
    if db.execute(
        "SELECT 1 FROM membership WHERE vehicle=? AND month=? AND ride=?",
        (vehicle, current.query_month, current.ride_id),
    ).fetchone():
        return ()
    missing = [
        row[0]
        for row in db.execute(
            "SELECT DISTINCT i.month FROM history_ids i LEFT JOIN membership m"
            " ON m.vehicle=i.vehicle AND m.month=i.month AND m.ride=i.ride"
            " WHERE i.vehicle=? AND i.ride=? AND m.ride IS NULL ORDER BY i.month LIMIT ?",
            (vehicle, current.ride_id, MAX_MONTHS + 1),
        )
    ]
    if current.query_month not in missing:
        missing.append(current.query_month)
    if not missing:
        return ()
    if len(missing) > MAX_MONTHS or candidate is None or candidate.started_at is None:
        return None
    updates: list[tuple[str, int]] = []
    count = size = 0
    for month in missing:
        parent = db.execute(
            "SELECT ids FROM months WHERE vehicle=? AND month=?", (vehicle, month)
        ).fetchone()
        if parent is None:
            return None  # A page is not a replacement for unknown monthly metadata.
        selection = decoded(parent[0])
        ids = selection["ids"]
        if (
            set(selection) != {"ids"}
            or not isinstance(ids, list)
            or len(ids) > MAX_RIDES
            or any(not isinstance(value, str) or opaque_id(value) != value for value in ids)
            or len(ids) != len(set(ids))
        ):
            return None
        verified = False
        for plan_id, number in db.execute(
            "SELECT i.plan,i.first_page FROM history_ids i JOIN history_pages p"
            " ON p.vehicle=i.vehicle AND p.plan=i.plan AND p.month=i.month"
            " AND p.page=i.first_page WHERE i.vehicle=? AND i.ride=? AND i.month=?"
            " AND p.observed<=? ORDER BY p.observed DESC,i.plan LIMIT 2",
            (vehicle, current.ride_id, month, evidence_at.isoformat(timespec="microseconds")),
        ):
            count += 1
            if count > MAX_PAGES:
                return None
            source = resolve(db, vehicle, plan_id)
            raw = page(db, vehicle, plan_id, month, number)
            if (
                source is None
                or raw is None
                or raw.backend_version != "0.1.7"
                or raw.endpoint_version != source.provider
                or not source.start_month <= month <= source.end_month
                or raw.received_at > evidence_at
            ):
                return None
            size += len(raw.encoded)
            if size > MAX_BYTES:
                return None
            witnesses = [
                ride
                for ride in parse_rides(raw.payload(), month)
                if ride.ride_id == current.ride_id
            ]
            if len(witnesses) != 1:
                continue
            witness = witnesses[0]
            if (
                witness.detail_id != current.detail_id
                or witness.started_at != candidate.started_at
                or {
                    "conflicting_ride_ids",
                    "conflicting_detail_ids",
                    "conflicting_time_representations",
                    "reversed_timestamps",
                }
                & set(witness.issues)
            ):
                return None
            verified = True
            break
        if not verified:
            return None
        # Preserve current selection semantics: older evidence does not revive
        # an ID omitted by a newer complete list. Partial months retain latest=0.
        updates.append((month, int(current.ride_id in ids)))
    return tuple(updates)
