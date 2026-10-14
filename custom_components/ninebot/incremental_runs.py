"""Independent single-month runs; never reset the first-history baseline."""

import secrets
import sqlite3
from datetime import datetime

from .archive_codec import decoded, encoded
from .history_plan import HistoryPlan, HistoryState, plan_data, restored
from .parsing import previous_month

PREFIX = "history_run:"
TERMINAL = {HistoryState.COMPLETE, HistoryState.WITH_GAPS, HistoryState.RECONCILED}


class RunCapacity(Exception):
    """Too many active months; keep existing work and evidence intact."""


def run_key(vehicle: str, identity: str) -> str:
    if (
        len(vehicle) != 64
        or len(identity) != 32
        or any(c not in "0123456789abcdef" for c in vehicle + identity)
    ):
        raise ValueError("Invalid history run scope")
    return f"{PREFIX}{vehicle}:{identity}"


def get(db: sqlite3.Connection, vehicle: str, identity: str) -> HistoryPlan | None:
    found = db.execute(
        "SELECT value FROM meta WHERE key=?", (run_key(vehicle, identity),)
    ).fetchone()
    return restored(decoded(found[0])) if found else None


def put(db: sqlite3.Connection, plan: HistoryPlan) -> None:
    if plan.start_month != plan.end_month:
        raise ValueError("Incremental run must be one month")
    db.execute(
        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (run_key(plan.vehicle, plan.plan_id), encoded(plan_data(plan))),
    )


def schedule(db: sqlite3.Connection, vehicle: str, month: str, now: datetime) -> HistoryPlan:
    previous_month(month)
    active_key = f"history_run_active:{vehicle}:{month}"
    row = db.execute("SELECT value FROM meta WHERE key=?", (active_key,)).fetchone()
    if row:
        old = get(db, vehicle, row[0])
        if old is None:
            raise ValueError("Missing incremental run")
        if old.state not in TERMINAL:
            return old  # Automatic/manual requests coalesce, including retry/paused work.
    if len(active(db, vehicle)) >= 12:
        raise RunCapacity
    plan = HistoryPlan.create(secrets.token_hex(16), vehicle, month, month, now.isoformat())
    put(db, plan)
    db.execute(
        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (active_key, plan.plan_id),
    )
    return plan


def validate_key(key: str, value: str) -> None:
    _, vehicle, identity = key.split(":", 2)
    plan = restored(decoded(value))
    if (
        key != run_key(vehicle, identity)
        or plan.vehicle != vehicle
        or plan.plan_id != identity
        or plan.start_month != plan.end_month
    ):
        raise ValueError("Invalid incremental run metadata")


def validate_active(db: sqlite3.Connection, key: str, value: str) -> None:
    _, vehicle, month = key.split(":", 2)
    previous_month(month)
    plan = get(db, vehicle, value)
    if plan is None or plan.start_month != month or plan.end_month != month:
        raise ValueError("Invalid incremental run pointer")


def active(db: sqlite3.Connection, vehicle: str) -> tuple[HistoryPlan, ...]:
    values = db.execute(
        "SELECT r.value FROM meta a JOIN meta r ON r.key='history_run:'||?||':'||a.value"
        " WHERE a.key LIKE ? AND json_extract(r.value,'$.state') NOT IN"
        " ('complete','completed_with_gaps','locally_reconciled')"
        " ORDER BY json_extract(r.value,'$.created_at') LIMIT 12",
        (vehicle, f"history_run_active:{vehicle}:%"),
    )
    return tuple(restored(decoded(value)) for (value,) in values)
