"""Exact referenced-byte recovery from bounded account-local managed copies.

No reference/hash remapping, timestamp rewriting, arbitrary path or cloud query.
Corrupt originals are copied and checked before replacement in the caller's
transaction. Oversized in-place originals remain untouched. Candidate scope is
bounded, not an assertion that every possible recovery copy was exhausted.
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .archive_codec import decoded, restored_ride, stamp
from .detail_codec import CorruptPayload, packed, scoped_ride, unpacked
from .object_checks import MAX_COMPRESSED, StoredObject, quarantine, verify
from .payload_inventory import advance

MAX_COPIES = 8
COLUMNS = ("vehicle", "ride", "hash", "body", "observed", "month", "backend", "endpoint")


@dataclass(frozen=True)
class RestoreResult:
    recovered: int = 0
    pending: bool = False
    examined: int = 0


def copied_body(
    path: Path,
    owner: str,
    vehicle: str,
    ride: str,
    digest: str,
    metadata: tuple,
) -> bytes | None:
    if path.is_symlink():
        return None
    try:
        source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
        try:
            if source.execute("PRAGMA user_version").fetchone()[0] not in {2, 3, 4, 5}:
                return None
            if source.execute("SELECT value FROM meta WHERE key='owner'").fetchone() != (owner,):
                return None
            if source.execute(
                "SELECT type FROM sqlite_schema WHERE name='detail_payloads'"
            ).fetchone() != ("table",):
                return None
            if (
                tuple(row[1] for row in source.execute("PRAGMA table_info(detail_payloads)"))
                != COLUMNS
            ):
                return None
            row = source.execute(
                "SELECT CASE WHEN length(body)<=? THEN body END,observed,month,backend,endpoint"
                " FROM detail_payloads WHERE vehicle=? AND ride=? AND hash=?",
                (MAX_COMPRESSED, vehicle, ride, digest),
            ).fetchone()
            return row[0] if row and row[1:] == metadata and isinstance(row[0], bytes) else None
        finally:
            source.close()
    except (sqlite3.DatabaseError, OSError, ValueError):
        return None  # One unreadable/foreign copy cannot block other candidates.


def restore(
    db: sqlite3.Connection,
    path: Path,
    owner: str,
    vehicle: str,
    ride: str,
    now: datetime,
    job_id: str,
) -> RestoreResult:
    from .recovery_search import get, key, page, put

    row = db.execute("SELECT data FROM rides WHERE vehicle=? AND id=?", (vehicle, ride)).fetchone()
    pointers = db.execute(
        "SELECT latest_hash,geometry_hash FROM details WHERE vehicle=? AND ride=?", (vehicle, ride)
    ).fetchone()
    if row is None or pointers is None:
        return RestoreResult()
    current = restored_ride(decoded(row[0]))
    recovered, examined = 0, 0
    pending = False
    for digest in dict.fromkeys(pointers):
        original = db.execute(
            "SELECT CASE WHEN length(body)<=? THEN body END,length(body),observed,month,"
            "backend,endpoint"
            " FROM detail_payloads WHERE vehicle=? AND ride=? AND hash=?",
            (MAX_COMPRESSED, vehicle, ride, digest),
        ).fetchone()
        if original is None or original[0] is None or not isinstance(original[0], bytes):
            continue  # Missing references/oversized pinned bytes require another recovery path.
        metadata = tuple(original[2:])
        try:
            record = unpacked(
                original[0], digest, stamp(metadata[0]), metadata[1], metadata[2], metadata[3]
            )
            scoped_ride(record, current)
            continue
        except CorruptPayload:
            pass
        scope = key(vehicle, ride, digest)
        try:
            search = get(db, scope, path, job_id)
            if search.finished:
                put(db, scope, search)
                continue
            files, more = (
                page(path, search, MAX_COPIES - examined) if examined < MAX_COPIES else ((), True)
            )
        except OSError:
            pending = True  # No false exhaustion from a transient directory failure.
            continue
        for identity in files:
            copy = path.with_name(f"{path.stem}.recovery-{identity}.sqlite3")
            search = replace(search, after=identity, checked=search.checked + 1)
            examined += 1
            body = copied_body(copy, owner, vehicle, ride, digest, metadata)
            if body is None:
                continue
            try:
                record = unpacked(
                    body, digest, stamp(metadata[0]), metadata[1], metadata[2], metadata[3]
                )
                scoped_ride(record, current)
                if packed(record)[0] != digest:
                    continue  # Never restore bytes that bypass current redaction policy.
            except (CorruptPayload, ValueError):
                continue
            obj = StoredObject(
                vehicle,
                "detail",
                0,
                json.dumps([ride, digest], ensure_ascii=False, separators=(",", ":")),
                digest,
                json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                original[1],
            )
            quarantine(db, obj, original[0], "payload", now)
            saved = db.execute(
                "SELECT body,body_hash FROM payload_quarantine WHERE vehicle=? AND kind='detail'"
                " AND identity=? AND digest=? AND storage_mode='copied' AND body_hash=?",
                (vehicle, obj.identity, digest, hashlib.sha256(original[0]).hexdigest()),
            ).fetchone()
            if (
                not saved
                or saved[0] != original[0]
                or hashlib.sha256(saved[0]).hexdigest() != saved[1]
            ):
                raise CorruptPayload("Unverified forensic preservation")
            db.execute(
                "UPDATE detail_payloads SET body=? WHERE vehicle=? AND ride=? AND hash=?",
                (body, vehicle, ride, digest),
            )
            # Refresh a matching negative receipt immediately, not a day later.
            updated = StoredObject(
                vehicle, "detail", 0, obj.identity, digest, obj.metadata, len(body)
            )
            if verify(db, updated, now) is not None:
                raise CorruptPayload("Recovered original failed verification")
            advance(db, vehicle)
            recovered += 1
            search = replace(search, finished=True)
            break
        else:
            search = replace(search, finished=not more)
        put(db, scope, search)
        pending = pending or not search.finished
    return RestoreResult(recovered, pending, examined)
