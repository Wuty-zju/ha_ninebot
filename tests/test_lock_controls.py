"""Offline cloud dispatch, target confirmation and native Lock service contracts."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ninebot import coordinator as module
from custom_components.ninebot.client import NinecliClient
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
    buttons = {row.unique_id for row in rows if row.domain == "button"}
    assert {
        # The established entity key is bucket; buck is its command name.
        f"SyntheticSN_{key}"
        for key in ("refresh", "bell", "bucket", "engine_start", "engine_stop")
    } <= buttons
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


@pytest.mark.parametrize("acc", [None, 0, 1, 2])
async def test_stop_sends_without_motion_or_p_telemetry(controls, entry, acc):
    co = controls
    sn = "SyntheticSN"
    snapshot = co.data[sn]
    co.data[sn] = replace(
        snapshot, status=replace(snapshot.status, locked=False, observations={"loc.acc": acc})
    )
    co.client.async_get_status_once.return_value = payload(vehicle=1)
    assert co.controls_enabled(sn, "engine/stop")
    await NinebotLock(entry, sn, "vehicle_lock").async_lock()
    co.client.async_control.assert_awaited_once_with(sn, "engine/stop")
    assert co.lock_state(sn, "vehicle_lock") is True
    assert co.control_results.diagnostics(sn)["engine/stop"]["confirmation"] == "confirmed"


async def test_manual_seat_lock_never_posts(controls, entry):
    with pytest.raises(HomeAssistantError) as err:
        await NinebotLock(entry, "SyntheticSN", "seat_lock").async_lock()
    assert err.value.translation_key == "seat_lock_manual"
    controls.client.async_control.assert_not_awaited()


async def test_already_observed_target_still_sends_once_with_consent(controls):
    co = controls
    sn = "SyntheticSN"
    snapshot = co.data[sn]
    co.data[sn] = replace(snapshot, status=replace(snapshot.status, locked=False))
    co.client.async_get_status_once.return_value = payload(vehicle=0)
    await co.async_control(sn, "engine/start")
    assert co.control_results.diagnostics(sn)["engine/start"]["confirmation"] == "confirmed"
    co.client.async_control.assert_awaited_once_with(sn, "engine/start")
    co.client.async_get_status_once.assert_awaited_once()
    with patch.object(co, "controls_enabled", return_value=False):
        with pytest.raises(HomeAssistantError):
            await co.async_control(sn, "engine/start")
    assert co.client.async_control.await_count == 1


async def test_pending_stays_observed_and_cross_account_commands_serialize_only_dispatch(
    controls, entry
):
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
        data={"business_uid": "other-account"},
        options={"enable_controls": True, "control_vehicles": ["SyntheticSN"]},
    )
    other = NinebotCoordinator(co.hass, other_entry, AsyncMock())
    other.data = dict(co.data)
    other._authenticated = True
    other.client.async_get_status_once.return_value = payload(seat=1)
    other_task = asyncio.create_task(other.async_control("SyntheticSN", "buck"))
    try:
        assert lock.is_unlocking is False and lock.is_locked is True
        for _ in range(5):
            await asyncio.sleep(0)
        other.client.async_control.assert_not_awaited()
        release.set()
        await asyncio.gather(task, other_task)
        other.client.async_control.assert_awaited_once_with("SyntheticSN", "buck")
        assert lock.is_unlocking is False and lock.is_locked is False
    finally:
        release.set()
        await asyncio.gather(task, other_task, return_exceptions=True)
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


async def test_permission_changes_while_queued_still_blocks_stop(controls):
    from custom_components.ninebot.capabilities import (
        CapabilityState,
        ControlCapability,
        VehicleCapabilities,
    )

    co = controls
    sn = "SyntheticSN"
    gate = co.broker._wire_gate
    await gate.acquire()
    await gate.acquire()
    task = asyncio.create_task(co.async_control(sn, "engine/stop"))
    await asyncio.sleep(0)
    co.data[sn] = replace(
        co.data[sn],
        status=replace(
            co.data[sn].status,
            capabilities=VehicleCapabilities(
                (
                    ControlCapability(
                        "engine/stop",
                        permission=CapabilityState.DENIED,
                    ),
                )
            ),
        ),
    )
    gate.release()
    gate.release()
    with pytest.raises(HomeAssistantError) as err:
        await task
    assert err.value.translation_key == "controls_disabled"
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


@pytest.mark.parametrize("reason", ["budget", "cooldown"])
async def test_broker_rejects_before_wire_without_readback_or_uncertain_claim(controls, reason):
    co = controls
    if reason == "budget":
        co.broker._command_limit = 0
        expected = "busy"
    else:
        co.broker._cooldown_until = asyncio.get_running_loop().time() + 60
        expected = "control_not_sent"
    with pytest.raises(HomeAssistantError) as err:
        await co.async_control("SyntheticSN", "engine/start")
    assert err.value.translation_key == expected
    co.client.async_control.assert_not_awaited()
    co.client.async_get_status_once.assert_not_awaited()
    result = co.control_results.diagnostics("SyntheticSN")["engine/start"]
    assert result["outcome"] == "rejected"
    assert result["confirmation"] == "not_requested" and result["read_attempts"] == 0
    assert result["readback"] == "skipped"
    assert not co.pending_controls and not co._control_leases


async def test_temporary_followup_observes_auto_relock_without_changing_command_result(
    controls,
    entry,
    monkeypatch,
):
    co = controls
    monkeypatch.setattr(module, "CONTROL_FOLLOWUP_DELAYS", (0, 0, 0))
    co.client.async_get_status_once.side_effect = [
        payload(vehicle=0),
        payload(vehicle=0),
        payload(vehicle=1),
        payload(vehicle=1),
    ]
    await co.async_control("SyntheticSN", "engine/start")
    following = co._control_followups["SyntheticSN"]
    assert not co.pending_controls and not co._control_leases
    await following
    await asyncio.sleep(0)
    assert not co._control_followups
    assert NinebotLock(entry, "SyntheticSN", "vehicle_lock").is_locked is True
    result = co.control_results.diagnostics("SyntheticSN")["engine/start"]
    assert result["confirmation"] == "confirmed" and result["observed_locked"] is False
    assert result["read_attempts"] == 1
    assert co.client.async_get_status_once.await_count == 4
    co.client.async_control.assert_awaited_once()
    co.client.async_get_battery.assert_awaited_once()  # bootstrap only
    assert module.CONTROL_FOLLOWUP_TIMEOUT == 30


async def test_followup_replaced_by_new_command_and_unload_cancels_remaining(controls):
    co = controls
    co.client.async_get_status_once.return_value = payload(vehicle=0)
    await co.async_control("SyntheticSN", "engine/start")
    old = co._control_followups["SyntheticSN"]
    co.client.async_get_status_once.return_value = payload(vehicle=1)
    await co.async_control("SyntheticSN", "engine/stop")
    assert old.cancelled()
    latest = co._control_followups["SyntheticSN"]
    assert latest is not old
    await co.async_close()
    assert latest.cancelled() and not co._control_followups
    assert co.client.async_control.await_count == 2


@pytest.mark.parametrize("failure", ["auth", "rate_limit", "network", "ownership"])
async def test_followup_failure_stops_without_retry_or_replaying_command(
    controls, monkeypatch, failure
):
    co = controls
    co.client.async_get_status_once.return_value = payload(vehicle=0)
    await co.async_control("SyntheticSN", "engine/start")
    following = co._control_followups.pop("SyntheticSN")
    following.cancel()
    await asyncio.gather(following, return_exceptions=True)
    monkeypatch.setattr(module, "CONTROL_FOLLOWUP_DELAYS", (0, 0, 0))
    if failure == "ownership":
        owner = co._key("SyntheticSN", module.Endpoint.STATUS)
        co._generation += 1
    else:
        owner = co._key("SyntheticSN", module.Endpoint.STATUS)
        co.client.async_get_status_once.side_effect = (
            NinebotAuthError()
            if failure == "auth"
            else NinebotError(ErrorKind.SERVICE, retry_after=60)
            if failure == "rate_limit"
            else NinebotError(ErrorKind.CONNECTION)
        )
    await co._control_followup("SyntheticSN", owner, asyncio.get_running_loop().time())
    assert co.client.async_get_status_once.await_count == (1 if failure == "ownership" else 2)
    co.client.async_control.assert_awaited_once()


async def test_followup_deadline_cancels_slow_read_without_replaying(controls, monkeypatch):
    co = controls
    monkeypatch.setattr(module, "CONTROL_FOLLOWUP_TIMEOUT", 0.02)
    monkeypatch.setattr(module, "CONTROL_FOLLOWUP_DELAYS", (0, 0, 0))

    async def blocked(*args):
        await asyncio.Event().wait()

    co.client.async_get_status_once.side_effect = blocked
    owner = co._key("SyntheticSN", module.Endpoint.STATUS)
    await co._control_followup("SyntheticSN", owner, asyncio.get_running_loop().time())
    co.client.async_get_status_once.assert_awaited_once()
    co.client.async_control.assert_not_awaited()
    assert not co.pending_controls and not co._control_leases


@pytest.mark.parametrize("transition", [0.2, 4.9, 9.8, 14.8])
@pytest.mark.parametrize("action", ["engine/start", "engine/stop", "buck"])
async def test_late_target_is_published_on_first_matching_read(
    controls, monkeypatch, transition, action
):
    from custom_components.ninebot.control_polling import read_slots

    schedule = (*range(15), 14.9)
    monkeypatch.setattr(module, "CONFIRMATION_DELAYS", schedule)
    elapsed = [0.0]
    observed = []

    async def slots(started, offsets):
        if offsets != schedule:
            async for offset in read_slots(started, offsets):
                yield offset
            return
        clock = [started]

        async def advance(delay):
            clock[0] += delay
            elapsed[0] = clock[0] - started
            await asyncio.sleep(0)

        async for offset in read_slots(started, offsets, now=lambda: clock[0], sleep=advance):
            yield offset

    monkeypatch.setattr(module, "read_slots", slots)
    target = action == "engine/stop"

    async def status(sn):
        observed.append(elapsed[0])
        reached = elapsed[0] >= transition
        locked = target if reached else not target
        return payload(vehicle=int(locked), seat=0 if locked else 1)

    controls.client.async_get_status_once.side_effect = status
    await controls.async_control("SyntheticSN", action)
    expected = next(offset for offset in schedule if offset >= transition)
    assert observed[-1] == pytest.approx(expected)
    key = "seat_lock" if action == "buck" else "vehicle_lock"
    assert controls.lock_state("SyntheticSN", key) is target
    assert len(observed) <= 16
    assert (
        controls.control_results.diagnostics("SyntheticSN")[action]["confirmation"] == "confirmed"
    )
    controls.client.async_control.assert_awaited_once_with("SyntheticSN", action)


async def test_seat_display_holds_unlock_then_returns_to_reported_latch(controls, entry, freezer):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.ninebot import adapters

    controls.client.async_get_status_once.return_value = payload(seat=1)
    await controls.async_control("SyntheticSN", "buck")
    lock = NinebotLock(entry, "SyntheticSN", "seat_lock")
    assert lock.is_locked is False
    assert lock.extra_state_attributes["unlock_feedback"] == "recently_unlocked"
    snapshot = controls.data["SyntheticSN"]
    controls.data["SyntheticSN"] = replace(snapshot, status=adapters.status(payload(seat=0)))
    assert lock.is_locked is False
    assert controls.lock_state("SyntheticSN", "seat_lock") is True
    controls.client.async_get_status_once.return_value = payload(seat=0)
    assert lock.extra_state_attributes["reported_state"] == "locked"
    assert lock.extra_state_attributes["unlock_feedback"] == "recently_unlocked"
    freezer.move_to(dt_util.utcnow() + timedelta(seconds=6))
    async_fire_time_changed(controls.hass, dt_util.utcnow())
    await controls.hass.async_block_till_done()
    assert lock.extra_state_attributes["unlock_feedback"] == "none"
    assert lock.is_locked is True
    assert not controls._seat_feedback_timers


async def test_seat_followup_does_not_treat_latch_relock_as_lid_closure(controls, monkeypatch):
    monkeypatch.setattr(module, "SEAT_FOLLOWUP_DELAYS", (0, 0, 0))
    controls.client.async_get_status_once.side_effect = [
        payload(seat=1),
        payload(seat=0),
        payload(seat=0),
    ]
    owner = controls._key("SyntheticSN", module.Endpoint.STATUS)
    await controls._control_followup(
        "SyntheticSN", owner, asyncio.get_running_loop().time(), seat=True
    )
    assert controls.client.async_get_status_once.await_count == 3
    controls.client.async_control.assert_not_awaited()
    assert controls.lock_state("SyntheticSN", "seat_lock") is True


async def test_regular_status_unlock_starts_display_hold_without_control(controls, entry):
    controls.client.async_get_status.return_value = payload(seat=1)
    await controls.async_refresh_vehicle("SyntheticSN")
    assert "SyntheticSN" in controls.seat_unlock_feedback
    lock = NinebotLock(entry, "SyntheticSN", "seat_lock")
    assert lock.is_locked is False
    controls.client.async_control.assert_not_awaited()
    timer = controls._seat_feedback_timers["SyntheticSN"]
    await controls.async_refresh_vehicle("SyntheticSN")
    assert controls._seat_feedback_timers["SyntheticSN"] is timer
    controls.client.async_get_status.return_value = payload(seat=0)
    await controls.async_refresh_vehicle("SyntheticSN")
    assert lock.is_locked is False
    assert controls.status_observation("SyntheticSN").values[3] is True
    controls.client.async_get_status.return_value = payload(seat=1)
    await controls.async_refresh_vehicle("SyntheticSN")
    assert controls._seat_feedback_timers["SyntheticSN"] is not timer
    controls._invalidate_auth()
    assert not controls._seat_feedback_timers and not controls.seat_unlock_feedback


async def test_seat_hold_never_fills_in_unknown_latch(controls, entry):
    from custom_components.ninebot import adapters

    controls.client.async_get_status_once.return_value = payload(seat=1)
    await controls.async_control("SyntheticSN", "buck")
    snapshot = controls.data["SyntheticSN"]
    controls.data["SyntheticSN"] = replace(snapshot, status=adapters.status(payload(seat=None)))
    lock = NinebotLock(entry, "SyntheticSN", "seat_lock")
    assert lock.is_locked is None
    assert lock.extra_state_attributes["reported_state"] == "unknown"


async def test_unconfirmed_seat_unlock_has_no_hold_or_extended_observation(controls):
    controls.client.async_get_status_once.return_value = payload(seat=0)
    with pytest.raises(HomeAssistantError):
        await controls.async_control("SyntheticSN", "buck")
    assert not controls.seat_unlock_feedback
    assert "SyntheticSN" not in controls._control_followups
    controls.client.async_control.assert_awaited_once()


@pytest.mark.parametrize("next_action", ["engine/start", "engine/stop"])
async def test_new_vehicle_command_supersedes_readback_without_replaying_post(
    controls, next_action
):
    co = controls
    reading = asyncio.Event()
    count = 0

    async def status(*args):
        nonlocal count
        count += 1
        if count == 1:
            reading.set()
            await asyncio.Event().wait()
        return payload(vehicle=int(next_action == "engine/stop"))

    co.client.async_get_status_once.side_effect = status
    old = asyncio.create_task(co.async_control("SyntheticSN", "engine/start"))
    await reading.wait()
    await asyncio.wait_for(co.async_control("SyntheticSN", next_action), 2)
    await asyncio.wait_for(old, 2)
    assert [call.args[1] for call in co.client.async_control.await_args_list] == [
        "engine/start",
        next_action,
    ]
    assert co.control_results.diagnostics("SyntheticSN")[next_action]["confirmation"] == "confirmed"
    assert not co._control_observers and not co._pending_commands


async def test_seat_cancel_wait_then_retry_never_sends_lock(controls, entry):
    co = controls
    reading = asyncio.Event()
    count = 0

    async def status(*args):
        nonlocal count
        count += 1
        if count == 1:
            reading.set()
            await asyncio.Event().wait()
        return payload(seat=1)

    co.client.async_get_status_once.side_effect = status
    seat = NinebotLock(entry, "SyntheticSN", "seat_lock")
    first = asyncio.create_task(seat.async_unlock())
    await reading.wait()
    await seat.async_lock()  # Cancel local waiting, not the physical command.
    await asyncio.wait_for(first, 2)
    assert co.control_results.diagnostics("SyntheticSN")["buck"]["confirmation"] == "superseded"
    assert seat.is_locked is True
    await seat.async_unlock()
    assert seat.is_locked is False
    with pytest.raises(HomeAssistantError) as error:
        await seat.async_lock()
    assert error.value.translation_key == "seat_lock_manual"
    await seat.async_unlock()  # Explicit repeated unlock remains possible.
    assert [call.args[1] for call in co.client.async_control.await_args_list] == ["buck"] * 3
