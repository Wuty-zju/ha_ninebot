"""Resumable local payload checks on the existing SQL actor; never network I/O.

The captured row-ID high watermarks bound a scan while history keeps arriving.
A completed payload scan does not prove normalized indexes or cloud completeness.
"""

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime

from . import page_reference_audit as page_references
from .archive_codec import decoded, encoded, stamp
from .const import MAX_RESPONSE_BYTES
from .object_checks import ObjectSummary, StoredObject, sources, summary, verify
from .payload_inventory import revision as inventory_revision
from .raw import RawRecord

PREFIX = "history_audit:"
FAULT_PREFIX = "history_audit_fault:"
MAX_INTEGER = 2**63 - 1
MAX_COMPRESSED_OBJECT = MAX_RESPONSE_BYTES + 1024
MAX_BATCH_COMPRESSED = 4 * MAX_COMPRESSED_OBJECT
MAX_BATCH_DECODED = 4 * MAX_RESPONSE_BYTES
MAX_BATCH_SECONDS = 0.05


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
    cutoff_inventory: int | None = None
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
        return (
            self.complete
            and self.total > 0
            and self.cutoff_inventory is not None
            and not self.changed
            and self.invalid == 0
        )


def restored(value: str) -> AuditPlan:
    data = decoded(value)
    if type(data.get("changed")) is not bool:
        raise ValueError("Invalid audit change flag")
    if set(data) == set(AuditPlan.__dataclass_fields__) - {"cutoff_inventory"}:
        data["cutoff_inventory"] = None
        data["changed"] = True  # Legacy scans cannot certify the managed raw inventory.
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
    if data["cutoff_inventory"] is not None and (
        type(data["cutoff_inventory"]) is not int
        or not 0 <= data["cutoff_inventory"] <= MAX_INTEGER
    ):
        raise ValueError("Invalid audit inventory revision")
    if data["version"] == 1 and data["cutoff_inventory"] is None:
        data["changed"] = True
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
        cutoff_inventory=inventory_revision(db),
    )
    put(db, vehicle, plan)
    page_references.start(db, vehicle, plan, page_count)
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
    plan = replace(
        plan,
        changed=plan.changed
        or revision != plan.cutoff_revision
        or inventory_revision(db) != plan.cutoff_inventory,
    )
    reference_scan = page_references.get(db, vehicle)
    check_pages = reference_scan is not None and page_references.matches(reference_scan, plan)
    remaining = limit
    compressed = decoded_bytes = 0
    deadline = time.monotonic() + MAX_BATCH_SECONDS
    exhausted = False
    while remaining and not plan.complete and not exhausted:
        rows = sources(
            db,
            vehicle,
            "detail" if plan.phase == "detail" else "page",
            after=plan.after,
            upper=plan.detail_upper if plan.phase == "detail" else plan.page_upper,
            limit=remaining,
        )
        if not rows:
            plan = replace(plan, phase="page" if plan.phase == "detail" else "complete", after=0)
            continue
        for row in rows:
            size = row.size
            # Reserve the maximum decoded size before opening a blob. SQLite
            # length() metadata does not materialize all candidate bodies.
            charge = size if isinstance(size, int) and 0 <= size <= MAX_COMPRESSED_OBJECT else 0
            if limit != remaining and (
                time.monotonic() >= deadline
                or compressed + charge > MAX_BATCH_COMPRESSED
                or decoded_bytes + MAX_RESPONSE_BYTES > MAX_BATCH_DECODED
            ):
                exhausted = True
                break
            compressed += charge
            decoded_bytes += MAX_RESPONSE_BYTES
            # Preserve existing legacy fault keys; integrity reuse uses logical identities.
            legacy = json.loads(row.identity)
            token = hashlib.sha256(
                str(row.digest if row.kind == "detail" else legacy[0]).encode()
            ).hexdigest()
            key = fault_key(vehicle, plan.phase, row.ordinal, token)
            if row.kind == "page" and check_pages:
                outcomes: list[str] = []

                def check_page(
                    record: RawRecord,
                    obj: StoredObject = row,
                    results: list[str] = outcomes,
                ) -> None:
                    results.append(page_references.assess(db, obj, record))

                reason = verify(db, row, now, on_page=check_page)
                page_references.observe(
                    db, vehicle, plan, row, outcomes[0] if outcomes else "unverifiable", now
                )
            else:
                reason = verify(db, row, now)
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
                after=row.ordinal,
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


def delta_step(db: sqlite3.Connection, vehicle: str, now: datetime, limit: int) -> ObjectSummary:
    """Only unmatched identity/digest/metadata/contract receipts, no old-body decode."""
    if type(limit) is not int or not 1 <= limit <= 8:
        raise ValueError("Invalid delta batch")
    deadline = time.monotonic() + MAX_BATCH_SECONDS
    processed = 0
    compressed = decoded_bytes = 0
    for kind in ("detail", "page"):
        for obj in sources(db, vehicle, kind, limit=limit - processed, pending=True):
            charge = (
                obj.size
                if isinstance(obj.size, int) and 0 <= obj.size <= MAX_COMPRESSED_OBJECT
                else 0
            )
            if processed and (
                time.monotonic() >= deadline
                or compressed + charge > MAX_BATCH_COMPRESSED
                or decoded_bytes + MAX_RESPONSE_BYTES > MAX_BATCH_DECODED
            ):
                return summary(db, vehicle)
            verify(db, obj, now)
            compressed += charge
            decoded_bytes += MAX_RESPONSE_BYTES
            processed += 1
            if processed == limit:
                return summary(db, vehicle)
    return summary(db, vehicle)
