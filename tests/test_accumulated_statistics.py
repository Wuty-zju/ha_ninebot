"""Synthetic replay of repeated capped lists, not a copied private vehicle fixture."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import pytest

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.archive_runtime import ArchiveStatistics
from custom_components.ninebot.models import VehicleProfile
from custom_components.ninebot.period_statistics import day_summary
from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure
from custom_components.ninebot.statistics_actions import statistics_response

NOW = datetime(2026, 10, 7, 4, tzinfo=UTC)
SN = "synthetic-statistics-vehicle"
YESTERDAY = date(2026, 10, 6)


def capped_month(count=37, *, first=None):
    """37 generated IDs over two synthetic days, no production dates or values."""
    first = max(0, count - 20) if first is None else first
    rows = []
    for index in range(first, count):
        day = 4 if index < 26 else 6
        start = datetime(2026, 10, day, 4, tzinfo=UTC) + timedelta(seconds=index * 120)
        rows.append(
            {
                "travel_id": f"synthetic-stat-{index:02}",
                "mileages": 1,
                "duration": 60,
                "ec": 30,
                "speed": 20,
                "start_time": int(start.timestamp()),
                "end_time": int(start.timestamp()) + 60,
            }
        )
    chart = [0] * 31
    chart[3], chart[5] = min(count, 26), max(0, count - 26)
    return {
        "times": count,
        "total_mileages": count,
        "ec": count * 30,
        "duration": count * 60,
        "detail": chart,
        "list": rows,
    }


async def seed(store):
    await store.async_record_profiles(
        (VehicleProfile(SN, "Synthetic", "Model"),), NOW, True, lambda: True
    )
    for offset, count in enumerate((20, 21, 37)):
        await store.async_record(
            SN, travel(capped_month(count), "202610"), NOW + timedelta(seconds=offset)
        )


async def test_capped_list_threshold_accumulation_action_and_restart(hass, freezer):
    freezer.move_to(NOW)
    store = ArchiveStatistics(hass, "accumulated", "owner")
    await store.async_load()
    try:
        await store.async_record_profiles(
            (VehicleProfile(SN, "Synthetic", "Model"),), NOW, True, lambda: True
        )
        for offset, count in enumerate((20, 21, 37)):
            received = NOW + timedelta(seconds=offset)
            await store.async_record(SN, travel(capped_month(count), "202610"), received)
            raw_month = await store.archive.async_month(SN, "202610")
            assert len(raw_month.travel.rides) == 20
            assert raw_month.known_ride_count == count
            assert raw_month.complete is (count == 20)
            assert raw_month.travel.summary.coverage == 20 / count
            rows, complete = store.month_rides(SN, "202610")
            assert complete and len(rows) == count
            assert day_summary(store, SN, date(2026, 10, 4), received).ride_count == min(count, 26)
        summary = day_summary(store, SN, YESTERDAY, received)
        assert (summary.ride_count, summary.duration_s, summary.energy_wh) == (11, 660, 330)
        assert summary.completeness_basis == "local_reconciled"
        today = day_summary(store, SN, NOW.date(), received)
        assert (today.ride_count, today.duration_s, today.energy_wh) == (0, 0, 0)
        stale = day_summary(store, SN, NOW.date(), received + timedelta(hours=1))
        assert stale.ride_count is None and stale.reason("ride_count") == "day_not_observed"
        response = statistics_response(
            await store.async_view(SN, ("202610",)), SN, ("202610",), received, True
        )
        row = next(item for item in response["days"] if item["date"] == YESTERDAY.isoformat())
        assert (row["ride_count"], row["duration_s"], row["energy_wh"]) == (11, 660, 330)
        assert row["completeness_basis"] == "local_reconciled"
        assert response["months"][0]["list_complete"] is False
    finally:
        await store.async_close()
    restarted = ArchiveStatistics(hass, "accumulated", "owner")
    try:
        await restarted.async_load()
        restored = day_summary(restarted, SN, YESTERDAY, received)
        assert restored.ride_count == 11 and restored.energy_wh == 330
        assert restored.completeness_basis == "local_reconciled"
    finally:
        await restarted.async_close()


async def test_only_twenty_of_thirty_seven_remains_unknown(hass, freezer):
    freezer.move_to(NOW)
    store = ArchiveStatistics(hass, "partial", "owner")
    try:
        await store.async_load()
        await store.async_record(SN, travel(capped_month(), "202610"), NOW)
        summary = day_summary(store, SN, YESTERDAY, NOW)
        assert summary.distance_km == 11
        assert summary.ride_count is summary.duration_s is summary.energy_wh is None
        assert summary.reason("ride_count") == "incomplete_month_list"
    finally:
        await store.async_close()


@pytest.mark.parametrize("field", ["ec", "duration", "missing_energy"])
async def test_reconciled_metrics_fail_independently(hass, freezer, field):
    freezer.move_to(NOW)
    store = ArchiveStatistics(hass, "metrics", "owner")
    try:
        await store.async_load()
        await seed(store)
        raw = capped_month()
        if field == "missing_energy":
            # A newly observed identity with absent energy, not a retained
            # historical fact from a prior successful observation.
            raw["list"][-1]["travel_id"] = "synthetic-new-without-energy"
            raw["list"][-1].pop("ec")
            complete = capped_month(first=0)
            complete["list"][-1] = deepcopy(raw["list"][-1])
            await store.async_record(SN, travel(complete, "202610"), NOW + timedelta(seconds=3))
        else:
            raw[field] += 100
            await store.async_record(SN, travel(raw, "202610"), NOW + timedelta(seconds=3))
        summary = day_summary(store, SN, YESTERDAY, NOW + timedelta(seconds=3))
        assert summary.ride_count == 11
        if field in {"ec", "missing_energy"}:
            assert summary.energy_wh is None and summary.duration_s == 660
        else:
            assert summary.duration_s is None and summary.energy_wh == 330
    finally:
        await store.async_close()


@pytest.mark.parametrize("problem", ["distance", "count", "future", "duration_conflict"])
async def test_same_count_is_not_enough_and_time_conflicts_are_guarded(hass, freezer, problem):
    freezer.move_to(NOW)
    store = ArchiveStatistics(hass, "conflicts", "owner")
    try:
        await store.async_load()
        await seed(store)
        raw = capped_month()
        if problem == "distance":
            raw["total_mileages"] += 10
        elif problem == "count":
            raw["times"] -= 1
        elif problem == "future":
            raw["list"][-1]["start_time"] = int(NOW.timestamp()) + 60
            raw["list"][-1]["end_time"] = int(NOW.timestamp()) + 120
        else:
            raw["list"][-1]["duration"] = 999
            raw["duration"] += 939
        await store.async_record(SN, travel(raw, "202610"), NOW + timedelta(seconds=3))
        summary = day_summary(store, SN, YESTERDAY, NOW + timedelta(seconds=3))
        if problem == "duration_conflict":
            assert summary.ride_count == 11 and summary.energy_wh == 330
            assert summary.duration_s is None
        else:
            assert summary.ride_count is summary.duration_s is summary.energy_wh is None
    finally:
        await store.async_close()


async def test_complete_omission_and_other_vehicle_do_not_pollute_statistics(hass, freezer):
    freezer.move_to(NOW)
    store = ArchiveStatistics(hass, "scope", "owner")
    try:
        await store.async_load()
        await seed(store)
        await store.async_record("other", travel(capped_month(), "202610"), NOW)
        assert day_summary(store, SN, YESTERDAY, NOW + timedelta(seconds=2)).ride_count == 11
        assert day_summary(store, "other", YESTERDAY, NOW + timedelta(seconds=2)).ride_count is None
        raw = capped_month()
        raw["times"], raw["total_mileages"], raw["ec"], raw["duration"] = 20, 20, 600, 1200
        raw.pop("detail")
        await store.async_record(SN, travel(raw, "202610"), NOW + timedelta(seconds=3))
        rows, complete = store.month_rides(SN, "202610")
        assert complete and len(rows) == 20
        assert store.month_completeness_basis(SN, "202610") == "upstream_complete"
        archived = await store.archive.async_month(SN, "202610")
        assert archived.known_ride_count == 37  # Evidence is retained, not counted after omission.
    finally:
        await store.async_close()


async def test_statistics_selection_budget_is_explicit(hass, freezer, monkeypatch):
    freezer.move_to(NOW)
    store = ArchiveStatistics(hass, "budget", "owner")
    try:
        await store.async_load()
        await seed(store)
        monkeypatch.setattr("custom_components.ninebot.ride_archive.MAX_TIMELINE_RIDES", 36)
        with pytest.raises(ArchiveError) as error:
            await store.async_view(SN, ("202610",))
        assert error.value.kind is ArchiveFailure.BUDGET
        assert store.archive_ready
    finally:
        await store.async_close()
