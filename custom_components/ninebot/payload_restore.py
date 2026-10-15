"""Exact referenced-byte recovery from bounded account-local managed copies.

No reference/hash remapping, timestamp rewriting, arbitrary path or cloud query.
Corrupt originals are copied and checked before replacement in the caller's
transaction. Oversized in-place originals remain untouched. Candidate scope is
bounded, not an assertion that every possible recovery copy was exhausted.
"""

import hashlib
import heapq
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from .archive_codec import decoded, restored_ride, stamp
from .detail_codec import CorruptPayload, packed, scoped_ride, unpacked
from .object_checks import MAX_COMPRESSED, StoredObject, quarantine, verify
from .payload_inventory import advance

MAX_COPIES = 8
MAX_SCAN_BYTES = 16 * MAX_COMPRESSED
COLUMNS = ("vehicle", "ride", "hash", "body", "observed", "month", "backend", "endpoint")


def candidates(path: Path) -> tuple[Path, ...]:
    pattern = re.compile(re.escape(path.stem) + r"\.recovery-[0-9a-f]{32}\.sqlite3")
    items: list[tuple[int, str]] = []
    try:
        with os.scandir(path.parent) as entries:
            for entry in entries:
                if not pattern.fullmatch(entry.name):
                    continue
                try:
                    if entry.is_file(follow_symlinks=False) and not entry.is_symlink():
                        item = (entry.stat(follow_symlinks=False).st_mtime_ns, entry.name)
                        if len(items) < MAX_COPIES:
                            heapq.heappush(items, item)
                        else:
                            heapq.heappushpop(items, item)
                except OSError:
                    continue
    except OSError:
        return ()
    return tuple(path.with_name(name) for _, name in sorted(items, reverse=True))


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
    db: sqlite3.Connection, path: Path, owner: str, vehicle: str, ride: str, now: datetime
) -> int:
    row = db.execute("SELECT data FROM rides WHERE vehicle=? AND id=?", (vehicle, ride)).fetchone()
    pointers = db.execute(
        "SELECT latest_hash,geometry_hash FROM details WHERE vehicle=? AND ride=?", (vehicle, ride)
    ).fetchone()
    if row is None or pointers is None:
        return 0
    current = restored_ride(decoded(row[0]))
    copies: tuple[Path, ...] | None = None
    read_bytes = 0
    restored = 0
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
        if copies is None:
            copies = candidates(path)
        for copy in copies:
            body = copied_body(copy, owner, vehicle, ride, digest, metadata)
            if body is None:
                continue
            read_bytes += len(body)
            if read_bytes > MAX_SCAN_BYTES:
                return restored
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
            restored += 1
            break
    return restored
