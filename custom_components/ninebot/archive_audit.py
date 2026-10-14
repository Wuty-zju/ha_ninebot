"""Resumable local payload checks on the existing SQL actor; never network I/O.

The captured row-ID high watermarks bound a scan while history keeps arriving.
A completed payload scan does not prove normalized indexes or cloud completeness.
"""

import hashlib
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import datetime

from .archive_codec import decoded, encoded, stamp
from .detail_codec import CorruptPayload, unpacked
from .history_store import page

PREFIX = "history_audit:"
FAULT_PREFIX = "history_audit_fault:"
MAX_INTEGER = 2**63 - 1


def scope(vehicle: str) -> str:
    if len(vehicle) != 64 or any(c not in "0123456789abcdef" for c in vehicle):
        raise ValueError("Invalid audit scope")
    return vehicle


@dataclass(frozen=True)
class AuditPlan:
    version: int
    detail_upper: int
    page_upper: int
    total: int
    cutoff_revision: int
    started_at: str
    updated_at: str
    phase: str = "detail"
    after: int = 0
    checked: int = 0
    invalid: int = 0
    changed: bool = False

    @property
    def complete(self) -> bool:
        return self.phase == "complete"

    @property
    def payloads_verified(self) -> bool:
        return self.complete and self.total > 0 and not self.changed and self.invalid == 0


def restored(value: str) -> AuditPlan:
    data = decoded(value)
    if set(data) != set(AuditPlan.__dataclass_fields__):
        raise ValueError("Invalid audit fields")
    for key in (
        "detail_upper",
        "page_upper",
        "total",
        "cutoff_revision",
        "after",
        "checked",
        "invalid",
    ):
        if type(data[key]) is not int or not 0 <= data[key] <= MAX_INTEGER:
            raise ValueError("Invalid audit count")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("Unsupported audit contract")
    if data["phase"] not in {"detail", "page", "complete"}:
        raise ValueError("Invalid audit phase")
    if type(data["changed"]) is not bool or not 0 <= data["invalid"] <= data["checked"]:
        raise ValueError("Invalid audit coverage")
    if stamp(data["updated_at"]) < stamp(data["started_at"]):
        raise ValueError("Invalid audit times")
    upper = data["detail_upper"] if data["phase"] == "detail" else data["page_upper"]
    if data["after"] > upper or (not data["changed"] and data["checked"] > data["total"]):
        raise ValueError("Invalid audit cursor")
    if data["phase"] == "complete" and (
        data["after"] != 0 or (not data["changed"] and data["checked"] != data["total"])
    ):
        raise ValueError("Incomplete audit cannot be verified")
    return AuditPlan(**data)


def get(db: sqlite3.Connection, vehicle: str) -> AuditPlan | None:
    row = db.execute("SELECT value FROM meta WHERE key=?", (PREFIX + scope(vehicle),)).fetchone()
    return restored(row[0]) if row else None


def put(db: sqlite3.Connection, vehicle: str, plan: AuditPlan) -> None:
    value = encoded(asdict(plan))
    restored(value)
    db.execute(
        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (PREFIX + scope(vehicle), value),
    )


def start(
    db: sqlite3.Connection, vehicle: str, now: datetime, *, restart: bool = False
) -> AuditPlan:
    existing = get(db, vehicle)
    if existing is not None and not existing.complete:
        return existing  # Repeated requests and restarts coalesce rather than reset the scan.
    if existing is not None and not restart:
        return existing
    detail_upper, detail_count = db.execute(
        "SELECT coalesce(max(rowid),0),count(*) FROM detail_payloads WHERE vehicle=?", (vehicle,)
    ).fetchone()
    page_upper, page_count = db.execute(
        "SELECT coalesce(max(rowid),0),count(*) FROM history_pages WHERE vehicle=?", (vehicle,)
    ).fetchone()
    revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
    plan = AuditPlan(
        1,
        detail_upper,
        page_upper,
        detail_count + page_count,
        revision,
        now.isoformat(),
        now.isoformat(),
    )
    put(db, vehicle, plan)
    return plan


def fault_key(vehicle: str, kind: str, rowid: int, token: str) -> str:
    scope(vehicle)
    if kind not in {"detail", "page"} or type(rowid) is not int or not 1 <= rowid <= MAX_INTEGER:
        raise ValueError("Invalid audit object scope")
    if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        raise ValueError("Invalid audit object token")
    return f"{FAULT_PREFIX}{vehicle}:{kind}:{rowid}:{token}"


def validate_meta(key: str, value: str) -> None:
    if key.startswith(PREFIX):
        scope(key.removeprefix(PREFIX))
        restored(value)
    elif key.startswith(FAULT_PREFIX):
        _, vehicle, kind, ordinal, token = key.split(":")
        if key != fault_key(vehicle, kind, int(ordinal), token) or value not in {
            "payload",
            "metadata",
        }:
            raise ValueError("Invalid audit fault")


def step(db: sqlite3.Connection, vehicle: str, now: datetime, limit: int) -> AuditPlan | None:
    if type(limit) is not int or not 1 <= limit <= 8:
        raise ValueError("Invalid audit batch")
    plan = get(db, vehicle)
    if plan is None or plan.complete:
        return plan
    if stamp(plan.updated_at) > now:
        return plan  # A backwards wall-clock adjustment is not database corruption.
    revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
    plan = replace(plan, changed=plan.changed or revision != plan.cutoff_revision)
    remaining = limit
    while remaining and not plan.complete:
        if plan.phase == "detail":
            rows = db.execute(
                "SELECT rowid,hash,body,observed,month,backend,endpoint FROM detail_payloads"
                " WHERE vehicle=? AND rowid>? AND rowid<=? ORDER BY rowid LIMIT ?",
                (vehicle, plan.after, plan.detail_upper, remaining),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT rowid,plan,month,page FROM history_pages WHERE vehicle=?"
                " AND rowid>? AND rowid<=? ORDER BY rowid LIMIT ?",
                (vehicle, plan.after, plan.page_upper, remaining),
            ).fetchall()
        if not rows:
            plan = replace(plan, phase="page" if plan.phase == "detail" else "complete", after=0)
            continue
        for row in rows:
            token = hashlib.sha256(str(row[1]).encode()).hexdigest()
            key = fault_key(vehicle, plan.phase, row[0], token)
            reason = None
            try:
                if plan.phase == "detail":
                    unpacked(row[2], row[1], stamp(row[3]), row[4], row[5], row[6])
                elif page(db, vehicle, row[1], row[2], row[3]) is None:
                    raise ValueError("Missing audit page")
            except CorruptPayload:
                reason = "payload"
            except (ValueError, TypeError, KeyError, OverflowError):
                reason = "metadata"
            if reason:
                db.execute(
                    "INSERT INTO meta VALUES (?,?) ON CONFLICT(key)"
                    " DO UPDATE SET value=excluded.value",
                    (key, reason),
                )
            else:
                db.execute(
                    "DELETE FROM meta WHERE key=?", (key,)
                )  # Derived fault flag, never original evidence.
            plan = replace(
                plan,
                after=row[0],
                checked=plan.checked + 1,
                invalid=plan.invalid + int(reason is not None),
            )
            remaining -= 1
        upper = plan.detail_upper if plan.phase == "detail" else plan.page_upper
        if plan.after == upper:
            plan = replace(plan, phase="page" if plan.phase == "detail" else "complete", after=0)
    plan = replace(
        plan,
        updated_at=now.isoformat(),
        changed=plan.changed
        or plan.checked > plan.total
        or (plan.complete and plan.checked != plan.total),
    )
    put(db, vehicle, plan)
    return plan
