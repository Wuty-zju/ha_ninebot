"""Conservative all-or-nothing recovery of missing referenced detail originals.

Only schema-5 verified receipts authenticate missing metadata. Repair a temporary
copy, run the entire strict checker, preserve original file bytes, then publish.
No cloud, arbitrary path, guessed metadata, normalized repair or FK disabling.
"""

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .archive_codec import decoded, restored_ride, stamp
from .detail_codec import CorruptPayload, packed, scoped_ride, unpacked
from .object_checks import CONTRACT, StoredObject, metadata, verify
from .payload_inventory import advance
from .payload_restore import copied_body
from .recovery_search import Search, names, page

MAX_MISSING = 8
MAX_COPIES = 8


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def idle(path: Path) -> bool:
    return not any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))


def rows(db: sqlite3.Connection, path: Path, owner: str) -> list[tuple] | None:
    missing = db.execute(
        "WITH refs AS (SELECT vehicle,ride,latest_hash AS hash FROM details UNION"
        " SELECT vehicle,ride,geometry_hash FROM details)"
        " SELECT r.vehicle,r.ride,r.hash FROM refs r LEFT JOIN detail_payloads p"
        " ON p.vehicle=r.vehicle AND p.ride=r.ride AND p.hash=r.hash"
        " WHERE p.hash IS NULL LIMIT ?",
        (MAX_MISSING + 1,),
    ).fetchall()
    if not missing or len(missing) > MAX_MISSING:
        return None
    search = Search("0" * 32, max(names(path), default=None))
    candidates, _ = page(path, search, MAX_COPIES)
    result = []
    for vehicle, ride, digest in missing:
        identity = json.dumps([ride, digest], ensure_ascii=False, separators=(",", ":"))
        receipt = db.execute(
            "SELECT metadata FROM payload_checks WHERE vehicle=? AND kind='detail'"
            " AND identity=? AND digest=? AND result='verified' AND contract=?",
            (vehicle, identity, digest, CONTRACT),
        ).fetchone()
        current = db.execute(
            "SELECT data FROM rides WHERE vehicle=? AND id=?", (vehicle, ride)
        ).fetchone()
        if receipt is None or current is None:
            return None
        meta = tuple(metadata(receipt[0], verified=True))
        normalized = restored_ride(decoded(current[0]))
        for candidate in candidates:
            source = path.with_name(f"{path.stem}.recovery-{candidate}.sqlite3")
            body = copied_body(source, owner, vehicle, ride, digest, meta)
            if body is None:
                continue
            try:
                raw = unpacked(body, digest, stamp(meta[0]), meta[1], meta[2], meta[3])
                scoped_ride(raw, normalized)
                if packed(raw)[0] != digest:
                    continue
            except (CorruptPayload, ValueError):
                continue
            result.append((vehicle, ride, digest, body, *meta))
            break
        else:
            return None
    return result


def recover(
    path: Path,
    owner: str,
    check: Callable[[sqlite3.Connection], None],
    *,
    minimum_free: int,
    max_bytes: int,
) -> bool:
    """Caller must first pass strict structural/owner check allowing only missing details.

    Eight missing objects and eight managed source files at most. Incomplete or
    oversized recovery is rejected, never partially published. The original is
    not opened for writes. There is no claim of exhaustive source discovery.
    """
    if (
        not idle(path)
        or shutil.disk_usage(path.parent).free < path.stat().st_size * 3 + minimum_free
    ):
        return False
    before = file_hash(path)
    temporary: list[Path] = []
    try:
        original = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
        try:
            recovered = rows(original, path, owner)
            if recovered is None:
                return False
            descriptor, name = tempfile.mkstemp(prefix=".ninebot-startup-", dir=path.parent)
            os.close(descriptor)
            staged = Path(name)
            temporary.append(staged)
            target = sqlite3.connect(staged)
            try:
                original.backup(target)
                pages = target.execute("PRAGMA page_count").fetchone()[0]
                size = target.execute("PRAGMA page_size").fetchone()[0]
                target.execute(f"PRAGMA max_page_count={max(pages, max_bytes // size)}")
                target.execute("PRAGMA foreign_keys=ON")
                with target:
                    target.executemany(
                        "INSERT INTO detail_payloads VALUES (?,?,?,?,?,?,?,?)", recovered
                    )
                    now = datetime.now(UTC)
                    for vehicle, ride, digest, body, *meta in recovered:
                        obj = StoredObject(
                            vehicle,
                            "detail",
                            0,
                            json.dumps([ride, digest], ensure_ascii=False, separators=(",", ":")),
                            digest,
                            json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
                            len(body),
                        )
                        if verify(target, obj, now) is not None:
                            return False
                        advance(target, vehicle)
                    check(target)
            finally:
                target.close()
        finally:
            original.close()
        if not idle(path) or file_hash(path) != before:
            return False
        descriptor, name = tempfile.mkstemp(prefix=".ninebot-forensic-", dir=path.parent)
        os.close(descriptor)
        forensic = Path(name)
        temporary.append(forensic)
        shutil.copyfile(path, forensic)
        if file_hash(forensic) != before or file_hash(path) != before or not idle(path):
            return False
        backup = path.with_name(f"{path.stem}.recovery-{secrets.token_hex(16)}.sqlite3")
        # Both the preserved bytes and replacement reach disk before publication.
        for item in (forensic, staged):
            with item.open("rb") as stream:
                os.fsync(stream.fileno())
        os.replace(forensic, backup)
        os.replace(staged, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return True
    finally:
        for item in temporary:
            item.unlink(missing_ok=True)
