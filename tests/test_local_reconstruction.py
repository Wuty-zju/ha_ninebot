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

        def damage_index():
            with archive._db() as db:
                db.execute("UPDATE rides SET started=?", ((NOW - timedelta(days=1)).isoformat(),))

        await archive._run(damage_index)
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


async def test_backup_refuses_active_transaction_without_committing_it(tmp_path):
    archive = await seed(tmp_path)
    try:

        def active():
            db = archive._db()
            db.execute("UPDATE rides SET observed=observed")
            try:
                with pytest.raises(ArchiveError) as err:
                    archive._backup(tmp_path / "active.sqlite3")
                assert err.value.kind is ArchiveFailure.BUSY
                assert db.in_transaction
            finally:
                db.rollback()

        await archive._run(active)
        assert not (tmp_path / "active.sqlite3").exists()
    finally:
        await archive.async_close()


async def test_job_restart_coalesces_and_reuses_one_recovery_point(tmp_path):
    archive = await seed(tmp_path, identified=False)
    await damage(archive)
    started = await archive.async_reconstruction_job(SN, "start", NOW, guard=lambda: True)
    assert (await archive.async_reconstruction_job(SN, "start", NOW, guard=lambda: True)) == started
    first = await archive.async_reconstruction_job(SN, "continue", NOW, guard=lambda: True)
    assert first.checked == first.repaired == 1 and first.blocked == 0
    backups = list(tmp_path.glob("*.recovery-*.sqlite3"))
    assert len(backups) == 1
    await archive.async_close()
    archive = RideArchive(tmp_path / "rides.sqlite3", OWNER)
    await archive.async_open()
    try:
        finished = await archive.async_reconstruction_job(SN, "continue", NOW, guard=lambda: True)
        assert finished.job_id == started.job_id and finished.state == "complete"
        assert finished.checked == 1 and not finished.changed
        assert list(tmp_path.glob("*.recovery-*.sqlite3")) == backups
        assert (await archive.async_private_detail(SN, "ride-a")).ride.distance_m == 2500
    finally:
        await archive.async_close()


async def test_job_pause_cancel_and_scope_change_are_honest(tmp_path):
    archive = await seed(tmp_path)
    try:
        job = await archive.async_reconstruction_job(SN, "start", NOW, guard=lambda: True)
        paused = await archive.async_reconstruction_job(SN, "pause", NOW, guard=lambda: True)
        assert paused.state == "paused"
        assert (
            await archive.async_reconstruction_job(SN, "step", NOW, guard=lambda: True)
        ) == paused
        assert not list(tmp_path.glob("*.recovery-*.sqlite3"))

        # A managed archive update invalidates captured completeness even if
        # all currently selected raw fields agree; never certify changing scope.
        def change_revision():
            with archive._db():
                archive._revision(bump=True)

        await archive._run(change_revision)
        await archive.async_reconstruction_job(SN, "continue", NOW, guard=lambda: True)
        finished = await archive.async_reconstruction_job(SN, "continue", NOW, guard=lambda: True)
        assert finished.job_id == job.job_id and finished.changed and finished.state == "with_gaps"
        new = await archive.async_reconstruction_job(SN, "start", NOW, guard=lambda: True)
        assert new.job_id != job.job_id
        cancelled = await archive.async_reconstruction_job(SN, "cancel", NOW, guard=lambda: True)
        assert cancelled.state == "cancelled"
        assert (
            await archive.async_reconstruction_job(SN, "continue", NOW, guard=lambda: True)
        ) == cancelled
    finally:
        await archive.async_close()


async def test_job_corrupt_backup_never_changes_more_data(tmp_path):
    archive = await seed(tmp_path)
    try:
        job = await archive.async_reconstruction_job(SN, "start", NOW, guard=lambda: True)
        await archive.async_reconstruction_job(SN, "continue", NOW, guard=lambda: True)
        backup = next(tmp_path.glob("*.recovery-*.sqlite3"))
        with backup.open("ab") as stream:
            stream.write(b"modified recovery point")
        blocked = await archive.async_reconstruction_job(SN, "continue", NOW, guard=lambda: True)
        assert blocked.state == "blocked_recovery"
        current = await archive.async_reconstruction_job(SN, "status", NOW, guard=lambda: True)
        assert current.job_id == job.job_id and current.checked == 1
        assert not archive.write_paused
        assert (await archive.async_private_detail(SN, "ride-a")).latest is not None
    finally:
        await archive.async_close()


async def test_job_records_corrupt_original_gap_without_deleting_it(tmp_path):
    archive = await seed(tmp_path)
    try:

        def corrupt():
            with archive._db() as db:
                db.execute("UPDATE detail_payloads SET body=?", (b"bad-original",))

        await archive._run(corrupt)
        await archive.async_reconstruction_job(SN, "start", NOW, guard=lambda: True)
        first = await archive.async_reconstruction_job(SN, "continue", NOW, guard=lambda: True)
        assert first.blocked == 1 and first.repaired == 0
        done = await archive.async_reconstruction_job(SN, "continue", NOW, guard=lambda: True)
        assert done.state == "with_gaps"
        assert (
            await archive._run(
                lambda: archive._db().execute("SELECT body FROM detail_payloads").fetchone()[0]
            )
            == b"bad-original"
        )
    finally:
        await archive.async_close()


async def test_two_repairs_use_one_backup_and_failed_transaction_keeps_checkpoint(
    tmp_path, monkeypatch
):
    archive = RideArchive(tmp_path / "rides.sqlite3", OWNER)
    await archive.async_open()
    try:
        second = {
            **ROW,
            "travel_id": "ride-b",
            "start_time": int(NOW.timestamp()) + 1000,
            "end_time": int(NOW.timestamp()) + 1600,
        }
        month = travel({"times": 2, "list": [ROW, second]}, "202610")
        await archive.async_record_month(SN, month, NOW)
        for index, payload in enumerate(({**ROW, "mileages": 2.5}, second)):
            raw = build_record(Endpoint.TRIP_DETAIL, payload, NOW + timedelta(seconds=1), "202610")
            ride = merge_detail(
                month.rides[index], parse_ride(payload, "202610", source=Endpoint.TRIP_DETAIL)
            )
            await archive.async_record_detail(SN, ride, raw.received_at, raw=raw)
        await damage(archive)
        await archive.async_reconstruction_job(SN, "start", NOW, guard=lambda: True)
        original = archive._upsert_ride

        def fail_after_write(*args, **kwargs):
            original(*args, **kwargs)
            raise ArchiveError(ArchiveFailure.STORAGE)

        monkeypatch.setattr(archive, "_upsert_ride", fail_after_write)
        with pytest.raises(ArchiveError):
            await archive.async_reconstruction_job(SN, "continue", NOW, guard=lambda: True)
        status = await archive.async_reconstruction_job(SN, "status", NOW, guard=lambda: True)
        assert status.checked == 0 and status.state == "pending_backup"
        assert (await archive.async_private_detail(SN, "ride-a")).ride.distance_m == 999
        monkeypatch.setattr(archive, "_upsert_ride", original)
        first = await archive.async_reconstruction_job(SN, "continue", NOW, guard=lambda: True)
        second_step = await archive.async_reconstruction_job(
            SN, "continue", NOW, guard=lambda: True
        )
        assert first.checked == 1 and second_step.checked == 2
        assert second_step.repaired == 1  # The already-correct second ride remains unchanged.
        assert len(list(tmp_path.glob("*.recovery-*.sqlite3"))) == 1
    finally:
        await archive.async_close()


async def test_projection_failure_keeps_publication_pending_for_retry():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from custom_components.ninebot.background_reconstruction import BackgroundReconstruction
    from custom_components.ninebot.reconstruction_job import ReconstructionJob

    statistics = SimpleNamespace(
        async_reconstruction_committed=AsyncMock(
            side_effect=[ArchiveError(ArchiveFailure.BUSY), None]
        ),
        _notify_change=Mock(),
        record_error=Mock(),
    )
    co = SimpleNamespace(
        statistics=statistics,
        preservation=SimpleNamespace(
            bootstrap=SimpleNamespace(guard=lambda *args, **kwargs: lambda: True),
        ),
    )
    publisher = BackgroundReconstruction(co)
    job = ReconstructionJob(
        "a" * 32,
        "ride-a",
        1,
        2,
        0,
        NOW.isoformat(),
        NOW.isoformat(),
        state="running",
        checked=1,
        repaired=1,
        backup_hash="b" * 64,
    )
    with pytest.raises(ArchiveError):
        await publisher.publish(SN, job)
    assert SN not in publisher.views
    await publisher.publish(SN, job)
    assert publisher.views[SN] == job
    assert statistics.async_reconstruction_committed.await_count == 2
    statistics._notify_change.assert_called_once()
