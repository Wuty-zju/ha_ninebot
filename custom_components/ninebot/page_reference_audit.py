"""Separate captured-page index checks, reusing the raw audit's decoded object.

No raw receipt is upgraded into a full reference/normalized/cloud certificate.
The compact checkpoint is derived metadata; original bytes/indexes are never edited.
"""

import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING

from .archive_codec import MAX_ROW_BYTES, decoded, encoded, stamp
from .history_plan import PROVIDERS
from .history_store import _shape
from .raw import Endpoint, RawRecord
from .travel import MAX_RIDES

if TYPE_CHECKING:
    from .archive_audit import AuditPlan
    from .object_checks import StoredObject

PREFIX = "page_reference_audit:"
MAX_INTEGER = 2**63 - 1


@dataclass(frozen=True)
class PageReferenceScan:
    contract: int
    audit_started: str
    cutoff_revision: int
    cutoff_inventory: int | None
    page_upper: int
    total: int
    updated_at: str
    checked: int = 0
    conflicts: int = 0
    unverifiable: int = 0
    after: int = 0


@dataclass(frozen=True)
class PageReferenceSummary:
    total: int
    checked: int
    conflicts: int
    unverifiable: int
    complete: bool
    scope_unchanged: bool
    captured_links_verified: bool
    observed_at: str


def key(vehicle: str) -> str:
    if len(vehicle) != 64 or any(c not in "0123456789abcdef" for c in vehicle):
        raise ValueError("Invalid page reference scope")
    return PREFIX + vehicle


def restored(value: str) -> PageReferenceScan:
    data = decoded(value)
    if (
        set(data) != set(PageReferenceScan.__dataclass_fields__)
        or type(data["contract"]) is not int
        or data["contract"] != 1
    ):
        raise ValueError("Invalid page reference contract")
    for name in (
        "cutoff_revision",
        "page_upper",
        "total",
        "checked",
        "conflicts",
        "unverifiable",
        "after",
    ):
        if type(data[name]) is not int or not 0 <= data[name] <= MAX_INTEGER:
            raise ValueError("Invalid page reference count")
    inventory = data["cutoff_inventory"]
    if inventory is not None and (type(inventory) is not int or not 0 <= inventory <= MAX_INTEGER):
        raise ValueError("Invalid page reference inventory")
    if (
        data["conflicts"] + data["unverifiable"] > data["checked"]
        or data["after"] > data["page_upper"]
    ):
        raise ValueError("Invalid page reference progress")
    if data["checked"] > data["after"] or ((data["checked"] == 0) != (data["after"] == 0)):
        raise ValueError("Invalid page reference cursor")
    if stamp(data["updated_at"]) < stamp(data["audit_started"]):
        raise ValueError("Invalid page reference time")
    return PageReferenceScan(**data)


def get(db: sqlite3.Connection, vehicle: str) -> PageReferenceScan | None:
    row = db.execute("SELECT value FROM meta WHERE key=?", (key(vehicle),)).fetchone()
    return restored(row[0]) if row else None


def put(db: sqlite3.Connection, vehicle: str, scan: PageReferenceScan) -> None:
    value = encoded(asdict(scan))
    restored(value)
    db.execute(
        "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key(vehicle), value),
    )


def matches(scan: PageReferenceScan, plan: "AuditPlan") -> bool:
    return (scan.audit_started, scan.cutoff_revision, scan.cutoff_inventory, scan.page_upper) == (
        plan.started_at,
        plan.cutoff_revision,
        plan.cutoff_inventory,
        plan.page_upper,
    )


def start(db: sqlite3.Connection, vehicle: str, plan: "AuditPlan", page_count: int) -> None:
    put(
        db,
        vehicle,
        PageReferenceScan(
            1,
            plan.started_at,
            plan.cutoff_revision,
            plan.cutoff_inventory,
            plan.page_upper,
            page_count,
            plan.updated_at,
        ),
    )


def assess(db: sqlite3.Connection, obj: "StoredObject", record: RawRecord) -> str:
    """Per-page conjunction proves the captured ID index, not monthly parent data."""
    from .object_checks import identity

    if record.backend_version != "0.1.7" or record.endpoint_version not in PROVIDERS:
        return "unverifiable"
    plan, month, page = identity("page", obj.identity)
    if record.endpoint is not Endpoint.TRAVEL or record.query_month != month:
        return "unverifiable"
    body = record.payload()
    if not isinstance(body, dict):
        return "unverifiable"
    ids, total, stop = _shape(body, month=month)
    if stop is not None:
        return "unverifiable"  # Preserve unknown upstream schemas as raw evidence.
    row = db.execute(
        "SELECT CASE WHEN length(CAST(ids AS BLOB))<=? THEN ids END,total FROM history_pages"
        " WHERE vehicle=? AND plan=? AND month=? AND page=?",
        (MAX_ROW_BYTES, obj.vehicle, plan, month, page),
    ).fetchone()
    if row is None:
        return "conflict"
    if row[0] is None:
        return "unverifiable"
    if not isinstance(row[0], str):
        return "conflict"
    try:
        header = decoded(row[0])
    except ValueError:
        return "conflict"
    if header != {"ids": list(ids)} or row[1] != total:
        return "conflict"
    scope = (obj.vehicle, plan, month)
    assigned = db.execute(
        "SELECT ride FROM history_ids WHERE vehicle=? AND plan=? AND month=?"
        " AND first_page=? LIMIT ?",
        (*scope, page, MAX_RIDES + 1),
    ).fetchall()
    wanted = set(ids)
    if any(identity[0] not in wanted for identity in assigned):
        return "conflict"
    if not ids:
        return "match"
    rows = db.execute(
        "SELECT i.ride,i.first_page,r.id,m.ride,p.page FROM history_ids i"
        " LEFT JOIN rides r ON r.vehicle=i.vehicle AND r.id=i.ride"
        " LEFT JOIN membership m ON m.vehicle=i.vehicle AND m.month=i.month AND m.ride=i.ride"
        " LEFT JOIN history_pages p ON p.vehicle=i.vehicle AND p.plan=i.plan"
        " AND p.month=i.month AND p.page=i.first_page"
        " WHERE i.vehicle=? AND i.plan=? AND i.month=? AND i.ride IN ("
        + ",".join("?" for _ in ids)
        + ")",
        (*scope, *ids),
    ).fetchall()
    if len(rows) != len(ids) or any(
        type(first) is not int
        or not 1 <= first <= page
        or ride is None
        or member is None
        or first_source is None
        for _, first, ride, member, first_source in rows
    ):
        return "conflict"
    return "match"


def observe(
    db: sqlite3.Connection,
    vehicle: str,
    plan: "AuditPlan",
    obj: "StoredObject",
    outcome: str,
    now: datetime,
) -> None:
    scan = get(db, vehicle)
    if scan is None or not matches(scan, plan):
        return  # A resumed pre-upgrade raw-only scan cannot gain a full certificate.
    if (
        outcome not in {"match", "conflict", "unverifiable"}
        or not scan.after < obj.ordinal <= scan.page_upper
    ):
        raise ValueError("Invalid page reference checkpoint")
    put(
        db,
        vehicle,
        replace(
            scan,
            checked=scan.checked + 1,
            after=obj.ordinal,
            conflicts=scan.conflicts + int(outcome == "conflict"),
            unverifiable=scan.unverifiable + int(outcome == "unverifiable"),
            updated_at=now.isoformat(),
        ),
    )


def summary(
    db: sqlite3.Connection, vehicle: str, plan: "AuditPlan | None", revision: int, inventory: int
) -> PageReferenceSummary | None:
    scan = get(db, vehicle)
    if scan is None or plan is None or not matches(scan, plan):
        return None
    unchanged = (
        not plan.changed and revision == scan.cutoff_revision and inventory == scan.cutoff_inventory
    )
    complete = plan.complete and scan.checked == scan.total and scan.after == scan.page_upper
    return PageReferenceSummary(
        scan.total,
        scan.checked,
        scan.conflicts,
        scan.unverifiable,
        complete,
        unchanged,
        complete and unchanged and scan.total > 0 and not (scan.conflicts or scan.unverifiable),
        scan.updated_at,
    )
