"""Native SQLite read-back, explicit write scope and correction semantics."""

from datetime import UTC, datetime, timedelta
from functools import partial
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.ninebot import adapters
from custom_components.ninebot.compat import validation as vol
from custom_components.ninebot.statistics_export import (
    MAX_SERIES_POINTS,
    bucket,
    merge_periods,
    statistic_id,
    valid_value,
)

NOW = datetime(2026, 10, 7, 4, tzinfo=UTC)
START = bucket("2026-09-01", "day")


@pytest.fixture
async def export_device(
    recorder_mock, hass, entry, app_client, freezer, enable_custom_integrations
):
    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("Asia/Shanghai")
    # Nonempty current list keeps bootstrap from incidentally fetching September.
    app_client.async_get_travel.return_value = {"times": 1, "list": [{"travel_id": "synthetic"}]}
    with patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield next(
            device.id
            for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
            if ("ninebot", "SyntheticSN") in device.identifiers
        )


async def record_month(entry, corrected=False):
    # Synthetic summary deliberately keeps server daily distance independent
    # from the complete empty returned-ride window. No energy distribution.
    await entry.runtime_data.coordinator.statistics.async_record(
        "SyntheticSN",
        adapters.travel(
            {
                "total_mileages": 2 if corrected else 3,
                "ec": 15,
                "times": 0,
                "duration": 0,
                "list": [],
                "detail": ([0.5, 0, 1.5] if corrected else [1, 0, 2]) + [0] * 27,
            },
            "202609",
        ),
        NOW + timedelta(seconds=1) if corrected else NOW,
        "0.1.7",
    )


async def call(hass, device, **data):
    return await hass.services.async_call(
        "ninebot",
        "import_statistics",
        {"device_id": device, "start_month": "202609", "end_month": "202609", **data},
        blocking=True,
        return_response=True,
    )


async def read(recorder_mock, hass, sid, period="day"):
    await async_wait_recording_done(hass)
    result = await recorder_mock.async_add_executor_job(
        partial(
            statistics_during_period,
            hass,
            START,
            NOW,
            {sid},
            period,
            None,
            {"change", "state", "sum"},
        )
    )
    return result.get(sid, [])


async def test_real_action_imports_days_and_months_without_cloud_or_current_mutation(
    recorder_mock, hass, entry, app_client, export_device, freezer
):
    assert hass.services.supports_response("ninebot", "import_statistics") is SupportsResponse.ONLY
    assert (await call(hass, export_device))["status"] == "no_closed_data"
    await record_month(entry)
    co = entry.runtime_data.coordinator
    current = co.data["SyntheticSN"]
    app_client.async_get_travel.reset_mock()
    response = await call(hass, export_device)
    assert response["status"] == "queued" and len(response["series"]) == 8
    assert (
        sum(item["source_points"] for item in response["series"]) == 121
    )  # first-day ride window unknown
    daily_id = statistic_id(entry.entry_id, "SyntheticSN", "day", "distance_km")
    monthly_id = statistic_id(entry.entry_id, "SyntheticSN", "month", "distance_km")
    days = await read(recorder_mock, hass, daily_id)
    assert [row["change"] for row in days[:3]] == [1, 0, 2]
    assert len(days) == 30
    assert [
        datetime.fromtimestamp(row["start"], ZoneInfo("Asia/Shanghai")).day for row in days[:3]
    ] == [1, 2, 3]
    month = await read(recorder_mock, hass, monthly_id, "month")
    assert len(month) == 1 and month[0]["change"] == 3
    assert co.data["SyntheticSN"] is current
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_get_trip_detail.assert_not_awaited()
    app_client.async_control.assert_not_awaited()
    assert not any("SyntheticSN" in item["statistic_id"] for item in response["series"])
    freezer.tick(timedelta(seconds=1))
    await record_month(entry, corrected=True)
    await call(hass, export_device)
    corrected = await read(recorder_mock, hass, daily_id)
    assert [row["change"] for row in corrected[:3]] == [0.5, 0, 1.5]
    assert corrected[-1]["sum"] == 2
    assert (await read(recorder_mock, hass, monthly_id, "month"))[0]["change"] == 2
    await call(hass, export_device)
    assert await read(recorder_mock, hass, daily_id) == corrected


async def test_real_recorder_sparse_buckets_and_full_suffix_replacement(recorder_mock, hass):
    await hass.config.async_set_time_zone("Asia/Shanghai")
    sid = statistic_id("entry", "vehicle", "day", "distance_km")
    metadata = {
        "statistic_id": sid,
        "source": "ninebot",
        "name": "Daily distance",
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "unit_class": "distance",
        "unit_of_measurement": "km",
    }
    rows = merge_periods(
        [],
        {START: 1, START + timedelta(days=1): 0, START + timedelta(days=3): 2},
        "day",
        "distance_km",
        NOW,
    )
    async_add_external_statistics(hass, metadata, rows)
    first = await read(recorder_mock, hass, sid)
    assert [row["change"] for row in first] == [1, 0, 2]
    # Query the original hour buckets, not an aggregate with altered start times.
    existing = await read(recorder_mock, hass, sid, "hour")
    changed = merge_periods(existing, {START: 0.5}, "day", "distance_km", NOW)
    assert len(changed) == 3 and changed[-1]["sum"] == 2.5
    async_add_external_statistics(hass, metadata, changed)
    second = await read(recorder_mock, hass, sid)
    assert [row["change"] for row in second] == [0.5, 0, 2]
    assert [
        datetime.fromtimestamp(row["start"], ZoneInfo("Asia/Shanghai")).day for row in second
    ] == [1, 2, 4]


async def test_timezone_scope_queue_and_unload_guards(recorder_mock, hass, entry, export_device):
    await hass.config.async_set_time_zone("UTC")
    with pytest.raises(HomeAssistantError) as exc:
        await call(hass, export_device)
    assert exc.value.translation_key == "statistics_timezone"
    await hass.config.async_set_time_zone("Asia/Shanghai")
    with pytest.raises(vol.Invalid):
        await call(hass, export_device, refresh=True)
    runtime = entry.runtime_data
    runtime.statistics_import_pending = 2
    with pytest.raises(HomeAssistantError) as exc:
        await call(hass, export_device)
    assert exc.value.translation_key == "busy"
    runtime.statistics_import_pending = 0
    with pytest.raises(HomeAssistantError) as exc:
        await call(hass, "unknown")
    assert exc.value.translation_key == "query_device"
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(HomeAssistantError) as exc:
        await call(hass, export_device)
    assert exc.value.translation_key == "query_unavailable"


async def test_no_recorder_does_not_install_or_start_it(
    hass, entry, app_client, freezer, enable_custom_integrations
):
    freezer.move_to(NOW)
    with patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"):
        assert await hass.config_entries.async_setup(entry.entry_id)
    device = next(
        d.id
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if ("ninebot", "SyntheticSN") in d.identifiers
    )
    with pytest.raises(HomeAssistantError) as exc:
        await call(hass, device)
    assert exc.value.translation_key == "statistics_recorder"


async def test_foreign_metadata_rejects_whole_batch_before_queue(
    recorder_mock, hass, entry, export_device
):
    await record_month(entry)
    sid = statistic_id(entry.entry_id, "SyntheticSN", "month", "energy_wh")
    async_add_external_statistics(
        hass,
        {
            "statistic_id": sid,
            "source": "ninebot",
            "name": "Wrong units",
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "unit_class": "energy",
            "unit_of_measurement": "kWh",
        },
        [{"start": START, "state": 1, "sum": 1}],
    )
    await async_wait_recording_done(hass)
    with pytest.raises(HomeAssistantError) as exc:
        await call(hass, export_device)
    assert exc.value.translation_key == "statistics_import_data"
    empty_sid = statistic_id(entry.entry_id, "SyntheticSN", "day", "distance_km")
    assert await read(recorder_mock, hass, empty_sid) == []
    assert entry.runtime_data.statistics_import_pending == 0


@pytest.mark.parametrize("value", [None, True, "1", -1, float("nan"), float("inf"), 10**1000])
def test_reject_unknown_or_invalid_metric(value):
    assert not valid_value(value, "distance_km")


@pytest.mark.parametrize(
    "row",
    [
        {"start": True, "state": 1},
        {"start": float("nan"), "state": 1},
        {"start": 1e100, "state": 1},
        {"start": START.timestamp(), "state": -1},
        {"start": (START + timedelta(hours=1)).timestamp(), "state": 1},
        {"start": NOW.timestamp(), "state": 1},
    ],
)
def test_reject_corrupt_existing_buckets(row):
    with pytest.raises(HomeAssistantError):
        merge_periods([row], {}, "day", "distance_km", NOW)


def test_identity_count_and_budget_guards():
    assert statistic_id("a", "car", "day", "distance_km") != statistic_id(
        "b", "car", "day", "distance_km"
    )
    assert not valid_value(1.5, "ride_count")
    with pytest.raises(HomeAssistantError):
        merge_periods(
            [{"start": START.timestamp(), "state": 1}] * (MAX_SERIES_POINTS + 1),
            {},
            "day",
            "distance_km",
            NOW,
        )
    with pytest.raises(HomeAssistantError):
        merge_periods(
            [{"start": (START + timedelta(days=1)).timestamp(), "state": 1}],
            {},
            "month",
            "distance_km",
            NOW,
        )
    with pytest.raises(HomeAssistantError):
        merge_periods([{"start": START.timestamp(), "state": 1}] * 2, {}, "day", "distance_km", NOW)
    with pytest.raises(HomeAssistantError):
        merge_periods(
            [], {START: 1e308, START + timedelta(days=1): 1e308}, "day", "distance_km", NOW
        )


async def test_waiting_import_cancel_and_unload_cannot_queue_old_owner(
    recorder_mock, hass, entry, export_device
):
    import asyncio

    runtime = entry.runtime_data
    await runtime.statistics_import_lock.acquire()
    first = asyncio.create_task(call(hass, export_device))
    second = asyncio.create_task(call(hass, export_device))
    await asyncio.sleep(0)
    assert runtime.statistics_import_pending == 2
    with pytest.raises(HomeAssistantError) as exc:
        await call(hass, export_device)
    assert exc.value.translation_key == "busy"
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert runtime.statistics_import_pending == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    runtime.statistics_import_lock.release()
    with pytest.raises(HomeAssistantError) as exc:
        await second
    assert exc.value.translation_key == "query_unavailable"
    assert runtime.statistics_import_pending == 0


async def test_native_chart_websocket_contract(
    recorder_mock, hass, entry, export_device, hass_ws_client
):
    await record_month(entry)
    result = await call(hass, export_device)
    await async_wait_recording_done(hass)
    assert len(result["dashboard_cards"]) == 8
    card = next(item for item in result["dashboard_cards"] if item["period"] == "day")
    assert card["chart_type"] == "bar" and card["stat_types"] == ["change"]
    sid = card["entities"][0]["entity"]
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "recorder/statistics_during_period",
            "start_time": START.isoformat(),
            "end_time": NOW.isoformat(),
            "statistic_ids": [sid],
            "period": card["period"],
            "types": card["stat_types"],
        }
    )
    data = await client.receive_json()
    assert data["success"]
    assert [row["change"] for row in data["result"][sid][:3]] == [1, 0, 2]
    assert data["result"][sid][0]["start"] == int(START.timestamp() * 1000)
