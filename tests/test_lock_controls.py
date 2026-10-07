"""Offline safety, target confirmation and native Lock service contracts."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ninebot import coordinator as module
from custom_components.ninebot.client import NinecliClient
from custom_components.ninebot.control_safety import SafetyObservation, SafetySource
from custom_components.ninebot.coordinator import NinebotCoordinator
from custom_components.ninebot.exceptions import ErrorKind, NinebotAuthError, NinebotError
from custom_components.ninebot.lock import NinebotLock

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
async def controls(hass, entry, app_client, monkeypatch):
    # Real schedules are covered by the deadline test, not five-second sleeps.
    monkeypatch.setattr(module, "CONFIRMATION_DELAYS", (0, 0, 0))
    app_client.async_get_status.return_value["barrel_lock_status"] = 0
    hass.config_entries.async_update_entry(
        entry, options={"enable_controls": True, "control_vehicles": ["SyntheticSN"]}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry.runtime_data.coordinator
    assert await hass.config_entries.async_unload(entry.entry_id)


def payload(vehicle=1, seat=0):
    return {"loc": {"lock": vehicle}, "barrel_lock_status": seat}


@pytest.mark.parametrize(
    "value,expected", [(0, True), (1, False), (None, None), (2, None), (True, None), ("x", None)]
)
async def test_seat_state_is_strict_not_lid_position(controls, value, expected):
    from custom_components.ninebot import adapters

    sn = "SyntheticSN"
    controls.data[sn] = replace(controls.data[sn], status=adapters.status(payload(seat=value)))
    assert controls.lock_state(sn, "seat_lock") is expected


@pytest.mark.parametrize("action,target", [("engine/start", "vehicle_lock"), ("buck", "seat_lock")])
async def test_command_updates_target_and_preserves_buttons(controls, entry, action, target):
    co = controls
    co.client.async_get_status_once.side_effect = [payload(), payload(vehicle=0, seat=1)]
    await co.async_control("SyntheticSN", action)
    co.client.async_control.assert_awaited_once_with("SyntheticSN", action)
    assert co.client.async_get_status_once.await_count == 2
    assert co.lock_state("SyntheticSN", target) is False
    result = co.control_results.diagnostics("SyntheticSN")[action]
    assert result["confirmation"] == "confirmed" and result["read_attempts"] == 2
    assert result["physical_outcome_verified"] is False
    assert not co.pending_controls and not co._control_leases
    rows = er.async_entries_for_config_entry(er.async_get(co.hass), entry.entry_id)
    assert sum(row.domain == "lock" for row in rows) == 2
    assert sum(row.domain == "button" for row in rows) == 5
    assert not any(row.unique_id.endswith(("_unlocked", "_seat_lock_raw")) for row in rows)


@pytest.mark.parametrize("raw,confirmation", [(payload(), "target_not_observed"), ({}, "unknown")])
async def test_accepted_is_not_success_and_read_budget_is_three(controls, raw, confirmation):
    co = controls
    co.client.async_get_status_once.return_value = raw
    with pytest.raises(HomeAssistantError):
        await co.async_control("SyntheticSN", "engine/start")
    co.client.async_control.assert_awaited_once()
    assert co.client.async_get_status_once.await_count == 3
    assert (
        co.control_results.diagnostics("SyntheticSN")["engine/start"]["confirmation"]
        == confirmation
    )
    assert not co.pending_controls and not co._control_leases


async def test_uncertain_post_stays_uncertain_even_when_target_observed(controls):
    co = controls
    co.client.async_control.side_effect = NinebotError(ErrorKind.CONNECTION)
    co.client.async_get_status_once.return_value = payload(vehicle=0)
    with pytest.raises(HomeAssistantError) as err:
        await co.async_control("SyntheticSN", "engine/start")
    assert err.value.translation_key == "control_uncertain"
    result = co.control_results.diagnostics("SyntheticSN")["engine/start"]
    assert result["outcome"] == "uncertain"
    assert result["confirmation"] == "observed_target_command_uncertain"
    assert co.lock_state("SyntheticSN", "vehicle_lock") is False
    co.client.async_control.assert_awaited_once()


@pytest.mark.parametrize(
    "stopped,parked,age,source",
    [
        (None, None, 0, SafetySource.UNKNOWN),
        (False, True, 0, SafetySource.REVIEWED_TELEMETRY),
        (True, False, 0, SafetySource.REVIEWED_TELEMETRY),
        (True, True, 6, SafetySource.REVIEWED_TELEMETRY),
        (True, True, -1, SafetySource.REVIEWED_TELEMETRY),
        (True, True, 0, SafetySource.UNKNOWN),
    ],
)
async def test_lock_requires_reviewed_stopped_and_p_not_acc(
    controls, entry, stopped, parked, age, source
):
    co = controls
    sn = "SyntheticSN"
    snapshot = co.data[sn]
    safety = SafetyObservation(stopped, parked, dt_util.utcnow() - timedelta(seconds=age), source)
    co.data[sn] = replace(snapshot, status=replace(snapshot.status, safety=safety))
    assert not co.controls_enabled(sn, "engine/stop")
    for call in (
        lambda: co.async_control(sn, "engine/stop"),
        lambda: NinebotLock(entry, sn, "vehicle_lock").async_lock(),
    ):
        with pytest.raises(HomeAssistantError) as err:
            await call()
        assert err.value.translation_key == "parking_unverified"
    co.client.async_control.assert_not_awaited()
    co.client.async_get_status_once.assert_not_awaited()


async def test_reviewed_synthetic_safety_can_lock_and_manual_seat_lock_never_posts(controls, entry):
    co = controls
    sn = "SyntheticSN"
    snapshot = co.data[sn]
    safety = SafetyObservation(True, True, dt_util.utcnow(), SafetySource.REVIEWED_TELEMETRY)
    co.data[sn] = replace(snapshot, status=replace(snapshot.status, locked=False, safety=safety))
    co.client.async_get_status_once.return_value = payload(vehicle=1)
    await NinebotLock(entry, sn, "vehicle_lock").async_lock()
    co.client.async_control.assert_awaited_once_with(sn, "engine/stop")
    with pytest.raises(HomeAssistantError) as err:
        await NinebotLock(entry, sn, "seat_lock").async_lock()
    assert err.value.translation_key == "seat_lock_manual"
    assert co.client.async_control.await_count == 1


async def test_already_in_target_sends_nothing_but_consent_is_still_required(controls):
    co = controls
    sn = "SyntheticSN"
    snapshot = co.data[sn]
    co.data[sn] = replace(snapshot, status=replace(snapshot.status, locked=False))
    await co.async_control(sn, "engine/start")
    assert co.control_results.diagnostics(sn)["engine/start"]["confirmation"] == "already_in_target"
    co.client.async_control.assert_not_awaited()
    co.client.async_get_status_once.assert_not_awaited()
    with patch.object(co, "controls_enabled", return_value=False):
        with pytest.raises(HomeAssistantError):
            await co.async_control(sn, "engine/start")


async def test_pending_never_optimistic_and_same_vehicle_across_accounts_is_busy(controls, entry):
    co = controls
    started, release = asyncio.Event(), asyncio.Event()

    async def post(*args):
        started.set()
        await release.wait()

    co.client.async_control.side_effect = post
    co.client.async_get_status_once.return_value = payload(vehicle=0)
    lock = NinebotLock(entry, "SyntheticSN", "vehicle_lock")
    task = asyncio.create_task(lock.async_unlock())
    await started.wait()
    other_entry = MockConfigEntry(
        domain="ninebot",
        data={},
        options={"enable_controls": True, "control_vehicles": ["SyntheticSN"]},
    )
    other = NinebotCoordinator(co.hass, other_entry, AsyncMock())
    other.data = dict(co.data)
    other._authenticated = True
    try:
        assert lock.is_unlocking is True and lock.is_locked is True
        with pytest.raises(HomeAssistantError) as err:
            await other.async_control("SyntheticSN", "buck")
        assert err.value.translation_key == "busy"
        other.client.async_control.assert_not_awaited()
        release.set()
        await task
        assert lock.is_unlocking is False and lock.is_locked is False
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await other.async_close()


@pytest.mark.parametrize("phase", ["post", "readback"])
async def test_cancel_releases_physical_lease_and_does_not_replay(controls, phase):
    co = controls
    started = asyncio.Event()

    async def blocked(*args):
        started.set()
        await asyncio.Event().wait()

    if phase == "post":
        co.client.async_control.side_effect = blocked
    else:
        co.client.async_get_status_once.side_effect = blocked
    task = asyncio.create_task(co.async_control("SyntheticSN", "buck"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not co.pending_controls and not co._control_leases and co._control_pending == 0
    co.client.async_control.assert_awaited_once()
    assert co.client.async_get_status_once.await_count == (phase == "readback")


async def test_confirmation_deadline_cancels_wire_and_clears_pending(controls, monkeypatch):
    co = controls
    monkeypatch.setattr(module, "CONFIRMATION_TIMEOUT", 0.02)

    async def blocked(*args):
        await asyncio.Event().wait()

    co.client.async_get_status_once.side_effect = blocked
    with pytest.raises(HomeAssistantError):
        await co.async_control("SyntheticSN", "engine/start")
    assert co.client.async_get_status_once.await_count == 1
    assert not co.pending_controls and not co._control_leases
    assert (
        co.control_results.diagnostics("SyntheticSN")["engine/start"]["confirmation"] == "unknown"
    )


async def test_confirmation_auth_failure_stops_without_another_read(controls):
    co = controls
    co.client.async_get_status_once.side_effect = NinebotAuthError()
    with pytest.raises(HomeAssistantError):
        await co.async_control("SyntheticSN", "buck")
    co.client.async_get_status_once.assert_awaited_once()
    assert not co._authenticated
    assert co.control_results.diagnostics("SyntheticSN")["buck"]["readback_error"] == "auth"


async def test_single_attempt_transport_does_not_double_confirmation_budget(tmp_path):
    client = NinecliClient(tmp_path, AsyncMock())
    with patch.object(
        client, "_request_locked", side_effect=NinebotError(ErrorKind.SERVICE, retryable=True)
    ) as request:
        with pytest.raises(NinebotError):
            await client.async_get_status_once("SyntheticSN")
        request.assert_awaited_once()
    await client.async_close()


async def test_safety_changes_while_queued_blocks_stop_at_wire(controls):
    co = controls
    sn = "SyntheticSN"
    snapshot = co.data[sn]
    safe = SafetyObservation(True, True, dt_util.utcnow(), SafetySource.REVIEWED_TELEMETRY)
    co.data[sn] = replace(snapshot, status=replace(snapshot.status, locked=False, safety=safe))
    gate = co.broker._wire_gate
    await gate.acquire()
    await gate.acquire()
    task = asyncio.create_task(co.async_control(sn, "engine/stop"))
    await asyncio.sleep(0)
    co.data[sn] = replace(
        co.data[sn], status=replace(co.data[sn].status, safety=replace(safe, parked=False))
    )
    gate.release()
    gate.release()
    with pytest.raises(HomeAssistantError) as err:
        await task
    assert err.value.translation_key == "parking_unverified"
    co.client.async_control.assert_not_awaited()
    co.client.async_get_status_once.assert_not_awaited()
    assert not co.pending_controls and not co._control_leases


async def test_precommand_read_showing_target_cannot_confirm_post(controls):
    co = controls
    entered, release = asyncio.Event(), asyncio.Event()

    async def old_status(*args):
        entered.set()
        await release.wait()
        return payload(vehicle=0)

    co.client.async_get_status.side_effect = old_status
    co.client.async_get_status_once.return_value = payload(vehicle=1)
    manual = asyncio.create_task(co.async_refresh_vehicle("SyntheticSN"))
    await entered.wait()
    command = asyncio.create_task(co.async_control("SyntheticSN", "engine/start"))
    await asyncio.sleep(0)
    release.set()
    try:
        await manual
        with pytest.raises(HomeAssistantError) as err:
            await command
        assert err.value.translation_key == "control_target_not_observed"
        co.client.async_control.assert_awaited_once()
        assert co.client.async_get_status_once.await_count == 3
    finally:
        release.set()
        await asyncio.gather(manual, command, return_exceptions=True)


async def test_confirmation_rate_limit_stops_and_never_replays_post(controls):
    co = controls
    co.client.async_get_status_once.side_effect = NinebotError(ErrorKind.SERVICE, retry_after=60)
    with pytest.raises(HomeAssistantError):
        await co.async_control("SyntheticSN", "buck")
    co.client.async_control.assert_awaited_once()
    co.client.async_get_status_once.assert_awaited_once()
    assert co.broker.cooling_down and not co.pending_controls
