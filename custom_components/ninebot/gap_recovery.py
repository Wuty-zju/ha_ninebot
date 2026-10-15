"""Re-enumerate only unverified baseline months, keeping original evidence intact.

Managed completion receipts supply a current coverage overlay. They do not
rewrite the original root/manifests or certify raw bytes/cloud completeness.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from .archive_codec import decoded, encoded, stamp
from .history_plan import MAX_MONTHS, HistoryPlan
from .incremental_runs import get, run_key, schedule
from .parsing import previous_month

PREFIX = "history_gap:"
CURSOR = "history_gap_cursor:"


@dataclass(frozen=True)
class GapCoverage:
    initial: int
    resolved: int
    pending: int
    next_retry: datetime | None = None
    basis: str = "managed_complete_month_runs_v1"


def key(vehicle: str, baseline: str, month: str) -> str:
    run_key(vehicle, baseline)
    previous_month(month)
    return f"{PREFIX}{vehicle}:{baseline}:{month}"


def restored(value: str) -> dict:
    data = decoded(value)
    if (
        set(data) != {"contract", "run", "attempts", "not_before"}
        or type(data["contract"]) is not int
        or data["contract"] != 1
    ):
        raise ValueError("Invalid history gap contract")
    run_key("0" * 64, data["run"])
    if type(data["attempts"]) is not int or not 1 <= data["attempts"] <= 2**31 - 1:
        raise ValueError("Invalid history gap attempts")
    stamp(data["not_before"])
    return data


def rows(db: sqlite3.Connection, vehicle: str, baseline: str) -> list[tuple]:
    return db.execute(
        "SELECT m.month,m.observed,g.value FROM history_months m LEFT JOIN meta g"
        " ON g.key='history_gap:'||m.vehicle||':'||m.plan||':'||m.month"
        " WHERE m.vehicle=? AND m.plan=? AND m.verified=0 ORDER BY m.month DESC LIMIT ?",
        (vehicle, baseline, MAX_MONTHS),
    ).fetchall()


def completed(
    db: sqlite3.Connection, root: HistoryPlan, month: str, observed: str, state: dict
) -> str | None:
    run = get(db, root.vehicle, state["run"])
    if (
        run is None
        or run.vehicle != root.vehicle
        or run.plan_id == root.plan_id
        or run.start_month != month
        or run.end_month != month
        or run.provider != root.provider
        or not run.discovery_complete
        or run.gap_months
        or run.verified_months != 1
    ):
        return None
    scope = (root.vehicle, run.plan_id, month)
    manifest = db.execute(
        "SELECT pages,known,total,verified,stop,observed FROM history_months"
        " WHERE vehicle=? AND plan=? AND month=?",
        scope,
    ).fetchone()
    if (
        manifest is None
        or manifest[3:5] != (1, None)
        or manifest[1] != manifest[2]
        or stamp(manifest[5]) < stamp(observed)
    ):
        return None
    pages, low, high, bad = db.execute(
        "SELECT count(*),min(p.page),max(p.page),coalesce(sum(p.backend IS NOT '0.1.7'"
        " OR p.endpoint!=? OR p.stop IS NOT NULL OR p.total IS NOT ?"
        " OR NOT(length(p.body)>0 OR h.digest IS NOT NULL)),0)"
        " FROM history_pages p LEFT JOIN history_payloads h ON h.digest=p.digest"
        " WHERE p.vehicle=? AND p.plan=? AND p.month=?",
        (root.provider, manifest[2], *scope),
    ).fetchone()
    ids = db.execute(
        "SELECT count(*) FROM history_ids WHERE vehicle=? AND plan=? AND month=?",
        scope,
    ).fetchone()[0]
    if not pages == high == manifest[0] or low != 1 or bad or ids != manifest[1]:
        return None
    return run.plan_id


def coverage(
    db: sqlite3.Connection, vehicle: str, baseline: str
) -> tuple[GapCoverage, tuple[str, ...]]:
    from .history_store import root as get_root

    root = get_root(db, vehicle)
    if root is None or root.plan_id != baseline:
        return GapCoverage(0, 0, 0), ()
    resolved = []
    retries = []
    gaps = rows(db, vehicle, baseline)
    if len(gaps) != root.gap_months:
        count = max(len(gaps), root.gap_months)
        return GapCoverage(count, 0, count), ()
    for month, observed, value in gaps:
        if value is None:
            continue
        state = restored(value)
        matched = completed(db, root, month, observed, state)
        if matched:
            resolved.append(matched)
        else:
            retries.append(stamp(state["not_before"]))
    return (
        GapCoverage(
            len(gaps), len(resolved), len(gaps) - len(resolved), min(retries) if retries else None
        ),
        tuple(resolved),
    )


def seed(
    db: sqlite3.Connection, vehicle: str, now: datetime, *, only_month: str | None = None
) -> HistoryPlan | None:
    from .history_store import root as get_root

    root = get_root(db, vehicle)
    if root is None:
        return None
    gaps = rows(db, vehicle, root.plan_id)
    cursor_key = f"{CURSOR}{vehicle}:{root.plan_id}"
    cursor = db.execute("SELECT value FROM meta WHERE key=?", (cursor_key,)).fetchone()
    if cursor:
        for i, (month, _, _) in enumerate(gaps):
            if month == cursor[0]:
                gaps = gaps[i + 1 :] + gaps[: i + 1]
                break
    for month, observed, value in gaps:
        if only_month is not None and month != only_month:
            continue
        if stamp(observed) > now:
            continue
        state = restored(value) if value else None
        if state:
            if completed(db, root, month, observed, state) or stamp(state["not_before"]) > now:
                continue
            old = get(db, vehicle, state["run"])
            if old and (not old.discovery_complete or old.gap_months == 0):
                # Existing paging resumes. An already verified chain with broken
                # local links requires local repair, not a cloud retry to hide it.
                continue
        attempts = state["attempts"] + 1 if state else 1
        if attempts > 2**31 - 1:
            continue
        pointer = db.execute(
            "SELECT value FROM meta WHERE key=?", (f"history_run_active:{vehicle}:{month}",)
        ).fetchone()
        existing = get(db, vehicle, pointer[0]) if pointer else None
        proven = (
            completed(db, root, month, observed, {"run": existing.plan_id}) if existing else None
        )
        run = existing if proven else schedule(db, vehicle, month, now, retry_discovery=True)
        assert run is not None
        delay = min(7 * 86400, 6 * 3600 * 2 ** min(attempts - 1, 5))
        data = {
            "contract": 1,
            "run": run.plan_id,
            "attempts": attempts,
            "not_before": (now + timedelta(seconds=delay)).isoformat(),
        }
        db.execute(
            "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key(vehicle, root.plan_id, month), encoded(data)),
        )
        db.execute(
            "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (cursor_key, month),
        )
        return run
    return None


def link_completed(db: sqlite3.Connection, run: HistoryPlan, month: str, now: datetime) -> None:
    """Bind a real manual/automatic completed page chain in its commit transaction."""
    from .history_store import root as get_root

    baseline = get_root(db, run.vehicle)
    if baseline is None or run.plan_id == baseline.plan_id:
        return
    original = db.execute(
        "SELECT observed FROM history_months WHERE vehicle=? AND plan=? AND month=? AND verified=0",
        (run.vehicle, baseline.plan_id, month),
    ).fetchone()
    if (
        original is None
        or completed(db, baseline, month, original[0], {"run": run.plan_id}) is None
    ):
        return
    name = key(run.vehicle, baseline.plan_id, month)
    old = db.execute("SELECT value FROM meta WHERE key=?", (name,)).fetchone()
    state = restored(old[0]) if old else None
    if state is not None and state["run"] == run.plan_id:
        return
    attempts = min(2**31 - 1, state["attempts"] + 1 if state else 1)
    data = {
        "contract": 1,
        "run": run.plan_id,
        "attempts": attempts,
        "not_before": (now + timedelta(hours=6)).isoformat(),
    }
    db.execute(
        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (name, encoded(data)),
    )


def unresolved(db: sqlite3.Connection, vehicle: str, month: str) -> bool:
    from .history_store import root as get_root

    root = get_root(db, vehicle)
    if root is None:
        return False
    row = db.execute(
        "SELECT observed FROM history_months WHERE vehicle=? AND plan=? AND month=? AND verified=0",
        (vehicle, root.plan_id, month),
    ).fetchone()
    if row is None:
        return False
    linked = db.execute(
        "SELECT value FROM meta WHERE key=?", (key(vehicle, root.plan_id, month),)
    ).fetchone()
    return linked is None or completed(db, root, month, row[0], restored(linked[0])) is None


def validate(db: sqlite3.Connection, name: str, value: str) -> None:
    from .history_store import root as get_root

    _, vehicle, baseline, *tail = name.split(":")
    run_key(vehicle, baseline)
    root = get_root(db, vehicle)
    if root is None or root.plan_id != baseline:
        raise ValueError("Invalid history gap baseline")
    if name.startswith(CURSOR):
        if tail:
            raise ValueError("Invalid history gap cursor scope")
        month = value
    else:
        if len(tail) != 1 or key(vehicle, baseline, tail[0]) != name:
            raise ValueError("Invalid history gap scope")
        month = tail[0]
        state = restored(value)
        run = get(db, vehicle, state["run"])
        if (
            run is None
            or run.start_month != month
            or run.end_month != month
            or run.provider != root.provider
        ):
            raise ValueError("Invalid history gap run")
    previous_month(month)
    if (
        db.execute(
            "SELECT 1 FROM history_months WHERE vehicle=? AND plan=? AND month=? AND verified=0",
            (vehicle, baseline, month),
        ).fetchone()
        is None
    ):
        raise ValueError("Missing original history gap")
