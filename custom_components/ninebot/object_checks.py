"""Schema-5 scoped integrity receipts and private forensic copies, one SQL actor.

Receipts bind logical identity, digest, metadata and checker contract, not rowid.
They describe past checks; daily deep scans still detect unobserved bit corruption.
No normalized metrics, cloud coverage, coordinates or credentials are exported.
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .archive_codec import stamp
from .const import MAX_RESPONSE_BYTES
from .detail_codec import CorruptPayload, unpacked
from .history_plan import MAX_PAGES
from .parsing import previous_month
from .raw import Endpoint, version_metadata
from .travel import opaque_id

CONTRACT = 1
MAX_COMPRESSED = MAX_RESPONSE_BYTES + 1024
RESULTS = {"verified", "payload", "metadata"}
SCHEMA = (
    "CREATE TABLE payload_checks (vehicle TEXT NOT NULL, kind TEXT NOT NULL,"
    " identity TEXT NOT NULL, digest TEXT NOT NULL, metadata TEXT NOT NULL,"
    " checked_at TEXT NOT NULL, result TEXT NOT NULL, contract INTEGER NOT NULL,"
    " PRIMARY KEY(vehicle,kind,identity))",
    "CREATE TABLE payload_quarantine (vehicle TEXT NOT NULL, kind TEXT NOT NULL,"
    " identity TEXT NOT NULL, evidence_key TEXT NOT NULL, digest TEXT NOT NULL,"
    " metadata TEXT NOT NULL, source_size INTEGER, storage_mode TEXT NOT NULL,"
    " body BLOB, body_hash TEXT,"
    " reason TEXT NOT NULL, captured_at TEXT NOT NULL,"
    " PRIMARY KEY(vehicle,kind,identity,evidence_key))",
    "CREATE INDEX quarantine_scope ON payload_quarantine(vehicle,kind)",
)
COLUMNS = {
    "payload_checks": (
        "vehicle",
        "kind",
        "identity",
        "digest",
        "metadata",
        "checked_at",
        "result",
        "contract",
    ),
    "payload_quarantine": (
        "vehicle",
        "kind",
        "identity",
        "evidence_key",
        "digest",
        "metadata",
        "source_size",
        "storage_mode",
        "body",
        "body_hash",
        "reason",
        "captured_at",
    ),
}
# Both expressions return metadata only. The body is opened after admission.
DETAIL_SOURCE = (
    "SELECT 'detail' AS kind,d.rowid AS ordinal,json_array(d.ride,d.hash) AS identity,"
    " d.hash AS digest,json_array(d.observed,d.month,d.backend,d.endpoint) AS metadata,"
    " length(d.body) AS size FROM detail_payloads d WHERE d.vehicle=?"
)
PAGE_SOURCE = (
    "SELECT 'page' AS kind,p.rowid AS ordinal,json_array(p.plan,p.month,p.page) AS identity,"
    " p.digest AS digest,json_array(p.observed,p.month,p.backend,p.endpoint) AS metadata,"
    " CASE WHEN length(p.body)>0 THEN length(p.body) ELSE coalesce("
    " (SELECT length(h.body) FROM history_payloads h WHERE h.digest=p.digest),"
    " (SELECT length(old.body) FROM history_pages old WHERE old.digest=p.digest"
    " AND length(old.body)>0 LIMIT 1),0) END AS size FROM history_pages p WHERE p.vehicle=?"
)
MATCH = (
    "c.vehicle=? AND c.kind=s.kind AND c.identity=s.identity"
    " AND c.digest=s.digest AND c.metadata=s.metadata AND c.contract=?"
)


def digest(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("Invalid private evidence digest")


def array(value: str, length: int) -> list:
    if not isinstance(value, str) or len(value.encode()) > 2048:
        raise ValueError("Invalid private evidence metadata budget")
    data = json.loads(value)
    if not isinstance(data, list) or len(data) != length:
        raise ValueError("Invalid private evidence metadata shape")
    return data


def identity(kind: str, value: str) -> list:
    if kind not in {"detail", "page"}:
        raise ValueError("Invalid private evidence kind")
    data = array(value, 2 if kind == "detail" else 3)
    if kind == "detail":
        if opaque_id(data[0]) != data[0]:
            raise ValueError("Invalid private evidence ride")
        digest(data[1])
    else:
        if (
            not isinstance(data[0], str)
            or len(data[0]) != 32
            or any(c not in "0123456789abcdef" for c in data[0])
        ):
            raise ValueError("Invalid private evidence run")
        previous_month(data[1])
        if type(data[2]) is not int or not 1 <= data[2] <= MAX_PAGES:
            raise ValueError("Invalid private evidence page")
    return data


def metadata(value: str, *, verified: bool) -> list:
    data = array(value, 4)
    if any(x is not None and (not isinstance(x, str) or len(x) > 128) for x in data):
        raise ValueError("Invalid private evidence metadata type")
    if verified:
        stamp(data[0])
        previous_month(data[1])
        for item in data[2:]:
            if item is not None and version_metadata(item) != item:
                raise ValueError("Invalid private evidence version")
    return data


@dataclass(frozen=True)
class StoredObject:
    vehicle: str
    kind: str
    ordinal: int
    identity: str
    digest: str
    metadata: str
    size: int | None


@dataclass(frozen=True)
class ObjectSummary:
    total: int
    verified: int
    invalid: int
    pending: int
    quarantined: int

    @property
    def receipts_complete(self) -> bool:
        return self.total > 0 and self.verified == self.total and self.invalid == self.pending == 0


def sources(
    db: sqlite3.Connection,
    vehicle: str,
    kind: str,
    *,
    after: int = 0,
    upper: int = 2**63 - 1,
    limit: int = 8,
    pending: bool = False,
) -> tuple[StoredObject, ...]:
    if kind not in {"detail", "page"} or type(limit) is not int or not 1 <= limit <= 8:
        raise ValueError("Invalid private evidence query")
    source = DETAIL_SOURCE if kind == "detail" else PAGE_SOURCE
    join = f" LEFT JOIN payload_checks c ON {MATCH}" if pending else ""
    conditions = " AND c.identity IS NULL" if pending else ""
    params = (vehicle, vehicle, CONTRACT) if pending else (vehicle,)
    rows = db.execute(
        f"SELECT s.* FROM ({source}) s{join} WHERE s.ordinal>? AND s.ordinal<=?"
        f"{conditions} ORDER BY s.ordinal LIMIT ?",
        (*params, after, upper, limit),
    ).fetchall()
    return tuple(StoredObject(vehicle, *row) for row in rows)


def load_body(db: sqlite3.Connection, obj: StoredObject) -> bytes | None:
    ref = identity(obj.kind, obj.identity)
    if obj.kind == "detail":
        row = db.execute(
            "SELECT CASE WHEN length(body)<=? THEN body END FROM detail_payloads"
            " WHERE vehicle=? AND ride=? AND hash=?",
            (MAX_COMPRESSED, obj.vehicle, *ref),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT CASE WHEN length(p.body)>0 THEN CASE WHEN length(p.body)<=? THEN p.body END"
            " ELSE (SELECT CASE WHEN length(h.body)<=? THEN h.body END FROM history_payloads h"
            " WHERE h.digest=p.digest) END FROM history_pages p"
            " WHERE p.vehicle=? AND p.plan=? AND p.month=? AND p.page=?",
            (MAX_COMPRESSED, MAX_COMPRESSED, obj.vehicle, *ref),
        ).fetchone()
        # Preserve old schema3 inline evidence after its schema5 migration.
        if row and row[0] is None and obj.size and obj.size <= MAX_COMPRESSED:
            row = db.execute(
                "SELECT body FROM history_pages WHERE digest=? AND length(body)>0"
                " AND length(body)<=? LIMIT 1",
                (obj.digest, MAX_COMPRESSED),
            ).fetchone()
    return row[0] if row and isinstance(row[0], bytes) else None


def quarantine(
    db: sqlite3.Connection, obj: StoredObject, body: bytes | None, reason: str, now: datetime
) -> None:
    mode = "copied" if body is not None else "in_place" if obj.size else "missing"
    actual = hashlib.sha256(body).hexdigest() if body is not None else None
    key = hashlib.sha256(
        json.dumps([mode, obj.digest, obj.metadata, actual, obj.size]).encode()
    ).hexdigest()
    db.execute(
        "INSERT OR IGNORE INTO payload_quarantine VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            obj.vehicle,
            obj.kind,
            obj.identity,
            key,
            obj.digest,
            obj.metadata,
            obj.size,
            mode,
            body,
            actual,
            reason,
            now.isoformat(),
        ),
    )


def verify(db: sqlite3.Connection, obj: StoredObject, now: datetime) -> str | None:
    body = None
    result = "verified"
    try:
        identity(obj.kind, obj.identity)
        digest(obj.vehicle)
        digest(obj.digest)
        meta = metadata(obj.metadata, verified=True)
        if not isinstance(obj.size, int) or not 0 <= obj.size <= MAX_COMPRESSED:
            raise CorruptPayload("Private object byte budget")
        body = load_body(db, obj)
        if body is None:
            raise CorruptPayload("Missing private object bytes")
        unpacked(
            body,
            obj.digest,
            stamp(meta[0]),
            meta[1],
            meta[2],
            meta[3],
            source=Endpoint.TRIP_DETAIL if obj.kind == "detail" else Endpoint.TRAVEL,
        )
    except CorruptPayload:
        result = "payload"
    except (ValueError, TypeError, KeyError, OverflowError):
        result = "metadata"
    if result != "verified":
        quarantine(db, obj, body, result, now)
    db.execute(
        "INSERT INTO payload_checks VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(vehicle,kind,identity)"
        " DO UPDATE SET digest=excluded.digest,metadata=excluded.metadata,"
        " checked_at=excluded.checked_at,result=excluded.result,contract=excluded.contract",
        (
            obj.vehicle,
            obj.kind,
            obj.identity,
            obj.digest,
            obj.metadata,
            now.isoformat(),
            result,
            CONTRACT,
        ),
    )
    return None if result == "verified" else result


def summary(db: sqlite3.Connection, vehicle: str) -> ObjectSummary:
    row = db.execute(
        f"WITH s AS ({DETAIL_SOURCE} UNION ALL {PAGE_SOURCE})"
        f" SELECT count(*),coalesce(sum(c.result='verified'),0),"
        " coalesce(sum(c.result IN ('payload','metadata')),0),coalesce(sum(c.identity IS NULL),0)"
        f" FROM s LEFT JOIN payload_checks c ON {MATCH}",
        (vehicle, vehicle, vehicle, CONTRACT),
    ).fetchone()
    quarantined = db.execute(
        "SELECT count(*) FROM payload_quarantine WHERE vehicle=?", (vehicle,)
    ).fetchone()[0]
    return ObjectSummary(int(row[0]), int(row[1]), int(row[2]), int(row[3]), int(quarantined))


def check(db: sqlite3.Connection) -> None:
    for vehicle, kind, ref, expected, meta, checked, result, contract in db.execute(
        "SELECT * FROM payload_checks"
    ):
        digest(vehicle)
        if result == "verified":
            digest(expected)
            identity(kind, ref)
        elif kind not in {"detail", "page"} or not isinstance(expected, str) or len(expected) > 128:
            raise ValueError("Invalid failed-check snapshot")
        else:
            array(ref, 2 if kind == "detail" else 3)
        metadata(meta, verified=result == "verified")
        stamp(checked)
        if result not in RESULTS or type(contract) is not int or not 1 <= contract <= 2**31 - 1:
            raise ValueError("Invalid private check contract")
    # Startup checks only structure and bounded size/type metadata, not all BLOBs.
    for (
        vehicle,
        kind,
        ref,
        key,
        expected,
        meta,
        source_size,
        mode,
        size,
        typ,
        actual,
        reason,
        captured,
    ) in db.execute(
        "SELECT vehicle,kind,identity,evidence_key,digest,metadata,source_size,storage_mode,"
        " length(body),"
        " typeof(body),body_hash,reason,captured_at FROM payload_quarantine"
    ):
        for item in (vehicle, key):
            digest(item)
        if kind not in {"detail", "page"} or not isinstance(expected, str) or len(expected) > 128:
            raise ValueError("Invalid forensic source snapshot")
        array(ref, 2 if kind == "detail" else 3)
        metadata(meta, verified=False)
        stamp(captured)
        if source_size is not None and (
            type(source_size) is not int or not 0 <= source_size <= 2**63 - 1
        ):
            raise ValueError("Invalid forensic source size")
        expected_key = hashlib.sha256(
            json.dumps([mode, expected, meta, actual, source_size]).encode()
        ).hexdigest()
        if expected_key != key:
            raise ValueError("Invalid forensic metadata fingerprint")
        if reason not in {"payload", "metadata"} or mode not in {"copied", "in_place", "missing"}:
            raise ValueError("Invalid private quarantine reason")
        if mode == "copied":
            digest(actual)
            if typ != "blob" or not 0 <= size <= MAX_COMPRESSED or size != source_size:
                raise ValueError("Invalid private quarantine copy")
        elif typ != "null" or actual is not None:
            raise ValueError("Invalid private quarantine reference")
