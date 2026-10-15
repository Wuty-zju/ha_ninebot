"""Bounded recorded-page metadata consistency, never a raw integrity certificate."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .archive_codec import decoded, stamp
from .history_plan import MAX_PAGES, PROVIDERS
from .history_store import resolve
from .travel import opaque_id

MAX_IDS = 5000
MAX_ID_BYTES = 1024 * 1024


@dataclass(frozen=True)
class RecordedEnumeration:
    pages: int
    rides: int
    received_at: datetime


def _ids(value: str | None) -> tuple[str, ...] | None:
    if not isinstance(value, str) or len(value.encode()) > MAX_ID_BYTES:
        return None
    try:
        data = decoded(value)
    except ValueError:
        return None
    if not isinstance(data, dict) or set(data) != {"ids"} or not isinstance(data["ids"], list):
        return None
    ids = data["ids"]
    if len(ids) > MAX_IDS or any(not isinstance(i, str) or opaque_id(i) != i for i in ids):
        return None
    return tuple(ids) if len(set(ids)) == len(ids) else None


def recorded_enumeration(
    db: sqlite3.Connection,
    vehicle: str,
    month: str,
    observed: str,
    reported: int | None,
    as_of: str,
) -> RecordedEnumeration | None:
    """Only the current month's exact receipt can match one completed page chain.

    Inspect bounded ID/header metadata on the caller's actor read. Raw payload
    hashes, metric meanings and upstream date boundaries remain separate audits.
    No state, selection, freshness or original single-page completeness is changed.
    """
    if type(reported) is not int or not 0 <= reported <= MAX_IDS:
        return None
    try:
        observed_at = stamp(observed)
        if observed_at > stamp(as_of):
            return None
    except ValueError:
        return None
    candidates = db.execute(
        "SELECT plan,pages,known FROM history_months WHERE vehicle=? AND month=?"
        " AND observed=? AND verified=1 AND stop IS NULL AND known=total AND total=? LIMIT 2",
        (vehicle, month, observed, reported),
    ).fetchall()
    if len(candidates) != 1:
        return None
    plan_id, pages, known = candidates[0]
    try:
        plan = resolve(db, vehicle, plan_id)
    except ValueError:
        return None
    if (
        plan is None
        or plan.provider not in PROVIDERS
        or not plan.start_month <= month <= plan.end_month
    ):
        return None
    scope = (vehicle, plan_id, month)
    count, size = db.execute(
        "SELECT count(*),coalesce(sum(length(CAST(ids AS BLOB))),0) FROM history_pages"
        " WHERE vehicle=? AND plan=? AND month=?",
        scope,
    ).fetchone()
    if not 1 <= pages == count <= MAX_PAGES or size > MAX_ID_BYTES:
        return None
    parent = db.execute(
        "SELECT CASE WHEN length(CAST(ids AS BLOB))<=? THEN ids END FROM months"
        " WHERE vehicle=? AND month=? AND observed=?",
        (MAX_ID_BYTES, vehicle, month, observed),
    ).fetchone()
    parent_ids = _ids(parent[0]) if parent else None
    if parent_ids is None:
        return None
    seen: dict[str, int] = {}
    last_ids: tuple[str, ...] = ()
    last_observed: datetime | None = None
    for expected, row in enumerate(
        db.execute(
            "SELECT p.page,p.ids,p.total,p.observed,p.backend,p.endpoint,p.stop,"
            " (length(p.body)>0 OR h.digest IS NOT NULL) FROM history_pages p"
            " LEFT JOIN history_payloads h ON h.digest=p.digest"
            " WHERE p.vehicle=? AND p.plan=? AND p.month=? ORDER BY p.page",
            scope,
        ),
        1,
    ):
        page, data, total, received, backend, endpoint, stop, payload_exists = row
        ids = _ids(data)
        if (
            page != expected
            or total != known
            or backend != "0.1.7"
            or endpoint != plan.provider
            or stop is not None
            or not payload_exists
            or ids is None
        ):
            return None
        try:
            at = stamp(received)
        except ValueError:
            return None
        if at > observed_at or (last_observed is not None and at < last_observed):
            return None
        before = len(seen)
        for identity in ids:
            seen.setdefault(identity, page)
        if (known and len(seen) == before) or (page < pages and len(seen) >= known):
            return None
        if len(seen) > MAX_IDS:
            return None
        last_ids, last_observed = ids, at
    if len(seen) != known or last_observed != observed_at or set(parent_ids) != set(last_ids):
        return None
    indexed = db.execute(
        "SELECT i.ride,i.first_page,r.id,m.latest FROM history_ids i"
        " LEFT JOIN rides r ON r.vehicle=i.vehicle AND r.id=i.ride"
        " LEFT JOIN membership m ON m.vehicle=i.vehicle AND m.month=i.month AND m.ride=i.ride"
        " WHERE i.vehicle=? AND i.plan=? AND i.month=? LIMIT ?",
        (*scope, MAX_IDS + 1),
    ).fetchall()
    if len(indexed) != known or any(
        seen.get(identity) != first or ride is None or latest != (identity in parent_ids)
        for identity, first, ride, latest in indexed
    ):
        return None
    return RecordedEnumeration(pages, known, observed_at)
