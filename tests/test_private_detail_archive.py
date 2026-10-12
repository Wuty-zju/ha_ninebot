"""Private synthetic detail persistence; no cloud or real GPS samples."""

import hashlib
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ninebot.raw import Endpoint, build_record
from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure, RideArchive
from custom_components.ninebot.travel import merge_detail, parse_ride
from custom_components.ninebot.adapters import travel

NOW = datetime(2026, 9, 26, tzinfo=UTC)
OWNER = hashlib.sha256(b"synthetic-account").hexdigest()
SN = "synthetic-vehicle"


def sample():
    return travel(
        {
            "month": "202609",
            "times": 2,
            "total_mileages": 2.4,
            "ec": 240,
            "duration": 1200,
            "list": [
                {
                    "travel_id": identity,
                    "mileages": 1.2,
                    "duration": 600,
                    "ec": 120,
                    "start_time": int(NOW.timestamp()) + index * 1000,
                    "end_time": int(NOW.timestamp()) + index * 1000 + 600,
                }
                for index, identity in enumerate(("first", "second"))
            ],
        },
        "202609",
    )


def record(raw, at=NOW):
    return build_record(Endpoint.TRIP_DETAIL, raw, at, "202609", backend_version="0.1.7")


async def opened(tmp_path):
    archive = RideArchive(tmp_path / "rides.sqlite3", OWNER)
    await archive.async_open()
    await archive.async_record_month(SN, sample(), NOW)
    return archive


async def save(archive, raw, at):
    month = await archive.async_month(SN, "202609")
    summary = next(ride for ride in month.travel.rides if ride.ride_id == "first")
    evidence = record(raw, at)
    ride = merge_detail(summary, parse_ride(raw, "202609", source=Endpoint.TRIP_DETAIL))
    return await archive.async_record_detail(SN, ride, at, raw=evidence)


async def test_full_private_payload_restart_and_independent_geometry(tmp_path):
    archive = await opened(tmp_path)
    trail = ";".join(f"{120 + i / 100000},30,{i},1" for i in range(2501))
    raw = {
        "trail": trail,
        "mileages": 2,
        "duration": 620,
        "ec": 140,
        "is_show_simple_point": False,
        "unknown_extra": {"value": [1, 2]},
        "token": "must-not-persist",
        "account": "must-not-persist",
    }
    try:
        await save(archive, raw, NOW + timedelta(seconds=1))
        first = await archive.async_private_detail(SN, "first", max_points=1)
        assert len(first.ride.track_points) == 1 and first.ride.track_truncated
        assert first.ride.track_quality.valid_point_count == 2501
        assert first.geometry.payload()["trail"] == trail
        assert first.latest.payload()["unknown_extra"] == {"value": [1, 2]}
        assert "token" not in first.latest.payload() and "account" not in first.latest.payload()
        await save(archive, raw, NOW + timedelta(seconds=2))
        equal = await archive.async_private_detail(SN, "first")
        assert equal.revision == first.revision
        assert equal.latest.received_at == NOW + timedelta(seconds=2)
        await save(
            archive,
            {
                "trail": "120,30,0,0;121,31,0,0",
                "is_show_simple_point": True,
                "mileages": 3,
                "duration": 630,
                "ec": 160,
            },
            NOW + timedelta(seconds=3),
        )
        mixed = await archive.async_private_detail(SN, "first")
        assert mixed.geometry_hash == first.geometry_hash != mixed.latest_hash
        assert mixed.ride.distance_m == 3000 and mixed.ride.energy_raw == 160
        assert mixed.ride.track_quality.valid_point_count == 2501
        backup = tmp_path / "backup.sqlite3"
        await archive.async_backup(backup)
    finally:
        await archive.async_close()
    for path in (tmp_path / "rides.sqlite3", backup):
        restored = RideArchive(path, OWNER)
        try:
            await restored.async_open()
            found = await restored.async_private_detail(SN, "first", max_points=2)
            assert found.geometry.payload()["trail"] == trail
            assert found.ride.distance_m == 3000
            assert len(found.ride.track_points) == 2
            assert await restored.async_private_detail("foreign-vehicle", "first") is None
        finally:
            await restored.async_close()
    with closing(sqlite3.connect(tmp_path / "rides.sqlite3")) as db:
        assert db.execute("SELECT count(*) FROM detail_payloads").fetchone()[0] == 2


async def test_v1_migration_preserves_legacy_and_recovery_copy(tmp_path):
    archive = await opened(tmp_path)
    path = archive.path
    await archive.async_close()
    # Construct the exact previous schema using our scalar-only baseline.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE details")
        db.execute("DROP TABLE detail_payloads")
        db.execute("PRAGMA user_version=1")
    original = path.read_bytes()
    migrated = RideArchive(path, OWNER)
    try:
        await migrated.async_open()
        assert len((await migrated.async_month(SN, "202609")).travel.rides) == 2
        assert await migrated.async_private_detail(SN, "first") is None
        copies = list(tmp_path.glob("rides.schema1-*.sqlite3"))
        assert len(copies) == 1
        with closing(sqlite3.connect(copies[0])) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM rides").fetchone()[0] == 2
        assert original != path.read_bytes()
    finally:
        await migrated.async_close()


async def test_private_receipt_rollback_backup_gate_and_bad_owner(tmp_path):
    archive = await opened(tmp_path)
    try:
        summary = (await archive.async_month(SN, "202609")).travel.rides[0]
        with pytest.raises(ArchiveError):
            await archive.async_record_detail(
                SN,
                summary,
                NOW + timedelta(seconds=1),
                raw=record({"start_time": int(NOW.timestamp()) + 1}, NOW + timedelta(seconds=1)),
            )
        assert await archive.async_private_detail(SN, "first") is None
        await archive.async_prepare_backup()
        with pytest.raises(ArchiveError) as err:
            await save(archive, {"trail": "120,30,1,1"}, NOW + timedelta(seconds=2))
        assert err.value.kind is ArchiveFailure.BUSY
        archive.resume_after_backup()
        await save(archive, {"trail": "120,30,1,1"}, NOW + timedelta(seconds=3))
    finally:
        await archive.async_close()
    foreign = RideArchive(archive.path, hashlib.sha256(b"other").hexdigest())
    try:
        with pytest.raises(ArchiveError) as err:
            await foreign.async_open()
        assert err.value.kind is ArchiveFailure.OWNER
    finally:
        await foreign.async_close()
