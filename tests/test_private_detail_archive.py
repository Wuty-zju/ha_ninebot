"""Private synthetic detail persistence; no cloud or real GPS samples."""

import hashlib
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.raw import Endpoint, build_record
from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure, RideArchive
from custom_components.ninebot.travel import merge_detail, parse_ride

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
        assert first.latest.payload()["token"] == "[redacted]"
        assert first.latest.payload()["account"] == "[redacted]"
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
        db.execute("DROP TABLE history_payloads")
        db.execute("DROP TABLE history_ids")
        db.execute("DROP TABLE history_months")
        db.execute("DROP TABLE history_pages")
        db.execute("DROP TABLE history_roots")
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


async def test_private_payload_hash_corruption_is_refused_on_read(tmp_path):
    archive = await opened(tmp_path)
    try:
        await save(archive, {"trail": "120,30,1,1"}, NOW + timedelta(seconds=1))
    finally:
        await archive.async_close()
    with closing(sqlite3.connect(archive.path)) as db, db:
        db.execute("UPDATE detail_payloads SET body=?", (b"not-a-zlib-stream",))
    reopened = RideArchive(archive.path, OWNER)
    try:
        await reopened.async_open()
        with pytest.raises(ArchiveError) as err:
            await reopened.async_private_detail(SN, "first")
        assert err.value.kind is ArchiveFailure.OBJECT
    finally:
        await reopened.async_close()


async def test_rejected_private_owner_guard_and_stale_receipt_do_not_write(tmp_path):
    archive = await opened(tmp_path)
    try:
        at = NOW + timedelta(seconds=2)
        await save(archive, {"trail": "120,30,1,1", "mileages": 2}, at)
        first = await archive.async_private_detail(SN, "first")
        await save(archive, {"trail": "120,30,1,1", "mileages": 1}, NOW + timedelta(seconds=1))
        after = await archive.async_private_detail(SN, "first")
        assert after.latest_hash == first.latest_hash and after.ride.distance_m == 2000
        summary = (await archive.async_month(SN, "202609")).travel.rides[0]
        with pytest.raises(ArchiveError) as err:
            await archive.async_record_detail(
                SN,
                summary,
                at + timedelta(seconds=1),
                guard=lambda: False,
                raw=record({"trail": "120,30,1,1"}, at + timedelta(seconds=1)),
            )
        assert err.value.kind is ArchiveFailure.OWNER
        assert (await archive.async_private_detail(SN, "first")).revision == first.revision
    finally:
        await archive.async_close()


async def test_targeted_summary_retained_partial_withdrawn_complete_and_status(tmp_path):
    archive = await opened(tmp_path)
    try:
        partial = travel({"times": 2, "list": [{"travel_id": "second", "mileages": 1}]}, "202609")
        await archive.async_record_month(SN, partial, NOW + timedelta(seconds=1))
        summary = await archive.async_detail_summary(SN, "first", "202609", guard=lambda: True)
        assert summary.detail_id == "first" and summary.query_month == "202609"
        assert (
            await archive.async_detail_summary("foreign", "first", "202609", guard=lambda: True)
            is None
        )
        assert await archive.async_detail_summary(SN, "first", "202608", guard=lambda: True) is None
        with pytest.raises(ArchiveError):
            await archive.async_detail_summary(SN, "first", "202609", guard=lambda: False)
        assert not await archive.async_saved_detail_ids(SN, ("first", "second"))
        raw = record({"trail": "120,30,1,1", "mileages": 1.2}, NOW + timedelta(seconds=2))
        await archive.async_record_detail(SN, summary, raw.received_at, raw=raw)
        assert await archive.async_saved_detail_ids(SN, ("first", "second")) == {"first"}
        assert not await archive.async_saved_detail_ids("foreign", ("first",))
        assert not await archive.async_saved_detail_ids(SN, ())
        with pytest.raises(ValueError):
            await archive.async_saved_detail_ids(SN, ("first",) * 101)
        complete = travel({"times": 1, "list": [{"travel_id": "second"}]}, "202609")
        await archive.async_record_month(SN, complete, NOW + timedelta(seconds=3))
        assert await archive.async_detail_summary(SN, "first", "202609", guard=lambda: True) is None
        assert (
            await archive.async_detail_summary(SN, "second", "202609", guard=lambda: True)
            is not None
        )
    finally:
        await archive.async_close()


async def test_targeted_summary_keeps_conflicting_mapping_unusable(tmp_path):
    archive = await opened(tmp_path)
    try:
        conflicting = travel(
            {"times": 3, "list": [{"travel_id": "first", "detail_id": "other"}]}, "202609"
        )
        await archive.async_record_month(SN, conflicting, NOW + timedelta(seconds=1))
        found = await archive.async_detail_summary(SN, "first", "202609", guard=lambda: True)
        assert found.detail_id is None
        assert "conflicting_detail_ids" in found.issues
    finally:
        await archive.async_close()


async def test_bad_object_does_not_destroy_other_details_or_block_healthy_writes(tmp_path):
    archive = await opened(tmp_path)
    at = NOW + timedelta(seconds=1)
    await save(archive, {"trail": "120,30,1,1"}, at)
    month = await archive.async_month(SN, "202609")
    second = next(ride for ride in month.travel.rides if ride.ride_id == "second")
    raw = record({"travel_id": "second", "trail": "121,31,1,1"}, at)
    await archive.async_record_detail(
        SN,
        merge_detail(second, parse_ride(raw.payload(), "202609", source=Endpoint.TRIP_DETAIL)),
        at,
        raw=raw,
    )
    await archive.async_close()
    damaged = b"corrupt-object-keep-original-bytes"
    with closing(sqlite3.connect(archive.path)) as db, db:
        db.execute("UPDATE detail_payloads SET body=? WHERE ride='first'", (damaged,))
    reopened = RideArchive(archive.path, OWNER)
    try:
        await reopened.async_open()
        with pytest.raises(ArchiveError) as error:
            await reopened.async_private_detail(SN, "first")
        assert error.value.kind is ArchiveFailure.OBJECT
        assert not reopened.write_paused
        assert (await reopened.async_private_detail(SN, "second")).geometry.payload()[
            "trail"
        ] == "121,31,1,1"
        assert await reopened.async_record_month("other-vehicle", sample(), at)
        assert await reopened.async_month("other-vehicle", "202609") is not None
        with closing(sqlite3.connect(archive.path)) as db:
            assert (
                db.execute("SELECT body FROM detail_payloads WHERE ride='first'").fetchone()[0]
                == damaged
            )
    finally:
        await reopened.async_close()


@pytest.mark.parametrize("role", ["latest", "selected_geometry"])
async def test_partial_view_preserves_independent_payloads_scalars_and_damaged_bytes(
    tmp_path, role
):
    archive = await opened(tmp_path)
    try:
        await save(
            archive,
            {
                "trail": "120,30,1,1;120.01,30.01,2,2;120.02,30.02,3,3",
                "mileages": 2,
                "is_show_simple_point": False,
            },
            NOW + timedelta(seconds=1),
        )
        await save(
            archive,
            {"trail": "120,30,0,0", "mileages": 3, "is_show_simple_point": True},
            NOW + timedelta(seconds=2),
        )
        before = await archive.async_private_detail(SN, "first")
        assert before.latest_hash != before.geometry_hash
        digest = before.latest_hash if role == "latest" else before.geometry_hash
        damaged = b"forensic-original-do-not-delete"
        with closing(sqlite3.connect(archive.path)) as db, db:
            db.execute("UPDATE detail_payloads SET body=? WHERE hash=?", (damaged, digest))
        with pytest.raises(ArchiveError) as denied:
            await archive.async_private_detail(SN, "first")
        assert denied.value.kind is ArchiveFailure.OBJECT
        partial = await archive.async_private_detail(SN, "first", allow_partial=True, max_points=1)
        assert (
            partial.ride.distance_m == 3000
        )  # Independent normalized scalar snapshot remains current.
        assert partial.faults == (f"{role}_payload_unavailable",)
        if role == "latest":
            assert partial.latest is None and partial.geometry is not None
            assert partial.geometry.received_at == NOW + timedelta(seconds=1)
            assert partial.ride.total_track_points == 3
            assert partial.geometry_source == "selected_geometry"
        else:
            assert partial.geometry is None and partial.latest is not None
            assert partial.latest.received_at == NOW + timedelta(seconds=2)
            assert partial.ride.total_track_points == 1
            assert partial.geometry_source == "latest_payload"
            assert partial.ride.geometry_selection == "fallback_latest_payload"
        # A tolerant read does not license a writer to erase the damaged original.
        with pytest.raises(ArchiveError) as blocked:
            await save(archive, {"trail": "120,30,1,1", "mileages": 4}, NOW + timedelta(seconds=3))
        assert blocked.value.kind is ArchiveFailure.OBJECT
        again = await archive.async_private_detail(SN, "first", allow_partial=True)
        assert again.ride.distance_m == 3000 and again.latest_hash == before.latest_hash
        with closing(sqlite3.connect(archive.path)) as db:
            assert (
                db.execute("SELECT body FROM detail_payloads WHERE hash=?", (digest,)).fetchone()[0]
                == damaged
            )
    finally:
        await archive.async_close()
    reopened = RideArchive(archive.path, OWNER)
    try:
        await reopened.async_open()
        found = await reopened.async_private_detail(SN, "first", allow_partial=True)
        assert found.faults == (f"{role}_payload_unavailable",) and found.ride.distance_m == 3000
    finally:
        await reopened.async_close()


async def test_partial_view_refuses_two_bad_roles_and_does_not_hide_invalid_scope(tmp_path):
    archive = await opened(tmp_path)
    try:
        await save(archive, {"trail": "120,30,1,1"}, NOW + timedelta(seconds=1))
        with closing(sqlite3.connect(archive.path)) as db, db:
            db.execute("UPDATE detail_payloads SET body=?", (b"unrecoverable-single-object",))
        with pytest.raises(ArchiveError) as bad:
            await archive.async_private_detail(SN, "first", allow_partial=True)
        assert bad.value.kind is ArchiveFailure.OBJECT
        assert (
            await archive.async_private_detail("foreign-vehicle", "first", allow_partial=True)
            is None
        )
        with closing(sqlite3.connect(archive.path)) as db, db:
            db.execute("UPDATE detail_payloads SET month='202608'")
        with pytest.raises(ArchiveError) as invalid:
            await archive.async_private_detail(SN, "first", allow_partial=True)
        assert invalid.value.kind is ArchiveFailure.SCHEMA
    finally:
        await archive.async_close()
