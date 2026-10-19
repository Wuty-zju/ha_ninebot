"""Period-correct entity continuity from synthetic normalized archive facts."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.archive_runtime import ArchiveStatistics
from custom_components.ninebot.exceptions import ErrorKind, NinebotError
from custom_components.ninebot.models import VehicleProfile
from custom_components.ninebot.period_statistics import day_summary
from custom_components.ninebot.statistics_actions import statistics_response

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
NOW = datetime(2026, 10, 7, 4, tzinfo=UTC)
SN = "SyntheticSN"


def payload():
    rows = []
    for key, day, km, energy, seconds in (("older", 6, 2, 30, 60), ("latest", 7, 1, 40, 30)):
        start = datetime(2026, 10, day, 3, tzinfo=UTC)
        rows.append(
            {
                "travel_id": key,
                "id": "detail-" + key,
                "mileages": km,
                "ec": energy,
                "duration": seconds,
                "speed": 25,
                "start_time": int(start.timestamp()),
                "end_time": int(start.timestamp()) + seconds,
            }
        )
    chart = [0] * 31
    chart[5], chart[6] = 2, 1
    return {
        "times": 2,
        "total_mileages": 3,
        "ec": 70,
        "duration": 90,
        "detail": chart,
        "list": rows,
    }


def state(hass, entry, key):
    registry = er.async_get(hass)
    uid = entry.runtime_data.identities.unique_id(SN, key)
    return hass.states.get(registry.async_get_entity_id("sensor", "ninebot", uid))


async def setup(hass, entry, app_client, freezer):
    freezer.move_to(NOW)
    app_client.async_get_travel.return_value = payload()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data.coordinator


async def test_archive_updates_all_daily_and_month_entities_without_another_cloud_poll(
    hass, entry, app_client, freezer
):
    co = await setup(hass, entry, app_client, freezer)
    before_ids = {
        key: state(hass, entry, key).entity_id
        for key in ("today_mileage", "today_ride_energy", "month_energy_raw")
    }
    raw = payload()
    raw["list"][1]["ec"], raw["ec"] = 50, 80
    changed = NOW + timedelta(seconds=1)
    freezer.move_to(changed)
    app_client.reset_mock()
    await co.statistics.async_record(SN, travel(raw, "202610"), changed, "0.1.7")
    await hass.async_block_till_done()
    assert float(state(hass, entry, "today_ride_energy").state) == 50
    assert float(state(hass, entry, "today_energy_intensity").state) == 50
    assert float(state(hass, entry, "month_energy_raw").state) == 80
    assert state(hass, entry, "month_energy_raw").attributes["source"] == "ride_archive"
    assert all(state(hass, entry, key).entity_id == eid for key, eid in before_ids.items())
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_get_trip_detail.assert_not_awaited()


async def test_current_period_values_survive_stale_live_data_and_short_failure(
    hass, entry, app_client, freezer
):
    co = await setup(hass, entry, app_client, freezer)
    freezer.move_to(NOW + timedelta(hours=1))
    snapshot = co.data[SN]
    co.data[SN] = replace(
        snapshot, travel_freshness=replace(snapshot.travel_freshness, error=ErrorKind.CONNECTION)
    )
    co.async_update_listeners()
    await hass.async_block_till_done()
    expected = {
        "month_mileage": 3,
        "month_energy_raw": 70,
        "month_duration": 90 / 3600,
        "today_mileage": 1,
        "today_ride_count": 1,
        "today_ride_energy": 40,
        "today_ride_duration": 30 / 3600,
        "today_energy_intensity": 40,
        "yesterday_mileage": 2,
        "yesterday_ride_energy": 30,
        "last_mileage": 1,
    }
    for key, value in expected.items():
        current = state(hass, entry, key)
        assert float(current.state) == pytest.approx(value), key
        assert current.attributes["data_fresh"] is False
    assert state(hass, entry, "today_ride_count").attributes["observation_state"] == "cached"
    assert state(hass, entry, "month_mileage").attributes["received_at"] == NOW.isoformat()


async def test_midnight_keeps_yesterday_provisional_without_fabricating_new_today(
    hass, entry, app_client, freezer
):
    co = await setup(hass, entry, app_client, freezer)
    after = datetime(2026, 10, 7, 16, tzinfo=UTC)
    freezer.move_to(after)
    co.async_update_listeners()
    await hass.async_block_till_done()
    assert float(state(hass, entry, "yesterday_ride_count").state) == 1
    assert float(state(hass, entry, "yesterday_mileage").state) == 1
    assert (
        state(hass, entry, "yesterday_ride_count").attributes["observation_state"]
        == "awaiting_day_close"
    )
    assert state(hass, entry, "today_ride_count").state == "unknown"
    # Strict Action/statistics projections still need post-close evidence.
    strict = day_summary(co.statistics, SN, date(2026, 10, 7), after)
    assert strict.ride_count is None
    response = statistics_response(co.statistics, SN, ("202610",), after, True)
    day = next(row for row in response["days"] if row["date"] == "2026-10-07")
    assert day["ride_count"] is None
    await co.async_refresh()
    assert float(state(hass, entry, "today_ride_count").state) == 0
    assert float(state(hass, entry, "today_mileage").state) == 0
    assert state(hass, entry, "today_energy_intensity").state == "unknown"
    assert (
        state(hass, entry, "today_energy_intensity").attributes["availability_reason"]
        == "zero_distance"
    )


async def test_new_month_does_not_present_previous_month_as_current(
    hass, entry, app_client, freezer
):
    co = await setup(hass, entry, app_client, freezer)
    freezer.move_to(datetime(2026, 10, 31, 16, tzinfo=UTC))
    # Reproject locally, without querying November or renewing any receipt.
    await co.statistics.async_refresh_projection()
    co.async_update_listeners()
    await hass.async_block_till_done()
    assert state(hass, entry, "month_mileage").state in {"unknown", "unavailable"}
    assert state(hass, entry, "today_mileage").state == "unknown"
    assert float(state(hass, entry, "last_mileage").state) == 1


async def test_offline_cold_start_restores_same_period_without_renewing_observation(
    hass, entry, app_client, freezer
):
    freezer.move_to(NOW)
    saved = ArchiveStatistics(hass, entry.entry_id, entry.data["business_uid"])
    try:
        await saved.async_load()
        await saved.async_record_profiles(
            (VehicleProfile(SN, "Scooter", "Test"),), NOW, True, lambda: True
        )
        await saved.async_record(SN, travel(payload(), "202610"), NOW, "0.1.7")
    finally:
        await saved.async_close()
    freezer.move_to(NOW + timedelta(hours=1))
    app_client.async_list_vehicles.side_effect = NinebotError(ErrorKind.CONNECTION)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not entry.runtime_data.coordinator.last_update_success
    assert not entry.runtime_data.coordinator._authenticated
    for key, expected in (
        ("month_mileage", 3),
        ("today_ride_count", 1),
        ("yesterday_mileage", 2),
        ("last_mileage", 1),
    ):
        current = state(hass, entry, key)
        assert float(current.state) == expected, key
        assert current.attributes["data_fresh"] is False
    assert state(hass, entry, "month_mileage").attributes["received_at"] == NOW.isoformat()
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_get_trip_detail.assert_not_awaited()
