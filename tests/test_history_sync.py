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
