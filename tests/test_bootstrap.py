"""Full synthetic client-range discovery; no cloud, sleeps or physical commands."""

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.ninebot.bootstrap import HistoryBootstrap
from custom_components.ninebot.history_plan import HistoryState, month_count
from custom_components.ninebot.raw import Endpoint, build_record
from custom_components.ninebot.ride_archive import RideArchive

NOW = datetime(2026, 10, 14, tzinfo=UTC)
SN = "synthetic-bootstrap-vehicle"
OWNER = hashlib.sha256(b"synthetic-bootstrap-account").hexdigest()


@pytest.fixture
async def context(tmp_path, monkeypatch):
    archive = RideArchive(tmp_path / "bootstrap.sqlite3", OWNER)
    await archive.async_open()
    clock = [NOW]
    co = SimpleNamespace(
        config_entry=SimpleNamespace(options={"preserve_history": True, "auto_history_sync": True}),
        _stopping=False,
        _generation=1,
        _ownership={SN: 1},
        local_vehicle_available=lambda sn: sn == SN,
        fresh=lambda sn, group: True,
        statistics=SimpleNamespace(
            archive=archive,
            writable=True,
            async_history_page_committed=AsyncMock(),
            record_error=Mock(),
            _notify_change=Mock(),
        ),
    )
    calls = []

    async def query(sn, month, page):
        calls.append((sn, month, page))
        rows = []
        total = 0
        if month == "202610":
            total = 2
            rows = [
                {
                    "travel_id": f"ride-{page}",
                    "mileages": 1,
                    "duration": 60,
                    "ec": 10,
                    "start_time": int(NOW.timestamp()) - page * 3600,
                    "end_time": int(NOW.timestamp()) - page * 3600 + 60,
                }
            ]
        return build_record(
            Endpoint.TRAVEL,
            {"times": total, "list": rows},
            clock[0],
            month,
            backend_version="0.1.7",
            endpoint_version="0.1.7+native.history.v1",
        )

    co.async_query_history_page = query
    monkeypatch.setattr("custom_components.ninebot.bootstrap.dt_util.utcnow", lambda: clock[0])
    try:
        yield co, archive, clock, calls
    finally:
        await archive.async_close()


async def test_first_scan_really_enumerates_full_client_range_and_preserves_all_pages(context):
    co, archive, clock, calls = context
    runner = HistoryBootstrap(co)
    await runner.async_seed((SN,), NOW)
    root = await archive.async_history_plan(SN)
    assert root.start_month == "200001" and month_count(root.start_month, root.end_month) == 322
    for _ in range(323):
        assert await runner.async_step((SN,), clock[0], 60)
        clock[0] += timedelta(seconds=61)
    root = await archive.async_history_plan(SN)
    assert root.discovery_complete and root.scanned_months == root.verified_months == 322
    assert root.state is HistoryState.PRESERVE  # Details are still missing: no false complete.
    assert len(calls) == 323 and calls[-1] == (SN, "200001", 1)
    assert len((await archive.async_page(SN)).rides) == 2
    assert (
        await archive.async_history_page(SN, root.plan_id, "202610", 2, guard=lambda: True)
    ).payload()["list"][0]["travel_id"] == "ride-2"
    await runner.async_seed((SN,), clock[0])
    assert await archive.async_history_plan(SN) == root
    assert not await runner.async_step((SN,), clock[0], 60)
    assert len(calls) == 323
    from custom_components.ninebot.travel import parse_ride

    for identity in ("ride-1", "ride-2"):
        body = {"travel_id": identity, "trail": "120,30,1,1;120.1,30.1,2,2"}
        raw = build_record(Endpoint.TRIP_DETAIL, body, clock[0], "202610", backend_version="0.1.7")
        await archive.async_record_detail(
            SN,
            parse_ride(body, "202610", source=Endpoint.TRIP_DETAIL),
            clock[0],
            raw=raw,
            guard=lambda: True,
            ordered=True,
        )
    await runner.async_seed((SN,), clock[0])
    finished, health = runner.snapshots[SN]
    assert finished.state is HistoryState.COMPLETE
    assert health.discovered == health.saved == 2 and not health.missing
    assert len(calls) == 323  # Completion verification is entirely local.


async def test_shared_budget_prevents_repeated_poll_and_reload_reuses_checkpoint(context):
    co, archive, clock, calls = context
    runner = HistoryBootstrap(co)
    await runner.async_seed((SN,), NOW)
    assert await runner.async_step((SN,), NOW, 60)
    assert not await runner.async_step((SN,), NOW, 60)
    assert len(calls) == 1
    root = await archive.async_history_plan(SN)
    assert root.next_page == 2
    await HistoryBootstrap(co).async_seed((SN,), NOW)
    assert await archive.async_history_plan(SN) == root


async def test_disabled_or_stale_profiles_do_not_create_or_query_roots(context):
    co, archive, clock, calls = context
    runner = HistoryBootstrap(co)
    co.config_entry.options["auto_history_sync"] = False
    await runner.async_seed((SN,), NOW)
    assert await archive.async_history_plan(SN) is None
    assert not await runner.async_step((SN,), NOW, 60)
    co.config_entry.options["auto_history_sync"] = True
    co.fresh = lambda sn, group: False
    await runner.async_seed((SN,), NOW)
    assert await archive.async_history_plan(SN) is None
    assert not calls


async def test_pause_resume_preserves_checkpoint_and_stops_background_wire(context):
    co, archive, clock, calls = context
    runner = HistoryBootstrap(co)
    await runner.async_seed((SN,), NOW)
    assert await runner.async_step((SN,), NOW, 60)
    checkpoint = await archive.async_history_plan(SN)
    await runner.async_pause(SN, True)
    clock[0] += timedelta(minutes=3)
    await runner.async_seed((SN,), clock[0])
    assert not await runner.async_step((SN,), clock[0], 60)
    assert len(calls) == 1
    assert await archive.async_history_plan(SN) == checkpoint
    await runner.async_pause(SN, False)
    assert await runner.async_step((SN,), clock[0], 60)
    assert len(calls) == 2 and calls[-1][-1] == 2


async def test_incremental_runner_queries_only_registered_month_and_preserves_baseline(context):
    co, archive, clock, calls = context
    runner = HistoryBootstrap(co)
    await runner.async_seed((SN,), NOW)
    baseline = await archive.async_history_plan(SN)
    run = await archive.async_schedule_history_run(SN, "202609", NOW, guard=lambda: True)
    assert await runner.async_step((SN,), NOW, 60, incremental=True)
    assert calls == [(SN, "202609", 1)]
    clock[0] += timedelta(seconds=61)
    assert not await runner.async_step((SN,), clock[0], 60, incremental=True)
    assert (await archive.async_history_run(SN, run.plan_id)).state is HistoryState.COMPLETE
    assert await archive.async_history_plan(SN) == baseline


async def test_incremental_seed_isolates_full_vehicle_queue_and_skips_baseline_month(context):
    from custom_components.ninebot.incremental_sync import IncrementalSync
    from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure

    co, archive, clock, calls = context
    runner = HistoryBootstrap(co)
    co.preservation = SimpleNamespace(bootstrap=runner)
    await runner.async_seed((SN,), NOW)
    sync = IncrementalSync(co)
    with patch.object(
        archive, "async_reconcile_history_month", new_callable=AsyncMock
    ) as reconcile:
        await sync.async_seed((SN,), NOW)
        reconcile.assert_not_awaited()  # First baseline already has this month queued.
        later = datetime(2026, 11, 2, tzinfo=UTC)
        co.local_vehicle_available = lambda sn: True
        co._ownership["other"] = 1
        baseline = await archive.async_history_plan(SN)
        with patch.object(archive, "async_history_plan", AsyncMock(return_value=baseline)):
            reconcile.side_effect = [ArchiveError(ArchiveFailure.BUSY), None]
            await sync.async_seed((SN, "other"), later)
        assert [call.args[:2] for call in reconcile.await_args_list] == [
            (SN, "202611"),
            ("other", "202611"),
        ]
    assert not calls


async def test_month_family_rotation_gives_each_pending_family_a_turn(context):
    from custom_components.ninebot.preservation import HistoryPreservation

    co, archive, clock, calls = context
    worker = HistoryPreservation(co)
    co.preservation = worker
    worker.bootstrap.async_step = AsyncMock(return_value=True)
    worker.incremental.async_step = AsyncMock(return_value=True)
    worker._legacy_month_step = AsyncMock(return_value=True)
    for _ in range(3):
        assert await worker._resume_months((SN,), 30)
    assert worker.bootstrap.async_step.await_count == 1
    assert worker.incremental.async_step.await_count == 1
    assert worker._legacy_month_step.await_count == 1
    assert await archive.async_history_family_turn() == 0


async def test_manual_request_survives_restart_with_automatic_seeding_disabled(context):
    co, archive, clock, calls = context
    co.config_entry.options["auto_history_sync"] = False
    runner = HistoryBootstrap(co)
    await runner.async_seed((SN,), NOW)
    assert await archive.async_history_plan(SN) is None
    assert not await runner.async_step((SN,), NOW, 60)
    await runner.async_request(SN)
    await runner.async_seed((SN,), NOW)
    assert await runner.async_step((SN,), NOW, 60)
    checkpoint = await archive.async_history_plan(SN)
    runner = HistoryBootstrap(co)  # New runtime reconstructs persisted manual permission.
    clock[0] += timedelta(minutes=3)
    await runner.async_seed((SN,), clock[0])
    assert not runner.enabled() and runner.enabled(SN)
    assert await archive.async_history_plan(SN) == checkpoint
    assert await runner.async_step((SN,), clock[0], 60)
    assert calls[-1] == (SN, "202610", 2)
    await runner.async_pause(SN, True)
    clock[0] += timedelta(minutes=3)
    assert not await runner.async_step((SN,), clock[0], 60)
    co.config_entry.options["preserve_history"] = False
    await runner.async_pause(SN, False)
    assert not await runner.async_step((SN,), clock[0], 60)
    assert len(calls) == 2


async def test_cached_progress_only_notifies_changed_scalar_views(context):
    from custom_components.ninebot.preservation import HistoryPreservation

    co, archive, clock, calls = context
    co._authenticated = True
    co.statistics.archive_ready = True
    co.statistics.error = None
    worker = HistoryPreservation(co)
    co.preservation = worker
    await worker.bootstrap.async_seed((SN,), NOW)
    await worker.async_refresh_progress((SN,), NOW)
    assert worker.progress[SN].state == "discovering"
    co.statistics._notify_change.assert_called_once()
    await worker.async_refresh_progress((SN,), NOW + timedelta(seconds=1))
    co.statistics._notify_change.assert_called_once()
    await worker.bootstrap.async_pause(SN, True)
    await worker.async_refresh_progress((SN,), NOW)
    assert worker.progress[SN].state == "paused" and not calls


async def test_local_bad_object_backfill_does_not_block_other_vehicle_seed(context):
    from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure

    co, archive, clock, calls = context
    co.local_vehicle_available = lambda sn: True
    co._ownership["other-vehicle"] = 1
    runner = HistoryBootstrap(co)
    with patch.object(
        archive,
        "async_backfill_detail_health",
        AsyncMock(side_effect=[ArchiveError(ArchiveFailure.OBJECT), 0]),
    ):
        await runner.async_seed((SN, "other-vehicle"), NOW)
    co.statistics.record_error.assert_called_once()
    assert "other-vehicle" in runner.snapshots and not calls
    assert await archive.async_history_plan(SN) is not None
    assert await archive.async_history_plan("other-vehicle") is not None
