"""Status transitions from existing successful receipts; no extra API polling."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot.demand import Need, entity_context
from custom_components.ninebot.exceptions import ErrorKind, NinebotError
from custom_components.ninebot.vehicle_events import StatusEventCursor, StatusObservation

NOW = datetime(2026, 10, 8, 10, tzinfo=UTC)
SN = "SyntheticSN"


def observation(revision, values=(False, False, True, True), *, seconds=0, epoch=(1, 1)):
    return StatusObservation((*epoch, revision), NOW + timedelta(seconds=seconds), values)


def test_known_transitions_are_batched_and_duplicate_receipts_are_ignored():
    cursor = StatusEventCursor()
    first = observation(1)
    assert not cursor.observe(first, max_gap_s=360)
    second = observation(2, (True, True, False, False), seconds=1)
    assert cursor.observe(second, max_gap_s=360) == (
        "powered_on",
        "charging_started",
        "unlocked",
        "seat_unlocked",
    )
    assert not cursor.observe(second, max_gap_s=360)
    assert cursor.observe(observation(3, seconds=2), max_gap_s=360) == (
        "powered_off",
        "charging_stopped",
        "locked",
        "seat_locked",
    )


@pytest.mark.parametrize("reset", [None, "ownership", "generation", "gap", "clock", "revision"])
def test_failure_reconnection_long_gap_and_clock_reset_establish_silent_baselines(reset):
    cursor = StatusEventCursor()
    cursor.observe(observation(2), max_gap_s=360)
    if reset is None:
        assert not cursor.observe(None, max_gap_s=360)
    sample = observation(
        1 if reset == "revision" else 3,
        (True, True, False, False),
        seconds=361 if reset == "gap" else -1 if reset == "clock" else 1,
        epoch=(1, 2) if reset == "ownership" else (2, 1) if reset == "generation" else (1, 1),
    )
    assert not cursor.observe(sample, max_gap_s=360)


def test_missing_values_are_not_coerced_to_false_or_replayed_when_known_again():
    cursor = StatusEventCursor()
    cursor.observe(observation(1), max_gap_s=360)
    assert not cursor.observe(observation(2, (None, None, None, None)), max_gap_s=360)
    assert not cursor.observe(observation(3, (True, True, False, False)), max_gap_s=360)
    assert cursor.observe(observation(4), max_gap_s=360) == (
        "powered_off",
        "charging_stopped",
        "locked",
        "seat_locked",
    )
    assert entity_context(SN, "event", "vehicle_event", "status").need is Need.STATUS


@pytest.fixture
async def vehicle(hass, entry, app_client, freezer):
    freezer.move_to(NOW)
    app_client.async_get_status.return_value = {
        "pwr": 0,
        "charging": 0,
        "loc": {"lock": 0},
        "barrel_lock_status": 0,
    }
    with patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        identity = er.async_get(hass).async_get_entity_id("event", "ninebot", f"{SN}_vehicle_event")
        assert identity
        yield identity


async def report(hass, entry, app_client, payload):
    app_client.async_get_status.return_value = payload
    co = entry.runtime_data.coordinator
    await co._group(SN, "status", force=True)
    co.async_set_updated_data(dict(co.data))
    await hass.async_block_till_done()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_real_entity_batches_same_millisecond_transitions_without_extra_queries(
    hass, entry, app_client, vehicle
):
    identity = vehicle
    initial = hass.states.get(identity)
    assert initial.state == "unknown"
    assert initial.attributes["observation_status"] == "waiting_for_transition"
    events = []
    remove = hass.bus.async_listen(
        EVENT_STATE_CHANGED,
        lambda event: events.append(event) if event.data["entity_id"] == identity else None,
    )
    calls = app_client.async_get_status.await_count
    try:
        await report(
            hass,
            entry,
            app_client,
            {"pwr": 1, "charging": 1, "loc": {"lock": 1}, "barrel_lock_status": 1},
        )
        first = hass.states.get(identity)
        assert first.attributes["event_type"] == "state_changed"
        assert first.attributes["changes"] == [
            "powered_on",
            "charging_started",
            "unlocked",
            "seat_unlocked",
        ]
        assert "Charging started" in first.attributes["summary"]
        assert first.attributes["source"] == "cloud_status_observation"
        assert first.attributes["observed_at"] == NOW.isoformat()
        co = entry.runtime_data.coordinator
        count = len(events)
        co.async_set_updated_data(dict(co.data))
        await hass.async_block_till_done()
        assert len(events) == count
        await report(
            hass,
            entry,
            app_client,
            {"pwr": 0, "charging": 0, "loc": {"lock": 0}, "barrel_lock_status": 0},
        )
        second = hass.states.get(identity)
        assert second.attributes["observation_revision"] > first.attributes["observation_revision"]
        assert second.attributes["changes"] == [
            "powered_off",
            "charging_stopped",
            "locked",
            "seat_locked",
        ]
        assert len(events) == count + 1  # Also true on old Core with an unchanged millisecond.
        assert app_client.async_get_status.await_count == calls + 2
        for forbidden in ("raw", "latitude", "longitude", "track", "account", "token"):
            assert forbidden not in second.attributes
        app_client.async_control.assert_not_awaited()
        app_client.async_get_trip_detail.assert_not_awaited()
    finally:
        remove()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_failed_read_and_unknown_field_do_not_emit_after_recovery(
    hass, entry, app_client, vehicle
):
    co = entry.runtime_data.coordinator
    app_client.async_get_status.side_effect = NinebotError(ErrorKind.CONNECTION)
    await co._group(SN, "status", force=True)
    co.async_set_updated_data(dict(co.data))
    await hass.async_block_till_done()
    assert co.status_observation(SN) is None
    app_client.async_get_status.side_effect = None
    await report(hass, entry, app_client, {"pwr": 1, "charging": 1, "loc": {"lock": 1}})
    assert hass.states.get(vehicle).state == "unknown"
    await report(hass, entry, app_client, {"pwr": 99, "charging": 99, "loc": {}})
    await report(hass, entry, app_client, {"pwr": 0, "charging": 0, "loc": {"lock": 0}})
    assert hass.states.get(vehicle).state == "unknown"
    # Removing ownership or expiring a successful receipt cannot supply an event.
    co.data[SN] = replace(co.data[SN], present=False)
    assert co.status_observation(SN) is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_reload_restores_last_display_but_does_not_replay_a_transition(
    hass, entry, app_client, vehicle
):
    await report(hass, entry, app_client, {"pwr": 1, "charging": 0, "loc": {"lock": 0}})
    before = hass.states.get(vehicle)
    row_id = er.async_get(hass).async_get(vehicle).id
    assert before.attributes["changes"] == ["powered_on"]
    with patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert er.async_get(hass).async_get(vehicle).id == row_id
    restored = hass.states.get(vehicle)
    assert restored.state == before.state
    assert restored.attributes["observation_revision"] == before.attributes["observation_revision"]
    assert restored.attributes["changes"] == ["powered_on"]
