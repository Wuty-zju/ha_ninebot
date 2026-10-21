"""Daily statistics rebuild from synthetic capped pages after a clean install."""

from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.ninebot.period_statistics import day_summary
from custom_components.ninebot.raw import Endpoint, build_record

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
NOW = datetime(2026, 10, 7, 4, tzinfo=UTC)
SN = "SyntheticSN"


def payload(page):
    rows = []
    for index in range(37):
        start = NOW - timedelta(days=1 if index < 26 else 0, seconds=3600 - index * 60)
        rows.append(
            {
                "travel_id": f"synthetic-day-{index}",
                "mileages": 1,
                "duration": 30,
                "ec": 20,
                "start_time": int(start.timestamp()),
                "end_time": int(start.timestamp()) + 30,
            }
        )
    chart = [0] * 31
    chart[5], chart[6] = 26, 11
    return {
        "times": 37,
        "total_mileages": 37,
        "duration": 1110,
        "ec": 740,
        "detail": chart,
        "list": rows[(page - 1) * 20 : page * 20],
    }


async def setup(hass, entry, app_client, freezer, *, startup=False):
    freezer.move_to(NOW)
    app_client.async_get_travel.return_value = payload(1)

    async def page(sn, month, number):
        return build_record(
            Endpoint.TRAVEL, payload(number), dt_util.utcnow(), month, backend_version="0.1.7"
        )

    query = AsyncMock(side_effect=page)
    with (
        nullcontext()
        if startup
        else patch(
            "custom_components.ninebot.coordinator.NinebotCoordinator.async_schedule_statistics_refresh"
        ),
        patch(
            "custom_components.ninebot.coordinator.NinebotCoordinator.async_query_history_page",
            query,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry.runtime_data.coordinator, query


async def test_clean_account_repairs_daily_index_without_history_details(
    hass, entry, app_client, freezer
):
    co, query = await setup(hass, entry, app_client, freezer)
    assert co.statistics.month_rides(SN, "202610")[1] is False
    with patch.object(co, "async_query_history_page", query):
        await co.daily_sync.async_step(NOW)
        await co.daily_sync.async_step(NOW)  # Shared durable budget prevents another page.
        assert query.await_count == 1
        freezer.tick(61)
        await co.daily_sync.async_step(dt_util.utcnow())
        assert query.await_count == 2
        assert co.statistics.month_rides(SN, "202610")[1] is True
        today = day_summary(co.statistics, SN, date(2026, 10, 7), dt_util.utcnow())
        yesterday = day_summary(co.statistics, SN, date(2026, 10, 6), dt_util.utcnow())
        assert (today.ride_count, today.duration_s, today.energy_wh) == (11, 330, 220)
        assert (yesterday.ride_count, yesterday.duration_s, yesterday.energy_wh) == (26, 780, 520)
        assert today.completeness_basis == "local_reconciled"
        assert today.energy_intensity_wh_per_km == 20
        freezer.tick(61)
        await co.daily_sync.async_step(dt_util.utcnow())
        assert query.await_count == 2  # Retained complete facts never cause another full scan.
    app_client.async_get_trip_detail.assert_not_awaited()
    assert entry.options.get("preserve_history", False) is False
    assert entry.options.get("auto_history_sync", False) is False
    assert co.daily_sync.cursors == {}


async def test_daily_repair_needs_enabled_daily_consumers(hass, entry, app_client, freezer):
    co, query = await setup(hass, entry, app_client, freezer)
    with (
        patch.object(co, "async_contexts", return_value=()),
        patch.object(co, "async_query_history_page", query),
    ):
        await co.daily_sync.async_step(NOW)
    query.assert_not_awaited()


async def test_daily_repeated_page_backs_off_and_keeps_gap(hass, entry, app_client, freezer):
    co, query = await setup(hass, entry, app_client, freezer)
    query.side_effect = None
    query.return_value = build_record(Endpoint.TRAVEL, payload(1), NOW, "202610")
    with patch.object(co, "async_query_history_page", query):
        await co.daily_sync.async_step(NOW)
        freezer.tick(61)
        await co.daily_sync.async_step(dt_util.utcnow())
        assert query.await_count == 2
        await co.daily_sync.async_step(dt_util.utcnow())
        assert query.await_count == 2
    assert co.statistics.month_rides(SN, "202610")[1] is False
    result = day_summary(co.statistics, SN, NOW.date(), dt_util.utcnow())
    assert result.ride_count is None


async def test_daily_repair_respects_persistent_pause(hass, entry, app_client, freezer):
    co, query = await setup(hass, entry, app_client, freezer)
    await co.statistics.archive.async_set_history_paused(SN, True, guard=lambda: True)
    with patch.object(co, "async_query_history_page", query):
        await co.daily_sync.async_step(NOW)
    query.assert_not_awaited()


async def test_first_setup_admits_daily_paging_without_waiting_status_period(
    hass, entry, app_client, freezer
):
    co, query = await setup(hass, entry, app_client, freezer, startup=True)
    query.assert_awaited_once_with(SN, "202610", 1)
    assert co.daily_sync.cursors[(SN, "202610")].page == 2
    assert app_client.async_get_status.await_count == 1
    assert app_client.async_get_travel.await_count == 1
    app_client.async_get_trip_detail.assert_not_awaited()


async def test_daily_repair_has_an_independent_cadence_even_without_hourly_quota(
    hass, entry, app_client, freezer
):
    hass.config_entries.async_update_entry(entry, options={"history_requests_per_hour": 0})
    co, query = await setup(hass, entry, app_client, freezer)
    with patch.object(co, "async_query_history_page", query):
        await co.daily_sync.async_step(dt_util.utcnow())
        for _ in range(29):
            freezer.tick(2)
            await co.daily_sync.async_step(dt_util.utcnow())
        assert query.await_count == 1
        freezer.tick(2)
        await co.daily_sync.async_step(dt_util.utcnow())
        assert query.await_count == 2
    app_client.async_get_trip_detail.assert_not_awaited()
