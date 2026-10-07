"""Event storage/HA lifecycle tests in an isolated test configuration."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.ninebot.event_store import MAX_CURSOR_BYTES, read_cursor_file
from custom_components.ninebot.models import Freshness
from custom_components.ninebot.ride_events import InvalidRideCursor

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
FIXTURES = Path(__file__).parent / "fixtures/ninecli/0.1.7"
NOW = datetime(2026, 9, 26, tzinfo=UTC)
REAL_WRITE = Store._async_write_data


@pytest.fixture
async def pipeline(hass, entry, app_client, freezer, tmp_path):
    # The default HA fixture mocks writes. Test the real atomic writer only in
    # this disposable configuration, so the disk acknowledgement is meaningful.
    hass.config.config_dir = str(tmp_path)
    freezer.move_to(NOW)
    app_client.async_get_travel.return_value = json.loads(
        (FIXTURES / "travel-nonempty.json").read_text()
    )
    # Tests of the pipeline itself subscribe explicitly. User disabling the
    # default-enabled Event remains supported and prevents an implicit listener.
    er.async_get(hass).async_get_or_create(
        "event",
        "ninebot",
        "SyntheticSN_ride",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    # This fixture drives every sample explicitly through _group. Disable the
    # coordinator's separate periodic timer so a virtual clock jump cannot
    # concurrently race that unguarded test-only path or reset our sample time.
    # Polling/demand scheduling is covered by the coordinator tests.
    with (
        patch.object(Store, "_async_write_data", REAL_WRITE),
        patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry.runtime_data.events


async def new_report(hass, entry, app_client, freezer, *, key="new-ride", seconds=120):
    now = NOW + timedelta(seconds=seconds)
    freezer.move_to(now)
    app_client.async_get_travel.return_value = {
        "list": [
            {
                "travel_id": key,
                "start_time": int(now.timestamp()) - 60,
                "end_time": int(now.timestamp()),
                "duration": 60,
                "mileages": 0.3,
                "speed": 20,
            }
        ]
    }
    co = entry.runtime_data.coordinator
    await co._group("SyntheticSN", "travel", force=True)
    co.async_set_updated_data(dict(co.data))
    await hass.async_block_till_done()


async def settled_report(hass, entry, freezer, *, seconds=720):
    """A separate successful query of the same payload, not a cache reread."""
    freezer.move_to(NOW + timedelta(seconds=seconds))
    co = entry.runtime_data.coordinator
    await co._group("SyntheticSN", "travel", force=True)
    co.async_set_updated_data(dict(co.data))
    await hass.async_block_till_done()


async def test_disabled_event_does_not_load_or_write_or_query_details(
    hass, entry, app_client, pipeline
):
    registry = er.async_get(hass)
    event_id = registry.async_get_entity_id("event", "ninebot", "SyntheticSN_ride")
    assert registry.async_get(event_id).disabled_by is er.RegistryEntryDisabler.USER
    assert hass.states.get(event_id) is None
    assert not pipeline._loaded and not pipeline._callbacks
    assert not await hass.async_add_executor_job(Path(pipeline._store.path).exists)
    app_client.async_get_trip_detail.assert_not_awaited()


async def test_cursor_commit_precedes_callback_and_duplicate_observation_does_not_reemit(
    hass, entry, app_client, freezer, pipeline
):
    emitted = []

    def event(ride, late):
        key = pipeline._vehicle_key("SyntheticSN")
        assert pipeline._cursors[key].observed_at == NOW + timedelta(seconds=720)
        emitted.append((ride.ride_id, late))

    unsubscribe = await pipeline.async_subscribe("SyntheticSN", event)
    assert not emitted and pipeline.available
    await new_report(hass, entry, app_client, freezer)
    assert not emitted
    await settled_report(hass, entry, freezer)
    assert emitted == [("new-ride", False)]
    pipeline._schedule()
    await hass.async_block_till_done()
    assert len(emitted) == 1
    stored = await hass.async_add_executor_job(Path(pipeline._store.path).read_text)
    for private in (
        "SyntheticSN",
        "fixture-ride",
        "new-ride",
        "latitude",
        "longitude",
        "trail",
        "password",
    ):
        assert private not in stored
    with pytest.raises(ValueError, match="Duplicate"):
        await pipeline.async_subscribe("SyntheticSN", event)
    unsubscribe()
    assert pipeline._unsub is None and not pipeline._callbacks


async def test_real_event_entity_baseline_small_attributes_and_restart(
    hass, entry, app_client, freezer, pipeline
):
    registry = er.async_get(hass)
    event_id = registry.async_get_entity_id("event", "ninebot", "SyntheticSN_ride")
    registry.async_update_entity(event_id, disabled_by=None)
    await hass.async_block_till_done()
    # Let HA's 30-second registry enable debounce perform its own reload. A
    # second delayed reload during a new report would correctly rebaseline it.
    enabled_at = NOW + timedelta(seconds=31)
    freezer.move_to(enabled_at)
    async_fire_time_changed(hass, enabled_at)
    await hass.async_block_till_done()
    event = hass.states.get(event_id)
    assert event.state == "unknown" and event.attributes["event_types"] == ["completed"]
    active = entry.runtime_data.events
    assert active._callbacks and active._cursors
    await new_report(hass, entry, app_client, freezer)
    assert hass.states.get(event_id).state == "unknown"
    await settled_report(hass, entry, freezer)
    co = entry.runtime_data.coordinator
    last = co.data["SyntheticSN"].travel.rides[0]
    assert co.ride_lifecycles["SyntheticSN"].phase(last) == "finalized_by_policy"
    assert co.fresh("SyntheticSN", "profile")
    assert active.available
    event = hass.states.get(event_id)
    assert event.state.startswith("2026-09-26T00:12:00")
    assert event.attributes["event_type"] == "completed"
    assert event.attributes["ride_id"] == "new-ride"
    assert event.attributes["average_speed_m_s"] == 5
    for large in ("raw", "track", "speed_samples", "latitude", "longitude"):
        assert large not in event.attributes
    before = event.state
    co = entry.runtime_data.coordinator
    co.data["SyntheticSN"] = replace(co.data["SyntheticSN"], travel_freshness=Freshness())
    co.async_set_updated_data(dict(co.data))
    await hass.async_block_till_done()
    assert hass.states.get(event_id).state == "unavailable"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    # RestoreEvent may retain the old visible event, but startup does not emit it again.
    assert hass.states.get(event_id).state == before
    await new_report(hass, entry, app_client, freezer, key="next-ride", seconds=840)
    await settled_report(hass, entry, freezer, seconds=1440)
    co = entry.runtime_data.coordinator
    assert co.ride_lifecycles["SyntheticSN"].phase(co.data["SyntheticSN"].travel.rides[0]) == (
        "finalized_by_policy"
    )
    assert entry.runtime_data.events._processed["SyntheticSN"] == NOW + timedelta(seconds=1440)
    assert hass.states.get(event_id).attributes["ride_id"] == "next-ride"
    app_client.async_get_trip_detail.assert_not_awaited()
    app_client.async_control.assert_not_awaited()


async def test_silent_store_write_failure_is_not_acknowledged_or_emitted(
    hass, entry, app_client, freezer, pipeline
):
    emitted = []
    await pipeline.async_subscribe("SyntheticSN", lambda ride, late: emitted.append(ride))
    before = await hass.async_add_executor_job(Path(pipeline._store.path).read_bytes)
    pipeline._store.async_save = AsyncMock()  # HA can log a write failure and return.
    await new_report(hass, entry, app_client, freezer)
    assert not emitted and not pipeline.available
    assert await hass.async_add_executor_job(Path(pipeline._store.path).read_bytes) == before
    assert ir.async_get(hass).async_get_issue("ninebot", f"ride_events_{entry.entry_id}")
    assert entry.runtime_data.coordinator.fresh("SyntheticSN", "status")


async def test_verified_empty_month_baselines_then_emits_first_stable_ride(
    hass, entry, app_client, freezer, pipeline
):
    app_client.async_get_travel.return_value = {"times": 0, "list": []}
    co = entry.runtime_data.coordinator
    await co._group("SyntheticSN", "travel", force=True)
    co.async_set_updated_data(dict(co.data))
    emitted = []
    await pipeline.async_subscribe("SyntheticSN", lambda ride, late: emitted.append(ride))
    assert pipeline._cursors[pipeline._vehicle_key("SyntheticSN")].baseline_at == NOW
    await new_report(hass, entry, app_client, freezer)
    assert not emitted
    await settled_report(hass, entry, freezer)
    assert [ride.ride_id for ride in emitted] == ["new-ride"]


async def test_cancelled_save_and_closed_subscriptions_never_emit(
    hass, entry, app_client, freezer, pipeline
):
    emitted = []
    remove = await pipeline.async_subscribe("SyntheticSN", lambda ride, late: emitted.append(ride))
    started = asyncio.Event()

    async def stalled(data):
        started.set()
        await asyncio.Event().wait()

    pipeline._store.async_save = stalled
    now = NOW + timedelta(seconds=120)
    freezer.move_to(now)
    app_client.async_get_travel.return_value = {
        "list": [
            {
                "travel_id": "candidate",
                "start_time": int(now.timestamp()) - 60,
                "end_time": int(now.timestamp()),
                "duration": 60,
            }
        ]
    }
    co = entry.runtime_data.coordinator
    await co._group("SyntheticSN", "travel", force=True)
    co.async_set_updated_data(dict(co.data))
    await asyncio.wait_for(started.wait(), 2)
    await pipeline.async_close()
    remove()
    assert not pipeline._tasks and not pipeline._callbacks and not emitted
    await pipeline.async_subscribe("SyntheticSN", lambda ride, late: emitted.append(ride))
    assert not pipeline._callbacks


@pytest.mark.parametrize(
    "data",
    [{"cursor_version": 2, "vehicles": {}}, {"cursor_version": 1, "vehicles": {"bad-key": {}}}],
)
async def test_unknown_cursor_preserved_and_repair_without_stopping_vehicle_data(
    hass, entry, pipeline, data
):
    path = Path(pipeline._store.path)
    await hass.async_add_executor_job(partial(path.parent.mkdir, parents=True, exist_ok=True))
    raw = {"version": 1, "minor_version": 1, "key": pipeline._store.key, "data": data}
    await hass.async_add_executor_job(path.write_text, json.dumps(raw))
    before = await hass.async_add_executor_job(path.read_bytes)
    await pipeline.async_subscribe("SyntheticSN", lambda ride, late: pytest.fail("must not emit"))
    assert not pipeline.available
    assert await hass.async_add_executor_job(path.read_bytes) == before
    assert ir.async_get(hass).async_get_issue("ninebot", f"ride_events_{entry.entry_id}")
    assert entry.runtime_data.coordinator.fresh("SyntheticSN", "status")


def test_invalid_envelope_and_size_limit_do_not_rename_or_overwrite(tmp_path):
    path = tmp_path / "cursor"
    assert read_cursor_file(path, "expected") is None
    for value in (
        b"bad-json",
        b"{}",
        b"[]",
        json.dumps({"version": 2, "minor_version": 1, "key": "expected", "data": {}}).encode(),
        b" " * (MAX_CURSOR_BYTES + 1),
    ):
        path.write_bytes(value)
        with pytest.raises((InvalidRideCursor, ValueError)):
            read_cursor_file(path, "expected")
        assert path.read_bytes() == value
