"""Synthetic local recovery evidence, real SQLite, no cloud or production HA."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.history_plan import HistoryPlan
from custom_components.ninebot.raw import Endpoint, build_record
from custom_components.ninebot.ride_archive import (
    ArchiveError,
    ArchiveFailure,
    RideArchive,
    vehicle_key,
)
from custom_components.ninebot.travel import merge_detail, parse_ride

NOW = datetime(2026, 10, 14, tzinfo=UTC)
SN = "synthetic-recovery-vehicle"
OWNER = hashlib.sha256(b"synthetic-recovery-owner").hexdigest()
ROW = {
    "travel_id": "ride-a",
    "start_time": int(NOW.timestamp()) - 600,
    "end_time": int(NOW.timestamp()),
    "mileages": 2,
    "duration": 600,
    "ec": 40,
}


async def seed(tmp_path, *, identified=True, witness=True):
    archive = RideArchive(tmp_path / "rides.sqlite3", OWNER)
    await archive.async_open()
    month = travel({"times": 1, "list": [ROW]}, "202610")
    await archive.async_record_month(SN, month, NOW)
    if witness:
        root = HistoryPlan.create("a" * 32, vehicle_key(SN), "202610", "202610", NOW.isoformat())
        await archive.async_set_history_plan(SN, root, None, guard=lambda: True)
        claimed = await archive.async_claim_history_page(SN, NOW, 30, guard=lambda: True)
        await archive.async_accept_history_page(
            SN,
            claimed,
            build_record(
                Endpoint.TRAVEL,
                {"times": 1, "list": [ROW]},
                NOW,
                "202610",
                backend_version="0.1.7",
                endpoint_version=root.provider,
            ),
            guard=lambda: True,
        )
    payload = {**ROW, "mileages": 2.5, "ec": 50, "trail": "120,30,1;120.001,30.001,2"}
    if not identified:
        payload.pop("travel_id")
    detail = parse_ride(payload, "202610", source=Endpoint.TRIP_DETAIL)
    ride = merge_detail(month.rides[0], detail)
    raw = build_record(Endpoint.TRIP_DETAIL, payload, NOW + timedelta(seconds=1), "202610")
    await archive.async_record_detail(SN, ride, raw.received_at, raw=raw)
    return archive


async def damage(archive, *, field="distance_m", value=999):
    def edit():
        db = archive._db()
        row = db.execute("SELECT data FROM rides WHERE id='ride-a'").fetchone()
        data = json.loads(row[0])
        data[field] = value
        with db:
            db.execute("UPDATE rides SET data=? WHERE id='ride-a'", (json.dumps(data),))

    await archive._run(edit)


@pytest.mark.parametrize("identified", [True, False])
async def test_correct_scalar_only_after_backup_preserve_raw_and_revision(tmp_path, identified):
    archive = await seed(tmp_path, identified=identified)
    try:
        await damage(archive)
        before = await archive.async_private_detail(SN, "ride-a")
        result = await archive.async_reconstruct_ride(SN, "ride-a", guard=lambda: True)
        assert result.status == "repairable" and result.fields == ("distance_m",)
        assert not result.repaired and not list(tmp_path.glob("*.recovery-*.sqlite3"))
        repaired = await archive.async_reconstruct_ride(
            SN, "ride-a", guard=lambda: True, repair=True
        )
        assert repaired.repaired
        after = await archive.async_private_detail(SN, "ride-a")
        assert after.ride.distance_m == 2500
        assert (
            after.latest_hash == before.latest_hash and after.geometry_hash == before.geometry_hash
        )
        assert after.latest.received_at == before.latest.received_at
        assert after.scalar_revision > before.scalar_revision
        backups = list(tmp_path.glob("*.recovery-*.sqlite3"))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as db:
            assert (
                json.loads(db.execute("SELECT data FROM rides").fetchone()[0])["distance_m"] == 999
            )
        assert (
            await archive.async_reconstruct_ride(SN, "ride-a", guard=lambda: True, repair=True)
        ).status == "healthy"
        assert len(list(tmp_path.glob("*.recovery-*.sqlite3"))) == 1
    finally:
        await archive.async_close()


async def test_idless_requires_independent_page_not_normalized_start_guess(tmp_path):
    archive = await seed(tmp_path, identified=False, witness=False)
    try:
        await damage(archive)
        result = await archive.async_reconstruct_ride(SN, "ride-a", guard=lambda: True, repair=True)
        assert result.status == "unverifiable" and not result.repaired
        assert (await archive.async_private_detail(SN, "ride-a")).ride.distance_m == 999
    finally:
        await archive.async_close()


async def test_never_restore_older_payload_over_newer_index(tmp_path):
    archive = await seed(tmp_path)
    try:
        await archive._run(
            lambda: archive._db().execute(
                "UPDATE rides SET observed=?", ((NOW + timedelta(seconds=20)).isoformat(),)
            )
        )
        result = await archive.async_reconstruct_ride(SN, "ride-a", guard=lambda: True, repair=True)
        assert result.status == "stale" and not result.repaired
    finally:
        await archive.async_close()


async def test_corrupt_payload_preserved_and_no_silent_cloud_repair(tmp_path):
    archive = await seed(tmp_path)
    try:

        def corrupt():
            with archive._db() as db:
                db.execute("UPDATE detail_payloads SET body=?", (b"bad-original",))

        await archive._run(corrupt)
        result = await archive.async_reconstruct_ride(SN, "ride-a", guard=lambda: True, repair=True)
        assert result.status == "corrupt" and not result.repaired
        assert (
            await archive._run(
                lambda: archive._db().execute("SELECT body FROM detail_payloads").fetchone()[0]
            )
            == b"bad-original"
        )
    finally:
        await archive.async_close()


async def test_ownership_revoked_during_backup_prevents_commit(tmp_path, monkeypatch):
    archive = await seed(tmp_path)
    permitted = True
    try:
        await damage(archive)
        original = archive._backup

        def backup(destination, *, recovery=False):
            nonlocal permitted
            original(destination, recovery=recovery)
            permitted = False

        monkeypatch.setattr(archive, "_backup", backup)
        with pytest.raises(ArchiveError) as err:
            await archive.async_reconstruct_ride(SN, "ride-a", guard=lambda: permitted, repair=True)
        assert err.value.kind is ArchiveFailure.OWNER
        assert (await archive.async_private_detail(SN, "ride-a")).ride.distance_m == 999
    finally:
        await archive.async_close()


async def test_backup_failure_preserves_index(tmp_path, monkeypatch):
    archive = await seed(tmp_path)
    try:
        await damage(archive)

        def failed(destination, *, recovery=False):
            raise ArchiveError(ArchiveFailure.STORAGE)

        monkeypatch.setattr(archive, "_backup", failed)
        with pytest.raises(ArchiveError) as err:
            await archive.async_reconstruct_ride(SN, "ride-a", guard=lambda: True, repair=True)
        assert err.value.kind is ArchiveFailure.STORAGE
        assert (await archive.async_private_detail(SN, "ride-a")).ride.distance_m == 999
    finally:
        await archive.async_close()


async def test_missing_detail_field_not_certified_or_erased(tmp_path):
    archive = await seed(tmp_path)
    try:
        # Detail never reported server_average_speed_raw; retain old fact, but
        # explicitly exclude it from fields verified against the latest raw.
        await damage(archive, field="server_average_speed_raw", value=42)
        result = await archive.async_reconstruct_ride(SN, "ride-a", guard=lambda: True, repair=True)
        assert result.status == "healthy" and not result.repaired
        assert "server_average_speed_raw" not in result.checked_fields
        assert (
            await archive.async_private_detail(SN, "ride-a")
        ).ride.server_average_speed_raw == 42
    finally:
        await archive.async_close()


async def test_time_index_rebuilt_and_recovery_copy_keeps_bad_index(tmp_path):
    archive = await seed(tmp_path)
    try:
        await archive._run(
            lambda: archive._db().execute(
                "UPDATE rides SET started=?", ((NOW - timedelta(days=1)).isoformat(),)
            )
        )
        result = await archive.async_reconstruct_ride(SN, "ride-a", guard=lambda: True, repair=True)
        assert result.repaired and result.fields == ("time_index",)
        await archive._run(lambda: archive._check(archive._db()))
        with sqlite3.connect(next(tmp_path.glob("*.recovery-*.sqlite3"))) as db:
            assert (
                db.execute("SELECT started FROM rides").fetchone()[0]
                == (NOW - timedelta(days=1)).isoformat()
            )
    finally:
        await archive.async_close()
