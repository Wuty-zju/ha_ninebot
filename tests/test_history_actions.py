"""Cross-month scans and display use synthetic months only, never cloud polling."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot.exceptions import ErrorKind, NinebotError
from custom_components.ninebot.history import HistoryState, HistoryStore
from custom_components.ninebot.history_actions import HISTORY_SCHEMA

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
async def history_device(hass, entry, app_client, freezer):
    freezer.move_to(datetime(2026, 9, 26, tzinfo=UTC))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data.coordinator.raw.clear()
    app_client.async_get_travel.reset_mock()

    def month(sn, query):
        stamp = datetime.strptime(query, "%Y%m").replace(tzinfo=UTC).timestamp()
        return {
            "month": query,
            "times": 4,
            "duration": 400,
            "ec": 1000,
            "total_mileages": 100,
            "list": [
                {
                    "travel_id": f"{query}-{i}",
                    "start_time": stamp + i * 200,
                    "end_time": stamp + i * 200 + 100,
                    "duration": 100,
                    "mileages": "0.5",
                    "ec": 10,
                }
                for i in range(3)
            ],
        }

    app_client.async_get_travel.side_effect = month
    return next(
        device.id
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if ("ninebot", "SyntheticSN") in device.identifiers
    )


async def call(hass, device, **data):
    return await hass.services.async_call(
        "ninebot",
        "get_history",
        {"device_id": device, "start_month": "202605", "end_month": "202609", **data},
        blocking=True,
        return_response=True,
    )


async def test_bounded_scan_drains_pages_truthful_totals_and_visible_entities(
    hass, entry, app_client, history_device
):
    assert hass.services.supports_response("ninebot", "get_history") is SupportsResponse.ONLY
    co = entry.runtime_data.coordinator
    current = co.data["SyntheticSN"]
    response = await call(hass, history_device, max_months_per_call=2, limit=2)
    assert len(response["months"]) == 2 and len(response["rides"]) == 2
    assert response["coverage"]["indexed_unique_count"] == 6
    assert response["scanned_months_totals"]["mileage_km"] == 200
    assert not response["coverage"]["range_scan_complete"]
    seen = {ride["ride_id"] for ride in response["rides"]}
    calls = app_client.async_get_travel.await_count
    response = await call(
        hass, history_device, cursor=response["next_cursor"], max_months_per_call=2, limit=2
    )
    assert response["months"] == [] and app_client.async_get_travel.await_count == calls
    assert response["rides"][0]["ride_id"] == "202609-2"
    while response["next_cursor"]:
        seen.update(ride["ride_id"] for ride in response["rides"])
        response = await call(
            hass, history_device, cursor=response["next_cursor"], max_months_per_call=2, limit=2
        )
    seen.update(ride["ride_id"] for ride in response["rides"])
    assert response["coverage"]["range_scan_complete"] is True
    assert response["coverage"]["all_rides_complete"] is False
    assert len(response["coverage"]["incomplete_months"]) == 5
    assert response["coverage"]["indexed_unique_count"] == 15
    assert response["scanned_months_totals"] == {
        "mileage_km": 500,
        "energy_wh": 5000,
        "reported_ride_count": 20,
        "duration_s": 2000,
        "basis": "server_month_summary",
    }
    assert app_client.async_get_travel.await_count == 5
    assert co.data["SyntheticSN"] is current
    assert len(seen) == 15
    app_client.async_get_trip_detail.assert_not_awaited()
    app_client.async_control.assert_not_awaited()
    registry = er.async_get(hass)
    await hass.async_block_till_done()
    for key, value in [
        ("history_scanned_months", 5),
        ("history_indexed_rides", 15),
        ("history_mileage", 500),
        ("history_energy", 5000),
        ("history_duration", 2000),
    ]:
        entity_id = registry.async_get_entity_id("sensor", "ninebot", f"SyntheticSN_{key}")
        assert registry.async_get(entity_id).disabled_by is None
        state = hass.states.get(entity_id)
        assert float(state.state) == value
        assert "state_class" not in state.attributes
        assert state.attributes["range_scan_complete"] is True
        assert len(json.dumps(dict(state.attributes))) < 1000
    encoded = json.dumps(response, allow_nan=False)
    assert len(encoded.encode()) < 1024 * 1024
    assert not any(
        word in encoded for word in ["latitude", "longitude", "token", "password", "SyntheticSN"]
    )


async def test_cursor_bound_to_vehicle_range_and_expiry(
    hass, entry, app_client, history_device, freezer
):
    first = await call(hass, history_device, max_months_per_call=1, limit=1)
    cursor = first["next_cursor"]
    app_client.async_get_travel.reset_mock()
    for data in [{"start_month": "202606", "cursor": cursor}, {"cursor": "0" * 32}]:
        with pytest.raises(HomeAssistantError) as error:
            await call(hass, history_device, **data)
        assert error.value.translation_key == "history_cursor"
    co = entry.runtime_data.coordinator
    stamp, original = co.history._states[cursor]
    co.history._states[cursor] = stamp, replace(original, vehicle="OtherVehicle")
    with pytest.raises(HomeAssistantError):
        await call(hass, history_device, cursor=cursor)
    co.history._states[cursor] = stamp, original
    freezer.tick(timedelta(seconds=901))
    with pytest.raises(HomeAssistantError):
        await call(hass, history_device, cursor=cursor)
    app_client.async_get_travel.assert_not_awaited()


async def test_partial_error_retains_prefix_and_blocks_repeat_until_retry(
    hass, entry, app_client, history_device
):
    original = app_client.async_get_travel.side_effect

    def fail(sn, month):
        if month == "202608":
            raise NinebotError(ErrorKind.CONNECTION)
        return original(sn, month)

    app_client.async_get_travel.side_effect = fail
    first = await call(hass, history_device)
    assert first["coverage"]["scanned_months"] == ["202609"]
    assert first["error"] == {"month": "202608", "kind": "connection"}
    assert first["retry_after_s"] == 60
    second = await call(hass, history_device, cursor=first["next_cursor"])
    assert second["error"]["kind"] == "retry_cooldown"
    assert app_client.async_get_travel.await_count == 2
    assert second["coverage"]["indexed_unique_count"] == 3


async def test_budget_is_explicit_not_false_full_history(hass, entry, app_client, history_device):
    with patch("custom_components.ninebot.history_actions.MAX_HISTORY_IDS", 2):
        response = await call(hass, history_device)
    assert response["stopped_reason"] == "index_budget"
    assert response["coverage"]["range_scan_complete"] is False
    assert response["coverage"]["all_rides_complete"] is False
    assert response["next_cursor"] is None
    assert len(response["rides"]) == 2
    assert app_client.async_get_travel.await_count == 1


def test_history_cache_eviction_budget_and_schema_limits(freezer):
    store = HistoryStore()
    now = datetime.now(UTC)
    state = HistoryState("entry", "vehicle", "202605", "202609", "202608")
    keys = [store.put(state, now) for _ in range(9)]
    assert store.get(keys[0], now) is None and store.get(keys[-1], now) is state
    assert len(store._states) == 8
    assert store.get(keys[-1], now + timedelta(seconds=901)) is None
    with patch("custom_components.ninebot.history.HISTORY_BUDGET", 1):
        assert store.put(state, now) is None
    store.clear()
    assert not store._states and not store.summary and not store.retry_at
    for fields in [
        {"limit": 101},
        {"max_months_per_call": 7},
        {"cursor": "../"},
        {"include_track": True},
    ]:
        with pytest.raises(vol.Invalid):
            HISTORY_SCHEMA(
                {"device_id": "device", "start_month": "202605", "end_month": "202609", **fields}
            )
