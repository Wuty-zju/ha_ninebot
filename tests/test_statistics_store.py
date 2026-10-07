"""Isolated persistence/replay, corrected reports and incomplete metadata."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.statistics_store import TravelStatisticsStore

NOW = datetime(2026, 9, 26, tzinfo=UTC)
REAL_WRITE = Store._async_write_data


def sample(*, month="202609", distance=14.8, energy=340, count=2, duration=2671):
    return travel(
        {
            "total_mileages": distance,
            "ec": energy,
            "times": count,
            "duration": duration,
            "list": [
                {"travel_id": "first", "mileages": 6.1, "ec": 145, "duration": 1287},
                {"travel_id": "second", "mileages": 8.7, "ec": 195, "duration": 1384},
            ],
        },
        month,
    )


def test_replace_upsert_revision_old_replay_and_entry_isolation(hass):
    store = TravelStatisticsStore(hass, "entry-one")
    assert store.update("one", sample(), NOW)
    assert not store.update("one", sample(distance=999), NOW - timedelta(seconds=1))
    assert store.update("one", sample(), NOW + timedelta(seconds=1))
    assert store.month("one", "202609").revision == 1
    assert store.update("one", sample(distance=15, energy=350), NOW + timedelta(seconds=2))
    summary = store.month("one", "202609")
    assert summary.distance_km == 15 and summary.energy_wh == 350
    assert summary.revision == 2
    rows, complete = store.month_rides("one", "202609")
    assert len(rows) == 2 and complete
    assert store.month("other", "202609") is None
    assert TravelStatisticsStore(hass, "entry-two").month("one", "202609") is None
    encoded = json.loads(json.dumps(store.dump()))
    restored = TravelStatisticsStore(hass, "entry-one")
    restored.restore(encoded)
    assert restored.month("one", "202609") == summary
    assert restored.month_rides("one", "202609") == (rows, True)


def test_partial_coverage_budget_and_cross_month_identity(hass):
    store = TravelStatisticsStore(hass, "entry")
    store.update("one", sample(count=128), NOW)
    assert not store.month_rides("one", "202609")[1]
    with patch("custom_components.ninebot.statistics_store.MAX_RIDES", 1):
        store.update("one", sample(), NOW + timedelta(seconds=1))
    assert len(store.rides[store.vehicle_key("one")]) == 1
    assert not store.month_rides("one", "202609")[1]
    store.update("one", sample(month="202608"), NOW + timedelta(seconds=2))
    assert not store.month_rides("one", "202609")[1]
    assert store.month("one", "202609").distance_km == 14.8


@pytest.mark.parametrize("value", [True, "12", -1, float("nan"), float("inf")])
def test_corrupt_metric_rejected_without_mutating_ledger(hass, value):
    store = TravelStatisticsStore(hass, "entry")
    store.update("one", sample(), NOW)
    before = json.loads(json.dumps(store.dump()))
    corrupt = json.loads(json.dumps(before))
    corrupt["months"][store.vehicle_key("one")]["202609"]["distance_km"] = value
    with pytest.raises(ValueError):
        store.restore(corrupt)
    assert json.loads(json.dumps(store.dump())) == before


async def test_real_save_load_no_raw_or_location_and_future_file_preserved(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    store = TravelStatisticsStore(hass, "entry")
    with patch.object(Store, "_async_write_data", REAL_WRITE):
        await store.async_load()
        await store.async_record("one", sample(), NOW)
        await store.async_save()
        path = Path(store._store.path)
        encoded = await hass.async_add_executor_job(path.read_text)
        for excluded in ("latitude", "longitude", "password", "trail", '"one"', '"raw"'):
            assert excluded not in encoded
        restored = TravelStatisticsStore(hass, "entry")
        await restored.async_load()
        assert restored.restored and restored.month("one", "202609").distance_km == 14.8
        assert not restored.update("one", sample(distance=999), NOW)
        future = json.loads(encoded)
        future["version"] = 99
        await hass.async_add_executor_job(path.write_text, json.dumps(future))
        blocked = TravelStatisticsStore(hass, "entry")
        await blocked.async_load()
        assert not blocked.available
        assert ir.async_get(hass).async_get_issue("ninebot", "travel_statistics_entry")
        await blocked.async_record("one", sample(), NOW)
        await blocked.async_save()
        assert json.loads(await hass.async_add_executor_job(path.read_text)) == future


async def test_explicit_removal_cancels_delayed_write_and_failed_ack_pauses_only_store(
    hass, tmp_path
):
    hass.config.config_dir = str(tmp_path)
    store = TravelStatisticsStore(hass, "entry")
    await store.async_record("one", sample(), NOW)
    # Default HA test writer does not create a file. A missing durable ack is
    # a failed write even when Store.async_save itself returns successfully.
    await store.async_save()
    assert not store.available
    await store.async_remove()
    assert not store.months and not store.rides
    assert not await hass.async_add_executor_job(Path(store._store.path).exists)


async def test_concurrent_candidates_preserve_both_vehicles_and_owner_guard(hass):
    store = TravelStatisticsStore(hass, "entry")
    started, release = asyncio.Event(), asyncio.Event()
    original = hass.async_add_executor_job
    allowed = True

    async def paused(target, *args):
        if getattr(target, "__name__", "") == "update":
            started.set()
            await release.wait()
        return await original(target, *args)

    with patch.object(hass, "async_add_executor_job", side_effect=paused):
        first = asyncio.create_task(store.async_record("one", sample(), NOW))
        await started.wait()
        second = asyncio.create_task(store.async_record("two", sample(distance=7), NOW))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        assert store.month("one", "202609").distance_km == 14.8
        assert store.month("two", "202609").distance_km == 7
        started.clear()
        release.clear()
        stale = asyncio.create_task(
            store.async_record(
                "one", sample(distance=999), NOW + timedelta(seconds=1), guard=lambda: allowed
            )
        )
        await started.wait()
        allowed = False
        release.set()
        await stale
        await store.async_record("three", sample(), NOW, guard=lambda: allowed)
        assert store.month("one", "202609").distance_km == 14.8
        assert store.month("three", "202609") is None


def test_same_id_partial_correction_retains_values_with_explicit_field_provenance(hass):
    store = TravelStatisticsStore(hass, "entry")
    store.update("one", sample(), NOW)
    partial = travel({"times": 1, "list": [{"travel_id": "first", "mileages": 7}]}, "202609")
    store.update("one", partial, NOW + timedelta(seconds=1))
    rows, complete = store.month_rides("one", "202609")
    assert complete and len(rows) == 1
    assert rows[0].distance_m == 7000 and rows[0].energy_wh == 145
    assert "energy_wh" not in rows[0].current_fields
    assert store.month("one", "202609").revision == 2


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_coordinator_current_and_historical_queries_share_persistent_source(
    hass, entry, app_client
):
    app_client.async_get_travel.return_value = {
        "total_mileages": 14.8,
        "times": 2,
        "ec": 340,
        "duration": 2671,
        "list": [],
    }
    assert await hass.config_entries.async_setup(entry.entry_id)
    co = entry.runtime_data.coordinator
    current = co.data["SyntheticSN"].travel.month
    assert co.statistics.month("SyntheticSN", current).distance_km == 14.8
    await co.async_query_month("SyntheticSN", "202608")
    assert co.statistics.month("SyntheticSN", "202608").energy_wh == 340
    assert co.data["SyntheticSN"].travel.month == current
