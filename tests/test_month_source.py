"""Current-month raw provenance on real isolated SQLite, no network or HA writes."""

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.detail_codec import unpacked
from custom_components.ninebot.raw import Endpoint, build_record
from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure, RideArchive

NOW = datetime(2026, 10, 16, tzinfo=UTC)
SN = "synthetic-month-source"
OWNER = hashlib.sha256(b"synthetic-month-source-account").hexdigest()


def source(*, received=NOW, backend="0.1.7", **extra):
    return build_record(
        Endpoint.TRAVEL,
        {
            "month": "202610",
            "times": 1,
            "total_mileages": 1,
            "ec": 2,
            "list": [
                {
                    "travel_id": "ride-a",
                    "mileages": 1,
                    "duration": 60,
                    "ec": 2,
                    "start_time": int(NOW.timestamp()) - 120,
                    "end_time": int(NOW.timestamp()) - 60,
                }
            ],
            **extra,
        },
        received,
        "202610",
        backend_version=backend,
    )


async def save(store, raw, **kwargs):
    return await store.async_record_month(
        SN,
        travel(raw.payload(), "202610"),
        raw.received_at,
        raw.backend_version,
        raw=raw,
        guard=lambda: True,
        ordered=True,
        **kwargs,
    )


async def inspect(store, sn=SN):
    return await store.async_month_source_check(sn, "202610", guard=lambda: True)


@pytest.fixture
async def store(tmp_path):
    archive = RideArchive(tmp_path / "rides.sqlite3", OWNER)
    await archive.async_open()
    try:
        yield archive
    finally:
        await archive.async_close()


async def test_source_retains_unknown_redacts_secrets_restarts_and_backs_up(store, tmp_path):
    raw = source(password="never-retain", unknown={"future": 42})
    await save(store, raw)
    result = await inspect(store)
    assert result.status == "match" and result.projection_verified
    assert result.raw_payload_integrity_verified and not result.conflicting_fields
    assert result.revision > 0
    with closing(sqlite3.connect(store.path)) as db:
        row = db.execute(
            "SELECT digest,body,observed,backend,endpoint FROM month_sources"
        ).fetchone()
        restored = unpacked(row[1], row[0], NOW, "202610", row[3], row[4], source=Endpoint.TRAVEL)
        assert restored.payload()["unknown"] == {"future": 42}
        assert restored.payload()["password"] == "[redacted]"
        assert db.execute("SELECT count(*) FROM month_sources").fetchone()[0] == 1
    assert (await inspect(store, "foreign-car")).status == "missing"
    backup = tmp_path / "backup.sqlite3"
    await store.async_backup(backup)
    await store.async_close()
    reopened = RideArchive(backup, OWNER)
    try:
        await reopened.async_open()
        assert await inspect(reopened) == result
    finally:
        await reopened.async_close()


async def test_latest_snapshot_replaces_in_place_and_old_or_missing_raw_never_renews_it(store):
    first = source()
    await save(store, first)
    later = source(received=NOW + timedelta(minutes=1), ec=3)
    await save(store, later)
    assert (await inspect(store)).status == "match"
    assert not await save(store, first)  # stale response must not replace current source.
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT count(*) FROM month_sources").fetchone()[0] == 1
        original = db.execute("SELECT digest,body,observed FROM month_sources").fetchone()
    await store.async_record_month(
        SN, travel(later.payload(), "202610"), NOW + timedelta(minutes=2), "0.1.7"
    )
    assert (await inspect(store)).status == "stale"
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT digest,body,observed FROM month_sources").fetchone() == original


@pytest.mark.parametrize("field", ["summary", "ids", "body", "backend"])
async def test_source_checks_distinguish_projection_conflict_corruption_and_unknown_version(
    store, field
):
    await save(store, source(backend="0.1.8" if field == "backend" else "0.1.7"))
    with closing(sqlite3.connect(store.path)) as db, db:
        if field == "summary":
            data = json.loads(db.execute("SELECT data FROM months").fetchone()[0])
            data["summary"]["energy_wh"] = 99
            db.execute("UPDATE months SET data=?", (json.dumps(data),))
        elif field == "ids":
            db.execute('UPDATE months SET ids=\'{"ids":["other"]}\'')
        elif field == "body":
            db.execute("UPDATE month_sources SET body=?", (b"bad-source-original",))
    result = await inspect(store)
    assert not result.projection_verified
    if field in {"summary", "ids"}:
        assert result.status == "conflict" and result.raw_payload_integrity_verified
        assert ("summary.energy_wh" if field == "summary" else "ids") in result.conflicting_fields
    else:
        assert result.status == ("corrupt" if field == "body" else "unsupported")
    assert "ride-a" not in str(result) and "bad-source-original" not in str(result)


@pytest.mark.parametrize("field", ["endpoint", "query_month", "received_at", "backend_version"])
async def test_invalid_source_binding_rolls_back_parent_and_source(store, field):
    raw = source()
    await save(store, raw)
    bad = replace(
        raw,
        **{
            field: {
                "endpoint": Endpoint.STATUS,
                "query_month": "202609",
                "received_at": NOW + timedelta(seconds=1),
                "backend_version": "0.1.8",
            }[field]
        },
    )
    before = await inspect(store)
    with pytest.raises(ArchiveError) as error:
        await store.async_record_month(
            SN,
            travel(raw.payload(), "202610"),
            NOW,
            "0.1.7",
            raw=bad,
            ordered=True,
            guard=lambda: True,
        )
    assert error.value.kind is ArchiveFailure.SCHEMA
    assert await inspect(store) == before


async def test_source_and_projection_rollback_together_on_late_failure_and_lost_owner(
    store, monkeypatch
):
    await save(store, source())
    before = await inspect(store)
    newer = source(received=NOW + timedelta(seconds=1), ec=99)
    with pytest.raises(ArchiveError) as denied:
        await store.async_record_month(
            SN,
            travel(newer.payload(), "202610"),
            newer.received_at,
            "0.1.7",
            raw=newer,
            ordered=True,
            guard=lambda: False,
        )
    assert denied.value.kind is ArchiveFailure.OWNER
    assert await inspect(store) == before

    def fail(*args):
        raise sqlite3.OperationalError("synthetic late failure")

    monkeypatch.setattr(store, "_upsert_ride", fail)
    with pytest.raises(ArchiveError):
        await save(store, newer)
    assert await inspect(store) == before
    assert (await store.async_month(SN, "202610")).travel.summary.energy_wh == 2


async def test_source_capacity_failure_preserves_previous_parent_and_body(store):
    await save(store, source())
    with closing(sqlite3.connect(store.path)) as db:
        current_size = (
            db.execute("PRAGMA page_count").fetchone()[0]
            * db.execute("PRAGMA page_size").fetchone()[0]
        )
    assert await store.async_set_capacity(current_size + 4096, guard=lambda: True)
    before = await inspect(store)
    large = "".join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(6000))
    with pytest.raises(ArchiveError) as error:
        await save(store, source(received=NOW + timedelta(seconds=1), unknown=large))
    assert error.value.kind is ArchiveFailure.CAPACITY
    assert await inspect(store) == before
    assert (await store.async_month(SN, "202610")).travel.summary.energy_wh == 2


async def test_schema5_migration_keeps_original_backup_and_missing_source_explicit(store):
    await save(store, source())
    path = store.path
    await store.async_close()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE month_sources")
        db.execute("PRAGMA user_version=5")
    original = path.read_bytes()
    migrated = RideArchive(path, OWNER)
    try:
        await migrated.async_open()
        assert (await inspect(migrated)).status == "missing"
        assert (await migrated.async_month(SN, "202610")).travel.summary.energy_wh == 2
        copies = list(path.parent.glob("rides.schema5-*.sqlite3"))
        assert len(copies) == 1 and copies[0].read_bytes() == original
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 6
    finally:
        await migrated.async_close()


async def test_schema6_failed_ddl_keeps_schema5_and_recovery_copy(store, monkeypatch):
    from custom_components.ninebot import month_source

    await save(store, source())
    path = store.path
    await store.async_close()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE month_sources")
        db.execute("PRAGMA user_version=5")
    original = path.read_bytes()
    monkeypatch.setattr(month_source, "SCHEMA", (*month_source.SCHEMA, "INVALID DDL"))
    migrated = RideArchive(path, OWNER)
    try:
        with pytest.raises(ArchiveError):
            await migrated.async_open()
        assert path.read_bytes() == original
        assert next(path.parent.glob("rides.schema5-*.sqlite3")).read_bytes() == original
    finally:
        await migrated.async_close()


async def seed_detail(store):
    from custom_components.ninebot.travel import merge_detail, parse_ride

    raw = source()
    await save(store, raw)
    summary = (await store.async_month(SN, "202610")).travel.rides[0]
    body = {**raw.payload()["list"][0], "trail": "120,30,1,1;120.001,30.001,2,2"}
    detail = build_record(
        Endpoint.TRIP_DETAIL, body, NOW + timedelta(seconds=1), "202610", backend_version="0.1.7"
    )
    await store.async_record_detail(
        SN,
        merge_detail(summary, parse_ride(body, "202610", source=Endpoint.TRIP_DETAIL)),
        detail.received_at,
        raw=detail,
    )
    return await store.async_private_detail(SN, "ride-a")


async def test_schema5_missing_detail_recovers_before_schema6_migration(store):
    import shutil

    before = await seed_detail(store)
    await store.async_start_history_audit(SN, NOW + timedelta(seconds=2), guard=lambda: True)
    assert (
        await store.async_step_history_audit(SN, NOW + timedelta(seconds=2), guard=lambda: True)
    ).payloads_verified
    path = store.path
    await store.async_close()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE month_sources")
        db.execute("PRAGMA user_version=5")
    copy = path.with_name("rides.recovery-" + "1" * 32 + ".sqlite3")
    shutil.copyfile(path, copy)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DELETE FROM detail_payloads")
    original = path.read_bytes()
    migrated = RideArchive(path, OWNER)
    try:
        await migrated.async_open()
        after = await migrated.async_private_detail(SN, "ride-a")
        assert after.latest_hash == before.latest_hash and after.latest is not None
        assert (await inspect(migrated)).status == "missing"
        assert list(path.parent.glob("rides.schema5-*.sqlite3"))
        assert any(p.read_bytes() == original for p in path.parent.glob("*.recovery-*.sqlite3"))
    finally:
        await migrated.async_close()


async def test_schema5_in_progress_reconstruction_keeps_old_recovery_point_after_upgrade(store):
    from dataclasses import asdict

    from custom_components.ninebot.ride_archive import vehicle_key

    await seed_detail(store)
    started = await store.async_reconstruction_job(
        SN, "start", NOW + timedelta(seconds=2), guard=lambda: True
    )
    await store._run(lambda: store._reconstruction_backup(vehicle_key(SN), started))
    path = store.path
    copy = path.with_name(f"rides.recovery-{started.job_id}.sqlite3")
    await store.async_close()
    for target in (path, copy):
        with closing(sqlite3.connect(target)) as db, db:
            db.execute("DROP TABLE month_sources")
            db.execute("PRAGMA user_version=5")
    checksum = hashlib.sha256(copy.read_bytes()).hexdigest()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "UPDATE meta SET value=? WHERE key=?",
            (
                json.dumps(asdict(replace(started, backup_hash=checksum))),
                "local_reconstruction:" + vehicle_key(SN),
            ),
        )
    migrated = RideArchive(path, OWNER)
    try:
        await migrated.async_open()
        result = await migrated.async_reconstruction_job(
            SN, "continue", NOW + timedelta(seconds=3), guard=lambda: True
        )
        assert result.state == "complete" and result.checked == 1
        assert result.backup_hash == checksum
        assert hashlib.sha256(copy.read_bytes()).hexdigest() == checksum
    finally:
        await migrated.async_close()
