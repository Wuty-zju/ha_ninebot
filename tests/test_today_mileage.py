"""Synthetic calendar/freshness replay; no new vehicle or daily-chart evidence."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot import adapters
from custom_components.ninebot.demand import Need, entity_context
from custom_components.ninebot.models import Freshness, VehicleProfile, VehicleSnapshot
from custom_components.ninebot.sensor import today_mileage

NOW = datetime(2026, 9, 25, 15, 59, 59, tzinfo=UTC)


def sample():
    return VehicleSnapshot(
        VehicleProfile("test", "Vehicle", "Model"),
        travel=adapters.travel({"detail": [2] * 30, "total_mileages": 60, "times": 0}, "202609"),
        travel_freshness=Freshness(NOW, NOW),
    )


def test_business_day_midnight_and_zero_are_not_old_sample_carryover():
    snapshot = sample()
    assert today_mileage(snapshot, NOW) == 2
    assert today_mileage(snapshot, NOW + timedelta(seconds=1)) is None
    assert today_mileage(snapshot, NOW + timedelta(days=6)) is None
    assert today_mileage(snapshot, NOW - timedelta(seconds=1)) is None
    zero = replace(
        snapshot, travel=adapters.travel({"detail": [0] * 30, "total_mileages": 0}, "202609")
    )
    assert today_mileage(zero, NOW) == 0
    assert entity_context("test", "sensor", "today_mileage", "travel").need is Need.MONTH


@pytest.mark.parametrize(
    "change", ["absent", "missing", "summary", "invalid", "old", "missing_day", "no_success"]
)
def test_today_invalid_or_missing_data_is_unknown(change):
    snapshot = sample()
    if change == "absent":
        snapshot = replace(snapshot, present=False)
    elif change == "missing":
        snapshot = replace(snapshot, travel=None)
    elif change == "summary":
        snapshot = replace(snapshot, travel=replace(snapshot.travel, summary=None))
    elif change == "invalid":
        snapshot = replace(
            snapshot, travel=adapters.travel({"detail": [2] * 30, "total_mileages": 0}, "202609")
        )
    elif change == "old":
        snapshot = replace(snapshot, travel_freshness=Freshness(NOW, NOW - timedelta(hours=1)))
    elif change == "missing_day":
        snapshot = replace(
            snapshot,
            travel=replace(
                snapshot.travel, summary=replace(snapshot.travel.summary, daily_mileage=())
            ),
        )
    else:
        snapshot = replace(snapshot, travel_freshness=Freshness(attempted_at=NOW))
    assert today_mileage(snapshot, NOW) is None


async def test_visible_daily_sensor_native_metadata_small_state_and_no_detail_queries(
    hass, entry, app_client, freezer, enable_custom_integrations
):
    freezer.move_to(NOW)
    app_client.async_get_travel.return_value = {
        "detail": [2] * 30,
        "total_mileages": 60,
        "times": 0,
    }
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", "ninebot", "SyntheticSN_today_mileage")
    row = registry.async_get(entity_id)
    assert row.disabled_by is row.hidden_by is None
    state = hass.states.get(entity_id)
    assert float(state.state) == 2
    assert state.attributes["unit_of_measurement"] == "km"
    assert state.attributes["device_class"] == "distance"
    assert "state_class" not in state.attributes
    assert len(json.dumps(dict(state.attributes))) < 1000
    co = entry.runtime_data.coordinator
    app_client.async_get_travel.reset_mock()
    freezer.tick(timedelta(seconds=1))
    co.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unknown"
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_get_trip_detail.assert_not_awaited()
    app_client.async_control.assert_not_awaited()


def test_today_name_and_icon_exist_in_all_shipped_languages():
    root = Path(__file__).parents[1] / "custom_components/ninebot"
    for path in [root / "strings.json", *(root / "translations").glob("*.json")]:
        assert json.loads(path.read_text())["entity"]["sensor"]["today_mileage"]["name"]
    assert (
        json.loads((root / "icons.json").read_text())["entity"]["sensor"]["today_mileage"][
            "default"
        ]
        == "mdi:map-marker-distance"
    )
