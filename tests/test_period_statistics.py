"""Business-day replay based on observed relationships, not new cloud samples."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.models import Freshness
from custom_components.ninebot.period_statistics import day_summary
from custom_components.ninebot.sensor import DAY_FIELDS, SENSORS
from custom_components.ninebot.statistics_store import TravelStatisticsStore

NOW = datetime(2026, 10, 7, 4, tzinfo=UTC)
YESTERDAY = date(2026, 10, 6)


def sample(*, count=2, chart=True):
    """Synthetic times/IDs; metrics follow the two recorded final observations."""
    payload = {
        "total_mileages": 14.8,
        "times": count,
        "ec": 340,
        "duration": 2671,
        "list": [
            {
                "travel_id": "first",
                "start_time_format": "2026-10-06 19:21:26",
                "end_time_format": "2026-10-06 19:42:53",
                "mileages": 6.1,
                "duration": 1287,
                "ec": 145,
            },
            {
                "travel_id": "second",
                "start_time_format": "2026-10-06 20:02:16",
                "end_time_format": "2026-10-06 20:25:20",
                "mileages": 8.7,
                "duration": 1384,
                "ec": 195,
            },
        ],
    }
    if chart:
        payload["detail"] = [0] * 5 + [14.8] + [0] * 25
    return payload


def record(hass, raw=None, *, received=NOW):
    store = TravelStatisticsStore(hass, "entry")
    store.update("one", travel(raw if raw is not None else sample(), "202610"), received)
    return store


def test_two_rides_day_coverage_zero_and_correction(hass):
    store = record(hass)
    summary = day_summary(store, "one", YESTERDAY, NOW)
    assert (summary.distance_km, summary.ride_count, summary.duration_s, summary.energy_wh) == (
        14.8,
        2,
        2671,
        340,
    )
    assert summary.distance_basis == "server_daily_chart" and summary.rides_complete
    today = day_summary(store, "one", NOW.date(), NOW)
    assert (today.distance_km, today.ride_count, today.duration_s, today.energy_wh) == (0, 0, 0, 0)
    assert day_summary(store, "other", YESTERDAY, NOW).distance_km is None
    raw = sample()
    raw["list"][1].update(mileages=8, duration=1384, ec=180)
    raw["total_mileages"], raw["ec"], raw["detail"][5] = 14.1, 325, 14.1
    store.update("one", travel(raw, "202610"), NOW + timedelta(seconds=1))
    corrected = day_summary(store, "one", YESTERDAY, NOW + timedelta(seconds=1))
    assert (corrected.distance_km, corrected.ride_count, corrected.energy_wh) == (14.1, 2, 325)
    assert corrected.revision == 2


def test_chart_and_end_day_policy_do_not_force_agreement(hass):
    raw = sample()
    raw["detail"][5], raw["detail"][4] = 13, 1.8
    store = record(hass, raw)
    assert day_summary(store, "one", YESTERDAY, NOW).distance_km == 13
    fallback = day_summary(record(hass, sample(chart=False)), "one", YESTERDAY, NOW)
    assert fallback.distance_km == 14.8 and fallback.distance_basis == "returned_unique_rides"


def test_partial_list_missing_or_old_fields_do_not_make_false_daily_totals(hass):
    partial = day_summary(record(hass, sample(count=128)), "one", YESTERDAY, NOW)
    assert partial.distance_km == 14.8
    assert partial.ride_count is partial.duration_s is partial.energy_wh is None
    assert partial.reason("energy_wh") == "incomplete_month_list"
    store = record(hass)
    raw = sample()
    raw["list"][1].pop("ec")
    store.update("one", travel(raw, "202610"), NOW + timedelta(seconds=1))
    missing = day_summary(store, "one", YESTERDAY, NOW + timedelta(seconds=1))
    assert missing.ride_count == 2 and missing.duration_s == 2671
    assert missing.energy_wh is None and missing.reason("energy_wh") == "missing_metric"
    raw["list"][1].pop("end_time_format")
    store.update("one", travel(raw, "202610"), NOW + timedelta(seconds=2))
    assert day_summary(store, "one", YESTERDAY, NOW + timedelta(seconds=2)).ride_count is None


def test_time_disagreement_duration_and_stale_observation(hass):
    raw = sample()
    raw["list"][1]["duration"] = 200
    result = day_summary(record(hass, raw), "one", YESTERDAY, NOW)
    assert result.ride_count == 2 and result.energy_wh == 340 and result.duration_s is None
    raw["list"][1]["end_time"] = int(datetime(2026, 10, 6, 12, 30, tzinfo=UTC).timestamp())
    assert day_summary(record(hass, raw), "one", YESTERDAY, NOW).ride_count is None
    yesterday_sample = NOW - timedelta(days=1)
    store = record(hass, received=yesterday_sample)
    result = day_summary(store, "one", YESTERDAY, NOW)
    assert result.distance_km is result.ride_count is None
    assert result.reason("distance_km") == "day_not_observed"
    assert day_summary(record(hass), "one", NOW.date(), NOW + timedelta(hours=1)).ride_count is None
    assert (
        day_summary(record(hass), "one", YESTERDAY, NOW - timedelta(seconds=1)).ride_count is None
    )


def test_month_rollover_requires_adjacent_observation_and_handles_cross_midnight(hass):
    now = datetime(2026, 10, 1, 3, tzinfo=UTC)
    store = TravelStatisticsStore(hass, "entry")
    current = travel({"times": 0, "list": [], "detail": [0] * 31, "total_mileages": 0}, "202610")
    previous = travel(
        {
            "times": 1,
            "list": [
                {
                    "travel_id": "cross",
                    "start_time_format": "2026-09-30 23:58:00",
                    "end_time_format": "2026-10-01 00:02:00",
                    "mileages": 1,
                    "ec": 20,
                    "duration": 240,
                }
            ],
        },
        "202609",
    )
    store.update("one", current, now)
    assert day_summary(store, "one", date(2026, 10, 1), now).ride_count is None
    store.update("one", previous, now - timedelta(days=1))
    assert day_summary(store, "one", date(2026, 10, 1), now).ride_count is None
    store.update("one", previous, now)
    first = day_summary(store, "one", date(2026, 10, 1), now)
    assert (first.ride_count, first.duration_s, first.energy_wh) == (1, 240, 20)
    assert first.distance_km == 0  # Server day chart and end-day policy are separate.
    store.update("one", current, now + timedelta(days=1))
    assert day_summary(store, "one", date(2026, 10, 1), now + timedelta(days=1)).ride_count == 1


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_visible_entities_native_metadata_historical_cache_and_retirement(
    hass, entry, app_client, freezer
):
    freezer.move_to(NOW)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("ninebot", "SyntheticSN")}
    )
    registry = er.async_get(hass)
    retained = registry.async_get_or_create(
        "sensor", "ninebot", "SyntheticSN_month_mileage", config_entry=entry, device_id=device.id
    )
    registry.async_update_entity(retained.entity_id, name="My monthly distance")
    retired = [
        registry.async_get_or_create(
            "sensor", "ninebot", f"SyntheticSN_{key}", config_entry=entry, device_id=device.id
        )
        for key in (
            "history_scanned_months",
            "history_indexed_rides",
            "history_mileage",
            "history_energy",
            "history_duration",
        )
    ]
    app_client.async_get_travel.return_value = sample()
    with patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert all(registry.async_get(row.entity_id) is None for row in retired)
        assert registry.async_get(retained.entity_id).name == "My monthly distance"
        expected = {
            "yesterday_mileage": (14.8, "km", "distance"),
            "yesterday_ride_count": (2, None, None),
            "yesterday_ride_duration": (2671, "s", "duration"),
            "yesterday_ride_energy": (340, "Wh", "energy"),
            "today_ride_count": (0, None, None),
            "today_ride_duration": (0, "s", "duration"),
            "today_ride_energy": (0, "Wh", "energy"),
            "month_energy_intensity": (340 / 14.8, "Wh/km", "energy_distance"),
            "last_energy_intensity": (195 / 8.7, "Wh/km", "energy_distance"),
        }
        for key, (value, unit, device_class) in expected.items():
            eid = registry.async_get_entity_id("sensor", "ninebot", f"SyntheticSN_{key}")
            row = registry.async_get(eid)
            state = hass.states.get(eid)
            assert row.disabled_by is row.hidden_by is None
            assert float(state.state) == pytest.approx(value)
            assert state.attributes.get("unit_of_measurement") == unit
            assert state.attributes.get("device_class") == device_class
            assert (state.attributes.get("state_class") == "measurement") == key.endswith(
                "intensity"
            )
            assert len(json.dumps(dict(state.attributes))) < 1200
        co = entry.runtime_data.coordinator
        app_client.async_get_travel.reset_mock()
        co.async_update_listeners()
        await hass.async_block_till_done()
        app_client.async_get_travel.assert_not_awaited()
        app_client.async_get_trip_detail.assert_not_awaited()
        app_client.async_control.assert_not_awaited()
        # Yesterday is a stored observation with its source timestamp, not an
        # unbounded freshness extension borrowed from current cloud telemetry.
        co.data["SyntheticSN"] = replace(
            co.data["SyntheticSN"],
            travel_freshness=Freshness(),
        )
        co.async_update_listeners()
        await hass.async_block_till_done()
        eid = registry.async_get_entity_id("sensor", "ninebot", "SyntheticSN_yesterday_ride_energy")
        assert float(hass.states.get(eid).state) == 340


def test_all_new_fields_have_names_and_icons_and_no_history_placeholders():
    from pathlib import Path

    root = Path(__file__).parents[1] / "custom_components/ninebot"
    keys = set(DAY_FIELDS) | {d.key for d in SENSORS if d.key.endswith("intensity")}
    for path in [root / "strings.json", *(root / "translations").glob("*.json")]:
        sensors = json.loads(path.read_text())["entity"]["sensor"]
        assert all(sensors[key]["name"] for key in keys)
        assert not any(key.startswith("history_") for key in sensors)
    icons = json.loads((root / "icons.json").read_text())["entity"]["sensor"]
    assert all(icons[key]["default"] for key in keys)


def test_future_adjacent_receipt_cannot_prove_a_closed_window(hass):
    now = datetime(2026, 10, 2, 3, tzinfo=UTC)
    store = TravelStatisticsStore(hass, "entry")
    empty = {"times": 0, "list": [], "total_mileages": 0}
    store.update("one", travel(empty, "202610"), now)
    store.update("one", travel(empty, "202609"), now + timedelta(hours=1))
    result = day_summary(store, "one", date(2026, 10, 1), now)
    assert result.ride_count is None
    assert result.reason("ride_count") == "adjacent_month_not_observed"
