"""Durable captured-scope local reconstruction, serialized by the archive actor.

One shared private recovery point per job; a checkpoint commits with each ride.
A completed scan is not a proof of all fields, references or cloud coverage.
"""

import re
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from secrets import token_hex

from .archive_codec import decoded, encoded, stamp
from .local_reconstruction import Reconstruction
from .payload_inventory import revision as raw_revision
from .travel import opaque_id

PREFIX = "local_reconstruction:"
CONTRACT = 1
MAX_INTEGER = 2**63 - 1
STATES = {
    "pending_backup",
    "running",
    "paused",
    "cancelled",
    "complete",
    "with_gaps",
    "blocked_recovery",
}


class RecoveryPointError(ValueError):
    """The private rollback object is unavailable; the main archive remains usable."""


@dataclass(frozen=True)
class ReconstructionJob:
    job_id: str
    upper: str | None
    total: int
    revision: int
    inventory: int
    started_at: str
    updated_at: str
    state: str = "pending_backup"
    after: str | None = None
    checked: int = 0
    repaired: int = 0
    blocked: int = 0
    corrupt: int = 0
    changed: bool = False
    backup_hash: str | None = None
    contract: int = CONTRACT

    @property
    def terminal(self) -> bool:
        return self.state in {"cancelled", "complete", "with_gaps"}


def scope(vehicle: str) -> str:
    if not re.fullmatch("[0-9a-f]{64}", vehicle):
        raise ValueError("Invalid local reconstruction scope")
    return PREFIX + vehicle


def restored(value: str) -> ReconstructionJob:
    data = decoded(value)
    if set(data) != set(ReconstructionJob.__dataclass_fields__):
        raise ValueError("Invalid local reconstruction fields")
    if not isinstance(data["job_id"], str) or not re.fullmatch("[0-9a-f]{32}", data["job_id"]):
        raise ValueError("Invalid local reconstruction identity")
    for field in ("upper", "after"):
        if data[field] is not None and opaque_id(data[field]) != data[field]:
            raise ValueError("Invalid local reconstruction cursor")
    for field in ("total", "revision", "inventory", "checked", "repaired", "blocked", "corrupt"):
        if type(data[field]) is not int or not 0 <= data[field] <= MAX_INTEGER:
            raise ValueError("Invalid local reconstruction count")
    if (
        type(data["changed"]) is not bool
        or type(data["contract"]) is not int
        or data["contract"] != CONTRACT
        or data["state"] not in STATES
        or data["repaired"] + data["blocked"] > data["checked"]
        or data["corrupt"] > data["blocked"]
        or (not data["changed"] and data["checked"] > data["total"])
        or (data["after"] is not None and (data["upper"] is None or data["after"] > data["upper"]))
        or stamp(data["updated_at"]) < stamp(data["started_at"])
    ):
        raise ValueError("Invalid local reconstruction checkpoint")
    digest = data["backup_hash"]
    if digest is not None and (
        not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)
    ):
        raise ValueError("Invalid recovery checksum")
    if data["state"] == "running" and digest is None:
        raise ValueError("Missing recovery checksum")
    return ReconstructionJob(**data)


def get(db: sqlite3.Connection, vehicle: str) -> ReconstructionJob | None:
    row = db.execute("SELECT value FROM meta WHERE key=?", (scope(vehicle),)).fetchone()
    return restored(row[0]) if row else None


def put(db: sqlite3.Connection, vehicle: str, job: ReconstructionJob) -> None:
    value = encoded(asdict(job))
    restored(value)
    db.execute(
        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (scope(vehicle), value),
    )


def fences(db: sqlite3.Connection) -> tuple[int, int]:
    return int(
        db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
    ), raw_revision(db)


def start(db: sqlite3.Connection, vehicle: str, now: datetime) -> ReconstructionJob:
    existing = get(db, vehicle)
    if existing is not None and not existing.terminal:
        return existing  # Coalesce repeated clicks and reloads, including paused work.
    total, upper = db.execute(
        "SELECT count(*),max(r.id) FROM rides r JOIN details d ON d.vehicle=r.vehicle"
        " AND d.ride=r.id WHERE r.vehicle=?",
        (vehicle,),
    ).fetchone()
    revision, inventory = fences(db)
    job = ReconstructionJob(
        token_hex(16), upper, total, revision, inventory, now.isoformat(), now.isoformat()
    )
    if total == 0:
        job = replace(job, state="complete")  # Empty local scope, not empty cloud history.
    put(db, vehicle, job)
    return job


def step(
    db: sqlite3.Connection,
    vehicle: str,
    now: datetime,
    evaluate: Callable[[str], Reconstruction],
) -> ReconstructionJob | None:
    job = get(db, vehicle)
    if job is None or job.state != "running":
        return job
    current = fences(db)
    job = replace(job, changed=job.changed or current != (job.revision, job.inventory))
    row = db.execute(
        "SELECT r.id FROM rides r JOIN details d ON d.vehicle=r.vehicle AND d.ride=r.id"
        " WHERE r.vehicle=? AND r.id<=? AND (? IS NULL OR r.id>?) ORDER BY r.id LIMIT 1",
        (vehicle, job.upper, job.after, job.after),
    ).fetchone()
    if row:
        result = evaluate(row[0])
        job = replace(
            job,
            after=row[0],
            checked=job.checked + 1,
            repaired=job.repaired + int(result.repaired),
            blocked=job.blocked + int(result.status not in {"healthy", "repairable"}),
            corrupt=job.corrupt + int(result.status == "corrupt"),
        )
    else:
        job = replace(job, state="with_gaps" if job.blocked or job.changed else "complete")
    revision, inventory = fences(db)
    job = replace(job, revision=revision, inventory=inventory, updated_at=now.isoformat())
    put(db, vehicle, job)
    db.execute(
        "INSERT INTO meta VALUES ('reconstruction_vehicle',?) ON CONFLICT(key)"
        " DO UPDATE SET value=excluded.value",
        (vehicle,),
    )
    return job
