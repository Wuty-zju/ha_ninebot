"""Bounded sparse month tasks; queue metadata is not the historical evidence archive."""

import json
import math
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from .archive_codec import encoded, stamp
from .parsing import previous_month

MAX_TASKS = 128
MAX_SELECTED_MONTHS = 12
STATES = {"pending", "querying", "waiting", "complete", "cancelled", "failed"}
ACTIVE = {"pending", "querying", "waiting"}
REASONS = {
    None,
    "connection",
    "service",
    "protocol",
    "busy",
    "auth",
    "closed",
    "unavailable",
    "storage",
}


class MonthTaskCapacity(Exception):
    """All bounded task slots are active; do not corrupt archive availability."""


@dataclass(frozen=True)
class MonthTask:
    month: str
    source: str
    revision: int
    state: str
    attempts: int
    created_at: str
    next_attempt: float
    finished_at: str | None = None
    reason: str | None = None
    list_complete: bool | None = None


def restored(value: str) -> MonthTask:
    data = json.loads(value)
    if not isinstance(data, dict) or set(data) != set(MonthTask.__dataclass_fields__):
        raise ValueError("Invalid month task")
    previous_month(data["month"])
    stamp(data["created_at"])
    if data["finished_at"] is not None:
        if stamp(data["finished_at"]) < stamp(data["created_at"]):
            raise ValueError("Invalid task receipt")
    if (
        data["source"] not in {"manual", "recent"}
        or data["state"] not in STATES
        or type(data["revision"]) is not int
        or not 1 <= data["revision"] < 2**63
        or type(data["attempts"]) is not int
        or not 0 <= data["attempts"] <= 255
        or type(data["next_attempt"]) not in (int, float)
        or not math.isfinite(data["next_attempt"])
        or not 0 <= data["next_attempt"] <= 4102444800
        or data["reason"] not in REASONS
        or data["list_complete"] is not None
        and type(data["list_complete"]) is not bool
        or data["state"] in {"pending", "querying", "complete", "cancelled"}
        and data["reason"] is not None
        or data["state"] in {"waiting", "failed"}
        and data["reason"] is None
        or data["state"] == "complete"
        and data["finished_at"] is None
    ):
        raise ValueError("Invalid month task fields")
    return MonthTask(**data)


def task_key(vehicle: str, month: str) -> str:
    previous_month(month)
    if len(vehicle) != 64 or any(c not in "0123456789abcdef" for c in vehicle):
        raise ValueError("Invalid task scope")
    return f"month_task:{vehicle}:{month}"


def validate_key(key: str, value: str) -> None:
    _, vehicle, month = key.split(":")
    if key != task_key(vehicle, month) or restored(value).month != month:
        raise ValueError("Invalid month task key")


def put(db: sqlite3.Connection, vehicle: str, task: MonthTask) -> None:
    value = encoded(asdict(task))
    restored(value)
    db.execute(
        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (task_key(vehicle, task.month), value),
    )


def rows(db: sqlite3.Connection, vehicle: str) -> tuple[MonthTask, ...]:
    return tuple(
        restored(value)
        for (value,) in db.execute(
            "SELECT value FROM meta WHERE key LIKE ? ORDER BY key", (f"month_task:{vehicle}:%",)
        )
    )


def make_room(db: sqlite3.Connection) -> None:
    tasks = [
        (key, restored(value))
        for key, value in db.execute("SELECT key,value FROM meta WHERE key LIKE 'month_task:%'")
    ]
    if len(tasks) < MAX_TASKS:
        return
    retired = sorted((task.created_at, key) for key, task in tasks if task.state not in ACTIVE)
    if not retired:
        raise MonthTaskCapacity
    db.execute("DELETE FROM meta WHERE key=?", (retired[0][1],))


def schedule(
    db: sqlite3.Connection,
    vehicle: str,
    months: tuple[str, ...],
    now: datetime,
    *,
    source: str = "manual",
) -> None:
    if not 1 <= len(months) <= MAX_SELECTED_MONTHS or len(set(months)) != len(months):
        raise ValueError("Invalid sparse month selection")
    with db:
        for month in months:
            task_key(vehicle, month)
            found = db.execute(
                "SELECT value FROM meta WHERE key=?", (task_key(vehicle, month),)
            ).fetchone()
            old = restored(found[0]) if found else None
            if old and old.state in ACTIVE:
                continue  # Repeated enqueue cannot overwrite an in-flight claim.
            if source == "recent" and old:
                if old.state in {"cancelled", "failed"} or (
                    old.finished_at and now < stamp(old.finished_at) + timedelta(days=1)
                ):
                    continue
            if old is None:
                make_room(db)  # Retire task metadata only, never rides/payloads.
            put(
                db,
                vehicle,
                MonthTask(
                    month,
                    source,
                    old.revision + 1 if old else 1,
                    "pending",
                    0,
                    now.isoformat(),
                    now.timestamp(),
                ),
            )


def claim(
    db: sqlite3.Connection,
    vehicle: str,
    now: datetime,
    reserve: Callable[[], bool],
    months: tuple[str, ...] | None = None,
) -> MonthTask | None:
    with db:
        eligible = sorted(
            (
                task
                for task in rows(db, vehicle)
                if task.state in ACTIVE
                and task.next_attempt <= now.timestamp()
                and (months is None or task.month in months)
            ),
            key=lambda task: (task.source != "recent", task.created_at, task.month),
        )
        if not eligible or not reserve():
            return None
        task = replace(
            eligible[0],
            state="querying",
            revision=eligible[0].revision + 1,
            attempts=min(255, eligible[0].attempts + 1),
            reason=None,
            next_attempt=now.timestamp() + 120,
        )
        put(db, vehicle, task)
        return task


def finish(
    db: sqlite3.Connection,
    vehicle: str,
    expected: MonthTask,
    now: datetime,
    *,
    success: bool,
    complete: bool | None = None,
    reason: str | None = None,
    delay: float = 0,
) -> bool:
    if not math.isfinite(delay) or delay < 0:
        raise ValueError("Invalid month retry delay")
    with db:
        current = db.execute(
            "SELECT value FROM meta WHERE key=?", (task_key(vehicle, expected.month),)
        ).fetchone()
        if current is None or restored(current[0]) != expected:
            return False
        state = "complete" if success else "failed" if expected.attempts >= 8 else "waiting"
        task = replace(
            expected,
            revision=expected.revision + 1,
            state=state,
            next_attempt=min(4102444800, now.timestamp() + delay),
            finished_at=now.isoformat() if success else None,
            reason=None if success else reason,
            list_complete=complete if success else None,
        )
        put(db, vehicle, task)
        return True


def cancel(db: sqlite3.Connection, vehicle: str, months: tuple[str, ...]) -> None:
    with db:
        for task in rows(db, vehicle):
            if task.month in months and task.state in ACTIVE:
                put(
                    db,
                    vehicle,
                    replace(task, revision=task.revision + 1, state="cancelled", reason=None),
                )


def progress(tasks: tuple[MonthTask, ...], now: datetime) -> list[dict[str, Any]]:
    return [
        {
            "month": task.month,
            "source": task.source,
            "state": task.state,
            "revision": task.revision,
            "attempts": task.attempts,
            "reason": task.reason,
            "retry_after_s": max(0, int(task.next_attempt - now.timestamp()))
            if task.state in ACTIVE
            else None,
            "last_success": task.finished_at,
            "list_complete": task.list_complete,
            "upstream_pagination_verified": False,
        }
        for task in tasks
    ]
