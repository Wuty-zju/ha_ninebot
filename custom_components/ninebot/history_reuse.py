"""Bounded reuse of verified independent month evidence by first discovery.

Never infer enumeration from normalized rows. Validate the entire bounded source
before any mutation; preserve received timestamps and record local acceptance
separately. Caller provides the existing account transaction and ownership guard.
"""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from .archive_codec import decoded, encoded, stamp
from .const import BUSINESS_TIMEZONE, MAX_RESPONSE_BYTES
from .history_plan import HistoryState
from .history_store import _shape, page, put_root, resolve, root
from .incremental_runs import run_key
from .parsing import previous_month

PREFIX = "history_reuse:"
MAX_PAGES = 8
MAX_BYTES = 4 * MAX_RESPONSE_BYTES
PAGE_COLUMNS = "page,digest,observed,backend,endpoint,ids,total,stop"


def key(vehicle: str, plan: str, month: str) -> str:
    run_key(vehicle, plan)
    previous_month(month)
    return f"{PREFIX}{vehicle}:{plan}:{month}"


def check(db: sqlite3.Connection, scope: str, value: str) -> None:
    _, vehicle, target, month = scope.split(":")
    if key(vehicle, target, month) != scope:
        raise ValueError("Invalid reused month scope")
    data = decoded(value)
    if set(data) != {"source_plan", "accepted_at"}:
        raise ValueError("Invalid reused month provenance")
    run_key(vehicle, data["source_plan"])
    baseline = root(db, vehicle)
    source = resolve(db, vehicle, data["source_plan"])
    if (
        baseline is None
        or baseline.plan_id != target
        or source is None
        or source.plan_id == target
        or source.start_month != month
        or source.end_month != month
        or not source.discovery_complete
        or source.provider != baseline.provider
        or stamp(source.cutoff) < stamp(baseline.cutoff)
        or not stamp(baseline.created_at)
        <= stamp(data["accepted_at"])
        <= stamp(baseline.updated_at)
    ):
        raise ValueError("Invalid reused month origin")
    source_scope, target_scope = (vehicle, source.plan_id, month), (vehicle, target, month)
    manifests = []
    for selected in (source_scope, target_scope):
        manifest = db.execute(
            "SELECT pages,known,total,verified,stop,observed FROM history_months"
            " WHERE vehicle=? AND plan=? AND month=?",
            selected,
        ).fetchone()
        if manifest is None or not 1 <= manifest[0] <= MAX_PAGES or manifest[3:5] != (1, None):
            raise ValueError("Missing verified reuse manifest")
        manifests.append(manifest)
    if manifests[0] != manifests[1]:
        raise ValueError("Reused month manifest mismatch")
    accepted = stamp(data["accepted_at"])
    if (
        month >= previous_month(accepted.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).strftime("%Y%m"))
        or stamp(manifests[0][5]) > accepted
    ):
        raise ValueError("Invalid reused month acceptance time")
    for columns, table, order in (
        (PAGE_COLUMNS, "history_pages", "page"),
        ("ride,first_page", "history_ids", "ride"),
    ):
        query = (
            f"SELECT {columns} FROM {table} WHERE vehicle=? AND plan=? AND month=? ORDER BY {order}"
        )
        if db.execute(query, source_scope).fetchall() != db.execute(query, target_scope).fetchall():
            raise ValueError("Reused month provenance mismatch")


def reuse(db: sqlite3.Connection, vehicle: str, now: datetime) -> bool:
    expected = root(db, vehicle)
    if (
        expected is None
        or expected.state is not HistoryState.DISCOVER
        or expected.next_month is None
        or expected.next_page != 1
        or expected.next_month
        >= previous_month(now.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).strftime("%Y%m"))
        or stamp(expected.updated_at) > now
    ):
        return False
    month = expected.next_month
    active = db.execute(
        "SELECT value FROM meta WHERE key=?", (f"history_run_active:{vehicle}:{month}",)
    ).fetchone()
    source = resolve(db, vehicle, active[0]) if active else None
    if (
        source is None
        or source.plan_id == expected.plan_id
        or source.start_month != month
        or source.end_month != month
        or not source.discovery_complete
        or source.provider != expected.provider
        or stamp(source.cutoff) < stamp(expected.cutoff)
    ):
        return False
    scope = (vehicle, source.plan_id, month)
    manifest = db.execute(
        "SELECT pages,known,total,verified,stop,observed FROM history_months"
        " WHERE vehicle=? AND plan=? AND month=?",
        scope,
    ).fetchone()
    if (
        manifest is None
        or not 1 <= manifest[0] <= MAX_PAGES
        or manifest[1] != manifest[2]
        or manifest[3:5] != (1, None)
    ):
        return False
    newer = db.execute(
        "SELECT observed FROM months WHERE vehicle=? AND month=?", (vehicle, month)
    ).fetchone()
    if newer and stamp(newer[0]) > stamp(manifest[5]):
        return False  # A later observation must be reconciled, even if counts stayed equal.
    receipts = db.execute(
        f"SELECT {PAGE_COLUMNS} FROM history_pages WHERE vehicle=? AND plan=? AND month=?"
        " ORDER BY page",
        scope,
    ).fetchall()
    if len(receipts) != manifest[0]:
        return False
    identities: dict[str, int] = {}
    consumed = 0
    previous = stamp(expected.cutoff)
    for number, receipt in enumerate(receipts, 1):
        raw = page(db, vehicle, source.plan_id, month, number)
        if raw is None:
            return False
        consumed += len(raw.encoded)
        if consumed > MAX_BYTES or not previous <= raw.received_at <= now:
            return False
        previous = raw.received_at
        payload = raw.payload()
        if not isinstance(payload, dict):
            return False
        ids, total, stop = _shape(payload)
        if (
            receipt[0] != number
            or receipt[3] != "0.1.7"
            or receipt[4] != expected.provider
            or receipt[5] != encoded({"ids": ids})
            or receipt[6] != total
            or receipt[7] is not None
            or total is None
            or total != manifest[2]
            or stop is not None
            or (
                number < len(receipts)
                and len(identities) + len(set(ids) - identities.keys()) >= total
            )
            or (number > 1 and not set(ids) - identities.keys())
        ):
            return False
        for identity in ids:
            identities.setdefault(identity, number)
    if (
        len(identities) != manifest[1]
        or previous != stamp(manifest[5])
        or sorted(identities.items())
        != db.execute(
            "SELECT ride,first_page FROM history_ids WHERE vehicle=? AND plan=? AND month=?"
            " ORDER BY ride",
            scope,
        ).fetchall()
        or db.execute(
            "SELECT 1 FROM history_ids i LEFT JOIN membership m ON m.vehicle=i.vehicle"
            " AND m.month=i.month AND m.ride=i.ride WHERE i.vehicle=? AND i.plan=? AND i.month=?"
            " AND m.ride IS NULL LIMIT 1",
            scope,
        ).fetchone()
    ):
        return False
    # No partial promotion: every raw page, indexed ID and monthly total is verified first.
    db.execute(
        "INSERT INTO history_pages SELECT vehicle,?,month,page,digest,body,observed,backend,"
        "endpoint,ids,total,stop"
        " FROM history_pages WHERE vehicle=? AND plan=? AND month=?",
        (expected.plan_id, *scope),
    )
    db.execute(
        "INSERT INTO history_ids SELECT vehicle,?,month,ride,first_page FROM history_ids"
        " WHERE vehicle=? AND plan=? AND month=?",
        (expected.plan_id, *scope),
    )
    db.execute(
        "INSERT INTO history_months SELECT vehicle,?,month,pages,known,total,verified,stop,observed"
        " FROM history_months WHERE vehicle=? AND plan=? AND month=?",
        (expected.plan_id, *scope),
    )
    following = expected.page_committed(now.isoformat(), finished=True, verified=True)
    put_root(db, following, expected)
    value = encoded({"source_plan": source.plan_id, "accepted_at": now.isoformat()})
    scope_key = key(vehicle, expected.plan_id, month)
    db.execute("INSERT INTO meta VALUES (?,?)", (scope_key, value))
    check(db, scope_key, value)
    from .payload_inventory import advance

    advance(db, vehicle)
    db.executemany(
        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (("preservation_cursor", vehicle), ("preservation_turn", "detail")),
    )
    return True
