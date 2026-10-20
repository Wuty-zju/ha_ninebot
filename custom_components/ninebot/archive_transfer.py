"""Private, versioned archive snapshots and transactional same-owner merging.

Only reviewed scalar tables are replayed into a live actor. Uploaded SQL/schema,
remote job checkpoints and authentication material are never executed/adopted.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import time
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .archive_codec import decoded, merged_ride, restored_ride, stamp
from .detail_codec import scoped_ride, unpacked
from .raw import Endpoint
from .travel import select_geometry

if TYPE_CHECKING:
    from .ride_archive import RideArchive

FORMAT = 1
MAX_PACKAGE = 100 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_ACCOUNTS = 16


def digest_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def readonly(path: Path) -> sqlite3.Connection:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_EXPANDED:
        raise ValueError("Invalid archive file")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 4 * 1024 * 1024)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA trusted_schema=OFF")
    deadline = time.monotonic() + 120
    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1"
    ).fetchone():
        db.close()
        raise ValueError("Executable archive schema is not accepted")
    return db


def snapshot(db: sqlite3.Connection, target: Path, vehicles: tuple[str, ...] | None = None) -> None:
    """SQLite backup API gives a consistent committed image, including WAL sources."""
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    size = (
        db.execute("PRAGMA page_size").fetchone()[0] * db.execute("PRAGMA page_count").fetchone()[0]
    )
    if shutil.disk_usage(target.parent).free < size * 2 + 32 * 1024 * 1024:
        raise ValueError("Insufficient snapshot space")
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    out = sqlite3.connect(target)
    try:
        db.backup(out)
        if vehicles is not None:
            known = {row[0] for row in out.execute("SELECT vehicle FROM vehicles")}
            if not vehicles or not set(vehicles) <= known:
                raise ValueError("Unknown vehicle selection")
            remove = known - set(vehicles)
            tables = [
                row[0] for row in out.execute("SELECT name FROM sqlite_master WHERE type='table'")
            ]
            # Table identifiers come only from the reviewed schema, not package names.
            for table in tables:
                if "vehicle" in {row[1] for row in out.execute(f'PRAGMA table_info("{table}")')}:
                    out.executemany(
                        f'DELETE FROM "{table}" WHERE vehicle=?', ((v,) for v in remove)
                    )
            out.execute(
                "DELETE FROM history_payloads WHERE digest NOT IN "
                "(SELECT digest FROM history_pages)"
            )
            # Account-wide and other-vehicle job strings must not escape a subset export.
            allowed = set(vehicles)
            for key, _ in out.execute("SELECT key,value FROM meta").fetchall():
                if key in {"owner", "revision", "profiles_observed"}:
                    continue
                if not any(v in key for v in allowed):
                    out.execute("DELETE FROM meta WHERE key=?", (key,))
            out.commit()
        out.execute("VACUUM")
    finally:
        out.close()


def validate(actor: "RideArchive", db: sqlite3.Connection) -> None:
    """Deep validation: bounds, strict codecs, foreign keys and raw digest/scope."""
    actor._check(db)
    for vehicle, identity, digest, body, observed, month, backend, endpoint in db.execute(
        "SELECT * FROM detail_payloads"
    ):
        row = db.execute(
            "SELECT data FROM rides WHERE vehicle=? AND id=?", (vehicle, identity)
        ).fetchone()
        raw = unpacked(body, digest, stamp(observed), month, backend, endpoint)
        scoped_ride(raw, restored_ride(decoded(row[0])))
    for _, month, digest, body, observed, backend, endpoint, _ in db.execute(
        "SELECT * FROM month_sources"
    ):
        unpacked(body, digest, stamp(observed), month, backend, endpoint, source=Endpoint.TRAVEL)
    # Historical page bodies may be content-addressed in the shared payload table.
    for month, digest, body, observed, backend, endpoint in db.execute(
        "SELECT p.month,p.digest,CASE WHEN length(p.body)>0 THEN p.body ELSE "
        "coalesce(h.body,(SELECT old.body FROM history_pages old WHERE old.digest=p.digest "
        "AND length(old.body)>0 LIMIT 1)) END,p.observed,p.backend,p.endpoint "
        "FROM history_pages p LEFT JOIN history_payloads h ON h.digest=p.digest"
    ):
        unpacked(body, digest, stamp(observed), month, backend, endpoint, source=Endpoint.TRAVEL)


def _insert(db: sqlite3.Connection, table: str, row: tuple[Any, ...]) -> None:
    db.execute(f"INSERT OR REPLACE INTO {table} VALUES ({','.join('?' for _ in row)})", row)


def merge(
    actor: "RideArchive",
    source: Path,
    vehicles: tuple[str, ...] | None = None,
    evidence_sources: tuple[Path, ...] = (),
) -> dict[str, int]:
    """One account transaction, deterministic replay, no source DDL/checkpoints."""
    db = actor._db(write=True)
    incoming = readonly(source)
    try:
        validate(actor, incoming)
        selected = {r[0] for r in incoming.execute("SELECT vehicle FROM vehicles")}
        if vehicles is not None:
            if not vehicles or not set(vehicles) <= selected:
                raise ValueError("Unknown import selection")
            selected = set(vehicles)
        before = {
            t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("rides", "details")
        }
        receipt = (
            "account_import:"
            + hashlib.sha256(
                (
                    digest_file(source)
                    + ":"
                    + ":".join(digest_file(p) for p in evidence_sources)
                    + ":"
                    + ",".join(sorted(selected))
                ).encode()
            ).hexdigest()
        )
        if db.execute("SELECT 1 FROM meta WHERE key=?", (receipt,)).fetchone():
            return {"rides": 0, "details": 0}
        # Evidence is immutable and separate from live jobs. Validate every file
        # before any account facts are changed; subset copies exclude other cars.
        imports = actor.path.parent / "imports"
        imports.mkdir(mode=0o700, exist_ok=True)
        for original in (source, *evidence_sources):
            original_db = readonly(original)
            try:
                validate(actor, original_db)
                available = {r[0] for r in original_db.execute("SELECT vehicle FROM vehicles")}
                keys = tuple(sorted(selected & available))
                if not keys:
                    continue
                pending_evidence = imports / "pending.sqlite3"
                pending_evidence.unlink(missing_ok=True)
                snapshot(original_db, pending_evidence, keys)
                evidence = imports / (digest_file(pending_evidence) + ".sqlite3")
                if evidence.exists():
                    pending_evidence.unlink()
                    continue
                if (
                    sum(p.stat().st_size for p in imports.glob("*.sqlite3"))
                    + pending_evidence.stat().st_size
                    > MAX_EXPANDED
                    or len(list(imports.glob("*.sqlite3"))) > 16
                ):
                    pending_evidence.unlink()
                    raise ValueError("Recovery evidence budget exceeded")
                os.replace(pending_evidence, evidence)
            finally:
                original_db.close()
        # Keep a consistent rollback image independently of transaction rollback.
        recovery = actor.path.parent / "recovery.sqlite3"
        pending = actor.path.parent / "recovery.pending.sqlite3"
        pending.unlink(missing_ok=True)
        snapshot(db, pending)
        os.replace(pending, recovery)
        with db:
            for row in incoming.execute("SELECT * FROM vehicles"):
                if row[0] not in selected:
                    continue
                old = db.execute(
                    "SELECT observed FROM vehicles WHERE vehicle=?", (row[0],)
                ).fetchone()
                if old is None:
                    _insert(db, "vehicles", (row[0], row[1], row[2], 0))
                elif old[0] < row[2]:
                    db.execute(
                        "UPDATE vehicles SET profile=?,observed=? WHERE vehicle=?",
                        (row[1], row[2], row[0]),
                    )
            for row in incoming.execute("SELECT * FROM rides"):
                if row[0] in selected:
                    old = actor._ride(row[0], row[1])
                    candidate = restored_ride(decoded(row[2]))
                    if old and (
                        (
                            old[0].detail_id is not None
                            and candidate.detail_id is not None
                            and old[0].detail_id != candidate.detail_id
                        )
                        or old[0].query_month != candidate.query_month
                    ):
                        raise ValueError("Conflicting ride identity")
                    if old and old[1] >= row[3]:
                        candidate = merged_ride(candidate, old[0])
                        actor._upsert_ride(row[0], candidate, old[1], ordered=True)
                    else:
                        actor._upsert_ride(row[0], candidate, row[3])
            for row in incoming.execute("SELECT * FROM months"):
                if row[0] not in selected:
                    continue
                old = db.execute(
                    "SELECT observed,revision FROM months WHERE vehicle=? AND month=?", row[:2]
                ).fetchone()
                if old is None or old[0] < row[3]:
                    replacement = (*row[:4], max(row[4], old[1] if old else 0) + 1, *row[5:])
                    _insert(db, "months", replacement)
                    db.execute(
                        "UPDATE membership SET latest=0 WHERE vehicle=? AND month=?", row[:2]
                    )
                    ids = actor._ids(row[6])
                    for ride_id in ids:
                        db.execute(
                            "INSERT INTO membership VALUES (?,?,?,1) "
                            "ON CONFLICT(vehicle,month,ride) "
                            "DO UPDATE SET latest=1",
                            (*row[:2], ride_id),
                        )
                    db.execute("DELETE FROM month_sources WHERE vehicle=? AND month=?", row[:2])
                    raw = incoming.execute(
                        "SELECT * FROM month_sources WHERE vehicle=? AND month=?", row[:2]
                    ).fetchone()
                    if raw:
                        _insert(db, "month_sources", raw)
            for row in incoming.execute("SELECT vehicle,month,ride,0 FROM membership"):
                if row[0] in selected:
                    db.execute("INSERT OR IGNORE INTO membership VALUES (?,?,?,?)", row)
            for row in incoming.execute("SELECT * FROM details"):
                vehicle, ride_id, latest_hash, geometry_hash, observed, revision, selection = row
                if vehicle not in selected:
                    continue
                old = actor._private_detail(vehicle, ride_id, 2000)
                summary = actor._ride(vehicle, ride_id)
                assert summary is not None
                payloads = {
                    r[2]: r
                    for r in incoming.execute(
                        "SELECT * FROM detail_payloads WHERE vehicle=? AND ride=?",
                        (vehicle, ride_id),
                    )
                }
                chosen = scoped_ride(
                    unpacked(
                        payloads[geometry_hash][3],
                        geometry_hash,
                        stamp(payloads[geometry_hash][4]),
                        payloads[geometry_hash][5],
                        payloads[geometry_hash][6],
                        payloads[geometry_hash][7],
                    ),
                    summary[0],
                    max_points=2000,
                )
                if old:
                    assert old.latest is not None and old.geometry is not None
                    previous = db.execute(
                        "SELECT latest_hash,geometry_hash FROM details WHERE vehicle=? AND ride=?",
                        (vehicle, ride_id),
                    ).fetchone()
                    adopt, reason = select_geometry(old.ride, chosen)
                    if not adopt:
                        geometry_hash = previous[1]
                        selection = reason
                    if old.latest.received_at >= stamp(observed):
                        latest_hash, observed = (
                            previous[0],
                            old.latest.received_at.isoformat(timespec="microseconds"),
                        )
                        # archive timestamps use the same UTC ISO representation.
                        from .ride_archive import utc_string

                        observed = utc_string(old.latest.received_at)
                for digest in {latest_hash, geometry_hash}:
                    if digest in payloads:
                        db.execute(
                            "INSERT OR IGNORE INTO detail_payloads VALUES (?,?,?,?,?,?,?,?)",
                            payloads[digest],
                        )
                _insert(
                    db,
                    "details",
                    (
                        vehicle,
                        ride_id,
                        latest_hash,
                        geometry_hash,
                        observed,
                        max(revision, old.revision if old else 0) + 1,
                        selection,
                    ),
                )
                db.execute(
                    "DELETE FROM detail_payloads WHERE vehicle=? AND ride=? AND hash NOT IN (?,?)",
                    (vehicle, ride_id, latest_hash, geometry_hash),
                )
                from .detail_health import put as put_health

                saved = actor._private_detail(vehicle, ride_id, 1)
                assert saved is not None and saved.latest_quality is not None
                assert saved.selected_quality is not None
                put_health(
                    db,
                    vehicle,
                    ride_id,
                    latest_hash,
                    geometry_hash,
                    saved.latest_quality,
                    saved.selected_quality,
                )
            from .payload_inventory import advance

            for vehicle in selected:
                db.execute("DELETE FROM payload_checks WHERE vehicle=?", (vehicle,))
                advance(db, vehicle)
            db.execute("INSERT INTO meta VALUES (?,?)", (receipt, "1"))
            actor._revision(bump=True)
            actor._check(db)
        return {
            t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] - before[t] for t in before
        }
    finally:
        incoming.close()


def pack(
    target: Path,
    snapshots: Iterable[tuple[str, Path]],
    evidence: dict[str, list[Path]] | None = None,
) -> None:
    records = []
    expanded = 0
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        os.chmod(target, 0o600)
        for owner, path in snapshots:
            name = f"accounts/{owner}.sqlite3"
            record: dict[str, Any] = {
                "owner": owner,
                "file": name,
                "sha256": digest_file(path),
                "bytes": path.stat().st_size,
                "evidence": [],
            }
            bundle.write(path, name)
            expanded += path.stat().st_size
            seen_sources: set[str] = set()
            for original in (evidence or {}).get(owner, []):
                digest = digest_file(original)
                if digest in seen_sources:
                    continue
                seen_sources.add(digest)
                if len(seen_sources) > 16:
                    raise ValueError("Recovery source budget exceeded")
                member = f"evidence/{owner}/{digest}.sqlite3"
                record["evidence"].append(
                    {"file": member, "sha256": digest, "bytes": original.stat().st_size}
                )
                bundle.write(original, member)
                expanded += original.stat().st_size
            records.append(record)
        if not 1 <= len(records) <= MAX_ACCOUNTS or expanded > MAX_EXPANDED:
            raise ValueError("Package exceeds account or expanded byte budget")
        bundle.writestr("manifest.json", json.dumps({"format": FORMAT, "accounts": records}))
    if target.stat().st_size > MAX_PACKAGE:
        raise ValueError("Package exceeds upload budget; export fewer vehicles")


def unpack(source: Path, target: Path) -> list[tuple[str, Path]]:
    if source.stat().st_size > MAX_PACKAGE:
        raise ValueError("Package exceeds upload budget")
    with zipfile.ZipFile(source) as bundle:
        infos = bundle.infolist()
        if len(infos) > MAX_ACCOUNTS * 17 + 1 or len({i.filename for i in infos}) != len(infos):
            raise ValueError("Invalid package members")
        if sum(i.file_size for i in infos) > MAX_EXPANDED:
            raise ValueError("Expanded package exceeds budget")
        manifest_info = bundle.getinfo("manifest.json")
        if manifest_info.file_size > 65536:
            raise ValueError("Invalid manifest budget")
        manifest = json.loads(bundle.read(manifest_info))
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != FORMAT
            or not isinstance(records := manifest.get("accounts"), list)
            or not 1 <= len(records) <= MAX_ACCOUNTS
        ):
            raise ValueError("Unsupported package format")
        expected = {"manifest.json"}
        result = []
        owners = set()
        target.mkdir(mode=0o700, parents=True, exist_ok=False)
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Invalid account record")
            owner = record["owner"]
            if (
                not isinstance(owner, str)
                or len(owner) != 64
                or any(c not in "0123456789abcdef" for c in owner)
                or owner in owners
            ):
                raise ValueError("Invalid account identity")
            owners.add(owner)
            evidence = record.get("evidence", [])
            if not isinstance(evidence, list) or len(evidence) > 16:
                raise ValueError("Invalid evidence budget")
            for number, member in enumerate([record, *evidence]):
                if not isinstance(member, dict):
                    raise ValueError("Invalid source record")
                digest = member["sha256"]
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)
                ):
                    raise ValueError("Invalid source digest")
                name = (
                    f"accounts/{owner}.sqlite3"
                    if number == 0
                    else f"evidence/{owner}/{digest}.sqlite3"
                )
                if member["file"] != name or name in expected:
                    raise ValueError("Invalid account member")
                expected.add(name)
                info = bundle.getinfo(name)
                if stat.S_ISLNK(info.external_attr >> 16) or info.file_size != member["bytes"]:
                    raise ValueError("Invalid database member")
                path = target / f"{owner}-{number}.sqlite3"
                with bundle.open(info) as src, path.open("xb") as dst:
                    os.chmod(path, 0o600)
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                if digest_file(path) != digest:
                    raise ValueError("Invalid database digest")
                result.append((owner, path))
        if {i.filename for i in infos} != expected:
            raise ValueError("Unexpected package members")
        return result


def unpack_upload(source: Path, target: Path) -> list[tuple[str, Path]]:
    """Accept a portable package or a current-schema standalone database."""
    with source.open("rb") as stream:
        sqlite_header = stream.read(16) == b"SQLite format 3\x00"
    if not sqlite_header:
        return unpack(source, target)
    if source.stat().st_size > MAX_PACKAGE:
        raise ValueError("Database exceeds upload budget")
    db = readonly(source)
    try:
        row = db.execute("SELECT value FROM meta WHERE key='owner'").fetchone()
        if (
            not row
            or not isinstance(owner := row[0], str)
            or len(owner) != 64
            or any(c not in "0123456789abcdef" for c in owner)
        ):
            raise ValueError("Invalid database owner")
        target.mkdir(mode=0o700, parents=True, exist_ok=False)
        path = target / f"{owner}-0.sqlite3"
        snapshot(db, path)
        return [(owner, path)]
    finally:
        db.close()
