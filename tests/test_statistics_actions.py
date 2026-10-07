"""Real isolated response Actions, bounded periods and no implicit polling."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.ninebot.models import Freshness
from custom_components.ninebot.statistics_actions import months_between

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
NOW = datetime(2026, 10, 7, 4, tzinfo=UTC)


@pytest.fixture
async def statistics_device(hass, entry, app_client, freezer):
    freezer.move_to(NOW)
    app_client.async_get_travel.return_value = {
        "total_mileages": 14.8,
        "ec": 340,
        "times": 2,
        "duration": 2671,
        "detail": [0] * 5 + [14.8] + [0] * 25,
        "list": [
            {
                "travel_id": "observed-one",
                "mileages": 14.8,
                "ec": 340,
                "start_time_format": "2026-10-06 19:00:00",
                "end_time_format": "2026-10-06 19:44:31",
                "duration": 2671,
            }
        ],
    }
    with patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield next(
            device.id
            for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
            if ("ninebot", "SyntheticSN") in device.identifiers
        )


async def call(hass, device, **data):
    return await hass.services.async_call(
        "ninebot",
        "get_statistics",
        {"device_id": device, "start_month": "202609", "end_month": "202610", **data},
        blocking=True,
        return_response=True,
    )


async def test_cached_series_sources_real_zero_gaps_and_read_only_current_state(
    hass, entry, app_client, statistics_device
):
    assert hass.services.supports_response("ninebot", "get_statistics") is SupportsResponse.ONLY
    co = entry.runtime_data.coordinator
    current = co.data["SyntheticSN"]
    app_client.async_get_travel.reset_mock()
    result = await call(hass, statistics_device)
    assert len(result["months"]) == 2 and len(result["days"]) == 37
    assert result["scope"]["month_count"] == 2
    assert result["summary"]["distance_km"] is None
    assert result["summary"]["missing_months"] == ["202609"]
    assert result["months"][1]["energy_intensity_wh_per_km"] == pytest.approx(340 / 14.8)
    yesterday = result["days"][-2]
    assert yesterday["date"] == "2026-10-06" and yesterday["distance_km"] == 14.8
    assert yesterday["energy_wh"] is yesterday["ride_count"] is None
    assert yesterday["availability"]["ride_count"] == "incomplete_month_list"
    assert result["days"][-1]["distance_km"] == 0
    assert result["days"][0]["distance_km"] is None
    assert not result["months"][1]["observed_after_period_end"]
    assert result["query"] == {"refresh_requested": False, "max_month_queries": 0}
    encoded = json.dumps(result, allow_nan=False)
    assert len(encoded.encode()) < 256 * 1024
    assert not any(
        private in encoded
        for private in ("SyntheticSN", "password", "token", "latitude", "trail", "ride_id")
    )
    assert co.data["SyntheticSN"] is current
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_get_trip_detail.assert_not_awaited()
    app_client.async_control.assert_not_awaited()


async def test_explicit_refresh_cache_correction_scope_and_daily_disable(
    hass, entry, app_client, statistics_device, freezer
):
    co = entry.runtime_data.coordinator
    current = co.data["SyntheticSN"]
    app_client.async_get_travel.reset_mock()
    first = await call(hass, statistics_device, refresh=True, include_daily=False)
    assert first["days"] == [] and app_client.async_get_travel.await_count == 1  # current cached
    assert first["summary"]["distance_km"] == 29.6  # two distinct server month aggregates
    assert first["summary"]["energy_intensity_wh_per_km"] == pytest.approx(340 / 14.8)
    assert all(row["received_at"] == NOW.isoformat() for row in first["months"])
    await call(hass, statistics_device, refresh=True)
    assert app_client.async_get_travel.await_count == 1
    freezer.tick(timedelta(seconds=601))
    app_client.async_get_travel.return_value = {
        "total_mileages": 10,
        "ec": 200,
        "times": 0,
        "list": [],
    }
    updated = await call(hass, statistics_device, refresh=True, include_daily=False)
    assert updated["summary"]["distance_km"] == 20
    assert updated["summary"]["energy_wh"] == 400
    assert all(row["revision"] == 2 for row in updated["months"])
    assert co.data["SyntheticSN"] is current
    app_client.async_get_trip_detail.assert_not_awaited()
    app_client.async_control.assert_not_awaited()


async def test_scope_storage_unload_and_parameter_failures(
    hass, entry, app_client, statistics_device
):
    for start, end in (("202603", "202610"), ("202610", "202609")):
        with pytest.raises(HomeAssistantError) as exc:
            await call(hass, statistics_device, start_month=start, end_month=end)
        assert exc.value.translation_key == "statistics_range"
    entry.runtime_data.coordinator.statistics.available = False
    with pytest.raises(HomeAssistantError) as exc:
        await call(hass, statistics_device)
    assert exc.value.translation_key == "statistics_unavailable"
    entry.runtime_data.coordinator.statistics.available = True
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(HomeAssistantError) as exc:
        await call(hass, statistics_device)
    assert exc.value.translation_key == "query_unavailable"
    with pytest.raises(HomeAssistantError) as exc:
        await call(hass, "foreign-device")
    assert exc.value.translation_key == "query_device"


async def test_local_history_remains_readable_when_live_profile_expires_or_auth_fails(
    hass, entry, app_client, statistics_device
):
    co = entry.runtime_data.coordinator
    co.data["SyntheticSN"] = replace(co.data["SyntheticSN"], profile_freshness=Freshness())
    app_client.reset_mock()
    for authenticated in (True, False):
        co._authenticated = authenticated
        assert (await call(hass, statistics_device))["months"][1]["distance_km"] == 14.8
        co.async_update_listeners()
        await hass.async_block_till_done()
        yesterday = next(
            item
            for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
            if item.unique_id.endswith("yesterday_mileage")
        )
        assert hass.states.get(yesterday.entity_id).state not in {"unknown", "unavailable"}
        with pytest.raises(HomeAssistantError) as caught:
            await call(hass, statistics_device, refresh=True)
        assert caught.value.translation_key == "query_unavailable"
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_get_status.assert_not_awaited()
    co.data["SyntheticSN"] = replace(co.data["SyntheticSN"], present=False)
    with pytest.raises(HomeAssistantError):
        await call(hass, statistics_device)


def test_month_boundaries_are_six_and_not_implicit_full_history():
    assert months_between("202608", "202701") == (
        "202608",
        "202609",
        "202610",
        "202611",
        "202612",
        "202701",
    )


async def test_real_script_response_variable_is_a_usable_public_contract(
    hass, entry, statistics_device
):
    assert await async_setup_component(
        hass,
        "script",
        {
            "script": {
                "ninebot_period_test": {
                    "sequence": [
                        {
                            "action": "ninebot.get_statistics",
                            "data": {
                                "device_id": statistics_device,
                                "start_month": "202610",
                                "end_month": "202610",
                            },
                            "response_variable": "period",
                        },
                        {"stop": "Return period data", "response_variable": "period"},
                    ]
                }
            }
        },
    )
    result = await hass.services.async_call(
        "script", "ninebot_period_test", {}, blocking=True, return_response=True
    )
    assert result["summary"]["distance_km"] == 14.8 and result["days"][-2]["date"] == "2026-10-06"
    # Script response does not register a history entity or store a list in state.
    registry = er.async_get(hass)
    assert not any(
        row.unique_id.endswith("history_mileage")
        for row in er.async_entries_for_config_entry(registry, entry.entry_id)
    )
