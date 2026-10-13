"""Persistent checkpoints and bounded missing-month Actions, synthetic data only."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from custom_components.ninebot.exceptions import ErrorKind, NinebotError
from custom_components.ninebot.sync_job import SyncJob, SyncState, job_data, restored_job

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
NOW = datetime(2026, 10, 8, tzinfo=UTC)
SN = "SyntheticSN"


@pytest.fixture
async def sync_device(hass, entry, app_client, freezer):
    freezer.move_to(NOW)

    def month(sn, query):
        return {"month": query, "times": 2, "list": [{"travel_id": f"{query}-ride"}]}

    app_client.async_get_travel.side_effect = month
    with patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    app_client.async_get_travel.reset_mock()
    return next(
        d.id
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if ("ninebot", SN) in d.identifiers
    )


async def call(hass, device, **data):
    return await hass.services.async_call(
        "ninebot",
        "sync_history",
        {"device_id": device, **data},
        blocking=True,
        return_response=True,
    )


async def test_bounded_missing_only_restart_and_truthful_coverage(
    hass, entry, app_client, sync_device
):
    assert hass.services.supports_response("ninebot", "sync_history") is SupportsResponse.ONLY
    first = await call(hass, sync_device, start_month="202604", end_month="202610")
    assert first["processed_months"] == 4 and first["next_month"] == "202606"
    assert first["queried_months"] == 3 and first["locally_skipped_months"] == 1
    assert app_client.async_get_travel.await_count == 3
    job_id = first["job_id"]
    app_client.async_get_travel.reset_mock()
    co = entry.runtime_data.coordinator
    co._authenticated = False
    assert (await call(hass, sync_device, operation="status"))["job_id"] == job_id
    app_client.async_get_travel.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)
    with patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    app_client.async_get_travel.reset_mock()
    status = await call(hass, sync_device, operation="status", job_id=job_id)
    assert status["next_month"] == "202606" and status["revision"] == first["revision"]
    final = await call(hass, sync_device, operation="continue", job_id=job_id)
    assert final["state"] == "complete" and final["processed_months"] == 7
    assert final["queried_months"] == 6 and not final["all_rides_complete"]
    assert len(final["incomplete_months"]) == 7  # Returned 1 of reported 2, not cloud pagination.
    assert app_client.async_get_travel.await_count == 3
    app_client.async_get_travel.reset_mock()
    repeat = await call(hass, sync_device, start_month="202604", end_month="202610")
    assert repeat["state"] == "complete" and repeat["locally_skipped_months"] == 7
    assert repeat["queried_months"] == 0 and repeat["job_id"] != job_id
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_get_trip_detail.assert_not_awaited()
    app_client.async_control.assert_not_awaited()
    assert not any(x in json.dumps(final) for x in (SN, "latitude", "token", "password"))


async def test_failed_query_keeps_checkpoint_cooldown_and_resumes(
    hass, entry, app_client, sync_device, freezer
):
    original = app_client.async_get_travel.side_effect
    app_client.async_get_travel.side_effect = NinebotError(ErrorKind.CONNECTION)
    first = await call(hass, sync_device, start_month="202609", end_month="202609")
    assert first["state"] == "waiting" and first["reason"] == "connection"
    assert first["processed_months"] == 0 and first["next_month"] == "202609"
    assert first["retry_after_s"] == 60
    app_client.async_get_travel.reset_mock()
    second = await call(hass, sync_device, operation="continue", job_id=first["job_id"])
    assert second["revision"] == first["revision"]
    app_client.async_get_travel.assert_not_awaited()
    freezer.tick(timedelta(seconds=61))
    app_client.async_get_travel.side_effect = original
    final = await call(hass, sync_device, operation="continue", job_id=first["job_id"])
    assert final["state"] == "complete" and final["queried_months"] == 1
    assert app_client.async_get_travel.await_count == 1


async def test_busy_scope_cancel_and_query_interruption(hass, entry, app_client, sync_device):
    co = entry.runtime_data.coordinator
    started = await co.history_sync.async_start(SN, "202604", "202609")
    for data, key in (
        ({"start_month": "202603", "end_month": "202609"}, "sync_conflict"),
        ({"operation": "continue"}, "sync_job"),
        ({"operation": "cancel", "job_id": "0" * 32}, "sync_job"),
    ):
        with pytest.raises(HomeAssistantError) as err:
            await call(hass, sync_device, **data)
        assert err.value.translation_key == key
    with pytest.raises(HomeAssistantError):
        await co.history_sync.async_job("other", started.job_id)
    entered = asyncio.Event()

    async def blocked(sn, month):
        entered.set()
        await asyncio.Event().wait()

    app_client.async_get_travel.side_effect = blocked
    task = asyncio.create_task(co.history_sync.async_advance(SN, started.job_id))
    await entered.wait()
    with pytest.raises(HomeAssistantError) as err:
        await co.history_sync.async_advance(SN, started.job_id)
    assert err.value.translation_key == "busy"
    cancelled = await call(hass, sync_device, operation="cancel", job_id=started.job_id)
    assert cancelled["state"] == "cancelled" and cancelled["processed_months"] == 0
    assert task.cancelled() and co.history_sync._task is None
    assert await co.statistics.async_cached_month(SN, "202609") is None
    app_client.async_get_travel.reset_mock()
    assert (await call(hass, sync_device, operation="continue", job_id=started.job_id))[
        "state"
    ] == "cancelled"
    app_client.async_get_travel.assert_not_awaited()


async def test_unload_preserves_claim_and_resumes_after_restart(
    hass, entry, app_client, sync_device
):
    co = entry.runtime_data.coordinator
    job = await co.history_sync.async_start(SN, "202609", "202609")
    entered = asyncio.Event()
    original = app_client.async_get_travel.side_effect

    async def blocked(sn, month):
        entered.set()
        await asyncio.Event().wait()

    app_client.async_get_travel.side_effect = blocked
    task = asyncio.create_task(co.history_sync.async_advance(SN, job.job_id))
    await entered.wait()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert task.cancelled()
    app_client.async_get_travel.side_effect = original
    with patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    restored = await call(hass, sync_device, operation="status")
    assert restored["state"] == "querying" and restored["processed_months"] == 0
    app_client.async_get_travel.reset_mock()
    result = await call(hass, sync_device, operation="continue", job_id=job.job_id)
    assert result["state"] == "complete" and result["queried_months"] == 1
    assert app_client.async_get_travel.await_count == 1


async def test_lost_ownership_cannot_advance_or_persist_month(hass, entry, app_client, sync_device):
    co = entry.runtime_data.coordinator
    job = await co.history_sync.async_start(SN, "202609", "202609")

    async def invalidated(sn, month):
        co._generation += 1
        return {"times": 0, "list": []}

    app_client.async_get_travel.side_effect = invalidated
    with pytest.raises(HomeAssistantError):
        await co.history_sync.async_advance(SN, job.job_id)
    stored = await co.statistics.archive.async_sync_job()
    assert stored.state is SyncState.QUERYING and stored.processed == 0
    assert await co.statistics.async_cached_month(SN, "202609") is None


async def test_offline_known_month_reused_but_missing_waits_without_cloud(
    hass, entry, app_client, sync_device
):
    co = entry.runtime_data.coordinator
    co._authenticated = False
    first = await call(hass, sync_device, start_month="202609", end_month="202610")
    assert first["state"] == "waiting" and first["reason"] == "unavailable"
    assert first["processed_months"] == 1 and first["locally_skipped_months"] == 1
    assert first["next_month"] == "202609" and first["retry_after_s"] is None
    app_client.async_get_travel.assert_not_awaited()


async def test_storage_retry_replays_received_raw_without_another_query(
    hass, entry, app_client, sync_device, freezer
):
    co = entry.runtime_data.coordinator
    with patch.object(co.statistics, "async_record"):
        first = await call(hass, sync_device, start_month="202609", end_month="202609")
    assert first["state"] == "waiting" and first["reason"] == "storage"
    assert first["processed_months"] == 0 and first["retry_after_s"] == 60
    assert app_client.async_get_travel.await_count == 1
    app_client.async_get_travel.reset_mock()
    freezer.tick(timedelta(seconds=61))
    second = await call(hass, sync_device, operation="continue", job_id=first["job_id"])
    assert second["state"] == "complete" and second["queried_months"] == 1
    app_client.async_get_travel.assert_not_awaited()
    assert await co.statistics.async_cached_month(SN, "202609") is not None


async def test_unavailable_archive_and_invalid_sync_schema(hass, entry, app_client, sync_device):
    from custom_components.ninebot.compat import validation as vol
    from custom_components.ninebot.sync_actions import SYNC_SCHEMA

    co = entry.runtime_data.coordinator
    for data in ({"job_id": "../private"}, {"operation": "control"}):
        with pytest.raises(vol.Invalid):
            SYNC_SCHEMA({"device_id": sync_device, **data})
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, sync_device)
    assert err.value.translation_key == "history_range"
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, sync_device, start_month="202610", end_month="202609")
    assert err.value.translation_key == "history_range"
    co.statistics.archive_ready = False
    for operation in ("start", "status"):
        with pytest.raises(HomeAssistantError) as err:
            await call(
                hass, sync_device, operation=operation, start_month="202609", end_month="202609"
            )
        assert err.value.translation_key == "statistics_unavailable"
    co.statistics.archive_ready = True
    await co.statistics.archive.async_close()
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, sync_device, start_month="202609", end_month="202609")
    assert err.value.translation_key == "statistics_unavailable"
    app_client.async_get_travel.assert_not_awaited()


def test_sync_codec_rejects_inconsistent_and_secret_metadata():
    job = SyncJob(
        "a" * 32, "b" * 64, "202601", "202603", "202603", NOW.isoformat(), NOW.isoformat()
    )
    assert restored_job(json.loads(json.dumps(job_data(job)))) == job
    for edit in (
        {"token": "secret"},
        {"processed": True},
        {"next_month": "202602"},
        {"state": "complete"},
        {"reason": "connection"},
        {"unknown_months": ["202603"]},
        {"retry_at": NOW.isoformat()},
        {"vehicle": "other"},
    ):
        with pytest.raises(ValueError):
            restored_job({**json.loads(json.dumps(job_data(job))), **edit})
    ready = job.advance(NOW.isoformat(), queried=True, complete=None)
    assert restored_job(json.loads(json.dumps(job_data(ready)))) == ready
    cancelled = replace(ready, state=SyncState.CANCELLED, reason="cancelled")
    assert restored_job(json.loads(json.dumps(job_data(cancelled)))) == cancelled


def test_checkpoint_coverage_and_terminal_state_cannot_be_forged():
    job = SyncJob(
        "a" * 32, "b" * 64, "202601", "202603", "202603", NOW.isoformat(), NOW.isoformat()
    ).advance(NOW.isoformat(), queried=True, complete=None)
    base = json.loads(json.dumps(job_data(job)))
    for change in (
        {"updated_at": "2026-10-07T00:00:00+00:00"},
        {"queried": 0},
        {"state": "complete", "next_month": None},
        {"state": "cancelled"},
        {"state": "waiting"},
        {"unknown_months": ["202603", "202603"]},
        {"unknown_months": ["202604"]},
        {"unknown_months": ["202602"]},
        {"unknown_months": [1]},
        {"incomplete_months": ["202603"]},
    ):
        with pytest.raises(ValueError):
            restored_job({**base, **change})
    done = job.advance(NOW.isoformat(), queried=False, complete=True).advance(
        NOW.isoformat(), queried=False, complete=True
    )
    with pytest.raises(ValueError):
        done.advance(NOW.isoformat(), queried=False, complete=True)


async def test_preservation_resumes_explicit_range_one_month_per_budget(
    hass, entry, app_client, sync_device, freezer
):
    from custom_components.ninebot.preservation import HistoryPreservation

    co = entry.runtime_data.coordinator
    await co.history_sync.async_start(SN, "202608", "202609")
    # No eligible details: range continuation is the next operation. Synthetic
    # current-month fixture has IDs without valid starts and never controls a vehicle.
    with patch.object(co.statistics.archive, "async_preservation_claim", return_value=None):
        worker = HistoryPreservation(co)
        await worker.async_tick(NOW)
        job = await co.history_sync.async_job(SN)
        assert job.processed == 1 and job.next_month == "202608"
        assert app_client.async_get_travel.await_count == 1
        await worker.async_tick(NOW + timedelta(seconds=1))
        assert app_client.async_get_travel.await_count == 1
        freezer.tick(timedelta(seconds=121))
        await worker.async_tick(NOW + timedelta(seconds=121))
        job = await co.history_sync.async_job(SN)
        assert job.state is SyncState.COMPLETE and job.queried == 2
        assert len(job.incomplete_months) == 2
        assert app_client.async_get_travel.await_count == 2
        await worker.async_close()
    app_client.async_control.assert_not_awaited()


async def test_sparse_repair_action_only_selected_months_and_partial_status(
    hass, entry, app_client, sync_device, freezer
):
    first = await call(hass, sync_device, operation="repair", months=["202604", "202608"])
    assert app_client.async_get_travel.await_count == 1
    assert first["tasks"][0]["month"] == "202604"
    assert first["tasks"][0]["state"] == "complete"
    assert first["tasks"][0]["list_complete"] is False
    assert first["tasks"][1]["state"] == "pending"
    assert not first["upstream_pagination_verified"]
    app_client.async_get_travel.reset_mock()
    status = await call(hass, sync_device, operation="repair_status")
    assert status["tasks"] == first["tasks"]
    assert not status["operation_performed"]
    await call(hass, sync_device, operation="repair_continue")
    app_client.async_get_travel.assert_not_awaited()
    freezer.tick(timedelta(seconds=121))
    final = await call(hass, sync_device, operation="repair_continue")
    assert all(task["state"] == "complete" for task in final["tasks"])
    assert all(task["list_complete"] is False for task in final["tasks"])
    assert app_client.async_get_travel.await_args.args == (SN, "202608")
    assert not any(value in json.dumps(final) for value in (SN, "token", "latitude"))
    app_client.async_control.assert_not_awaited()


async def test_sparse_cancel_drains_query_without_publishing_result(
    hass, entry, app_client, sync_device
):
    co = entry.runtime_data.coordinator
    await co.statistics.archive.async_schedule_months(SN, ("202609",), NOW, guard=lambda: True)
    entered = asyncio.Event()

    async def blocked(sn, month):
        entered.set()
        await asyncio.Event().wait()

    app_client.async_get_travel.side_effect = blocked
    task = asyncio.create_task(co.preservation.months.async_step(SN, 30, NOW))
    await entered.wait()
    cancelled = await call(hass, sync_device, operation="repair_cancel", months=["202609"])
    assert task.cancelled() and cancelled["tasks"][0]["state"] == "cancelled"
    assert await co.statistics.async_cached_month(SN, "202609") is None
    assert co.preservation.months._task is None


async def test_recent_seed_stops_after_first_week_and_reuses_same_day(
    hass, entry, app_client, sync_device, freezer
):
    co = entry.runtime_data.coordinator
    freezer.move_to(datetime(2026, 10, 1, tzinfo=UTC))
    when = datetime(2026, 10, 1, tzinfo=UTC)
    # Isolate calendar planning from the setup fixture's Oct 8 profile receipt.
    with patch.object(co, "fresh", return_value=True):
        await co.preservation.months.async_seed_recent((SN,), when)
        assert await co.preservation.months.async_step(SN, 30, when)
        assert app_client.async_get_travel.await_args.args == (SN, "202609")
        app_client.async_get_travel.reset_mock()
        await co.preservation.months.async_seed_recent((SN,), when + timedelta(hours=1))
        assert not await co.preservation.months.async_step(SN, 30, when + timedelta(hours=1))
        await co.preservation.months.async_seed_recent((SN,), when + timedelta(days=7))
        assert not await co.preservation.months.async_step(SN, 30, when + timedelta(days=7))
        app_client.async_get_travel.assert_not_awaited()


async def test_pause_resume_status_are_device_scoped_local_only(
    hass, entry, app_client, sync_device
):
    paused = await call(hass, sync_device, operation="pause")
    assert paused["paused"] and paused["state"] == "paused"
    assert paused["total_months"] is None and paused["details"] is None
    status = await call(hass, sync_device, operation="sync_status")
    assert status["paused"] and not status["background_enabled"]
    resumed = await call(hass, sync_device, operation="resume")
    assert not resumed["paused"] and resumed["state"] == "not_started"
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_control.assert_not_awaited()
    assert not await entry.runtime_data.coordinator.statistics.archive.async_history_paused(SN)
