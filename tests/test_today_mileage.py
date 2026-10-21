"""Synthetic calendar/freshness replay; no new vehicle or daily-chart evidence."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot import adapters
from custom_components.ninebot.demand import Need, entity_context
from custom_components.ninebot.period_statistics import day_summary
from custom_components.ninebot.statistics_store import TravelStatisticsStore

NOW = datetime(2026, 9, 25, 15, 59, 59, tzinfo=UTC)


def chart_value(hass, raw, received=NOW, when=NOW, month="202609"):
    store = TravelStatisticsStore(hass, "chart-contract")
    if raw is not None:
        store.update("test", adapters.travel(raw, month), received)
    return day_summary(
        store, "test", when.astimezone(ZoneInfo("Asia/Shanghai")).date(), when
    ).distance_km


def test_business_day_midnight_and_zero_are_not_old_sample_carryover(hass):
    raw = {"detail": [2] * 30, "total_mileages": 60}
    assert chart_value(hass, raw) == 2
    assert chart_value(hass, raw, when=NOW + timedelta(seconds=1)) is None
    assert chart_value(hass, raw, when=NOW + timedelta(days=6)) is None
    assert chart_value(hass, raw, when=NOW - timedelta(seconds=1)) is None
    assert chart_value(hass, {"detail": [0] * 30, "total_mileages": 0}) == 0
    assert entity_context("test", "sensor", "today_mileage", "travel").need is Need.DAY


@pytest.mark.parametrize("change", ["missing", "chart", "invalid", "old", "missing_day", "future"])
def test_today_invalid_or_missing_data_is_unknown(hass, change):
    raw = {"detail": [2] * 30, "total_mileages": 60}
    received = NOW
    if change == "missing":
        raw = None
    elif change == "chart":
        raw = {}
    elif change == "invalid":
        raw["total_mileages"] = 0
    elif change == "old":
        received = NOW - timedelta(hours=1)
    elif change == "missing_day":
        raw["detail"] = []
    else:
        received = NOW + timedelta(seconds=1)
    assert chart_value(hass, raw, received=received) is None


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
