"""Real local archive and standard HA calendar API, with no cloud collection."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.calendar import CalendarEntity
from homeassistant.components.calendar.const import DATA_COMPONENT
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot.adapters import travel
from custom_components.ninebot.archive_runtime import ArchiveStatistics
from custom_components.ninebot.calendar import NinebotRideCalendar
from custom_components.ninebot.compat import update_calendar_listeners
from custom_components.ninebot.demand import Need, entity_context
from custom_components.ninebot.exceptions import NinebotAuthError
from custom_components.ninebot.models import VehicleProfile
from custom_components.ninebot.ride_archive import ArchiveError, ArchiveFailure

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
NOW = datetime(2026, 10, 8, 10, tzinfo=UTC)
START = datetime(2024, 1, 7, 4, tzinfo=UTC)
SN = "SyntheticSN"


@pytest.fixture(autouse=True)
def calendar_clock(freezer):
    # Tokens must be issued after setting the clock, not in its future.
    freezer.move_to(NOW)


def sample(ids=("one", "two"), *, count=None, month="202401", start=START):
    return travel(
        {
            "times": len(ids) if count is None else count,
            "list": [
                {
                    "travel_id": identity,
                    "start_time": int(start.timestamp()) + index * 3600,
                    "end_time": int(start.timestamp()) + index * 3600 + 600,
                    "mileages": "1.20",
                    "duration": 600,
                    "speed": "30.0",
                    "ec": 100,
                }
                for index, identity in enumerate(ids)
            ],
        },
        month,
    )


async def seed(hass, entry):
    store = ArchiveStatistics(hass, entry.entry_id, entry.data["business_uid"])
    try:
        await store.async_load()
        await store.async_record_profiles(
            (VehicleProfile(SN, "Scooter", "Test"),), NOW, True, lambda: True
        )
        await store.async_record(SN, sample(), NOW)
    finally:
        await store.async_close()


def calendar(hass):
    registry = er.async_get(hass)
    identity = registry.async_get_entity_id("calendar", "ninebot", f"{SN}_ride_calendar")
    assert identity is not None
    row = registry.async_get(identity)
    assert row.disabled_by is None
    return hass.data[DATA_COMPONENT].get_entity(identity)


async def test_offline_cold_start_calendar_and_latest_across_old_months(
    hass, entry, app_client, freezer, hass_client
):
    freezer.move_to(NOW)
    await seed(hass, entry)
    app_client.async_list_vehicles.side_effect = NinebotAuthError()
    with patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        co = entry.runtime_data.coordinator
        entity = calendar(hass)
        assert isinstance(entity, NinebotRideCalendar) and entity.available
        assert not co._authenticated and not co.last_update_success
        assert entity.event is None and entity.state == "off" and not entity.supported_features
        before = co.data[SN]
        app_client.reset_mock()
        events = await entity.async_get_events(hass, START, START + timedelta(days=1))
        assert len(events) == 2 and events[0].end <= events[1].start
        assert not events[0].all_day and events[0].location is None
        assert events[0].uid != "one" and len(events[0].uid) == 64
        assert "30.0 km/h" in events[0].description and "7.2 km/h" in events[0].description
        assert not await entity.async_get_events(hass, NOW, NOW + timedelta(days=1))
        client = await hass_client()
        response = await client.get(
            f"/api/calendars/{entity.entity_id}",
            params={"start": START.isoformat(), "end": (START + timedelta(days=1)).isoformat()},
        )
        assert response.status == 200
        result = await response.json()
        assert len(result) == 2
        start = result[0]["start"]
        assert (
            datetime.fromisoformat(start["dateTime"] if isinstance(start, dict) else start) == START
        )
        assert result[0].get("status") is None
        assert not {"track", "rides", "latitude", "longitude", "raw"} & result[0].keys()
        registry = er.async_get(hass)
        expected = {
            "last_mileage": ("1.2", 2),
            "last_energy_raw": ("100.0", 0),
            "last_ride_duration": ("600.0", 0),
            "last_ride_max_speed": ("30.0", 1),
            "last_ride_average_speed": ("7.2", 1),
        }
        for key, (value, precision) in expected.items():
            identity = registry.async_get_entity_id("sensor", "ninebot", f"{SN}_{key}")
            state = hass.states.get(identity)
            assert float(state.state) == float(value)
            assert state.attributes["source"] == "ride_archive"
            assert state.attributes["query_month"] == "202401"
            assert state.attributes["received_at"] == NOW.isoformat()
            assert state.attributes["ride_phase"] == "reported"
            assert hass.data["sensor"].get_entity(identity).suggested_display_precision == precision
        assert co.data[SN] is before and before.travel is None
        identity = registry.async_get_entity_id("lock", "ninebot", f"{SN}_vehicle_lock")
        assert hass.states.get(identity).state == "unavailable"
        app_client.async_get_status.assert_not_awaited()
        app_client.async_get_battery.assert_not_awaited()
        app_client.async_get_travel.assert_not_awaited()
        app_client.async_get_trip_detail.assert_not_awaited()
        app_client.async_control.assert_not_awaited()


async def test_archive_revisions_update_entities_without_live_replay_or_unrelated_queries(
    hass, entry, app_client, freezer, monkeypatch
):
    freezer.move_to(NOW)
    with patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity = calendar(hass)
        co = entry.runtime_data.coordinator
        before = co.data[SN]
        notify = Mock()
        monkeypatch.setattr("custom_components.ninebot.calendar.update_calendar_listeners", notify)
        write = Mock()
        monkeypatch.setattr(entity, "async_write_ha_state", write)
        app_client.reset_mock()
        await co.statistics.async_record(SN, sample(), NOW)
        await hass.async_block_till_done()
        notify.assert_called_once_with(entity)
        write.assert_not_called()
        events = await entity.async_get_events(hass, START, START + timedelta(days=1))
        uids = [event.uid for event in events]
        entity._handle_coordinator_update()
        write.assert_not_called()
        await co.statistics.async_record(SN, sample(), NOW + timedelta(seconds=1))
        await hass.async_block_till_done()
        notify.assert_called_once()
        await co.statistics.async_record(
            SN, sample(ids=("one",), count=3), NOW + timedelta(seconds=2)
        )
        assert len(await entity.async_get_events(hass, START, START + timedelta(days=1))) == 2
        await co.statistics.async_record(SN, sample(ids=("one",)), NOW + timedelta(seconds=3))
        events = await entity.async_get_events(hass, START, START + timedelta(days=1))
        assert len(events) == 1 and events[0].uid == uids[0]
        await hass.async_block_till_done()
        assert co.data[SN] is before
        assert co.statistics.latest_ride(SN).ride.ride_id == "one"
        assert entity.available
        co.data[SN] = replace(before, present=False)
        entity._handle_coordinator_update()
        write.assert_called_once()
        with pytest.raises(HomeAssistantError):
            await entity.async_get_events(hass, START, START + timedelta(days=1))
        app_client.async_get_travel.assert_not_awaited()
        app_client.async_control.assert_not_awaited()


async def test_calendar_scope_invalid_range_budget_and_failures(
    hass, entry, app_client, freezer, monkeypatch
):
    freezer.move_to(NOW)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity = calendar(hass)
    co = entry.runtime_data.coordinator
    archive = co.statistics.archive
    with pytest.raises(HomeAssistantError) as err:
        await entity.async_get_events(hass, START.replace(tzinfo=None), NOW)
    assert err.value.translation_key == "calendar_range"
    for kind, key in (
        (ArchiveFailure.BUDGET, "calendar_range"),
        (ArchiveFailure.BUSY, "calendar_unavailable"),
    ):
        monkeypatch.setattr(archive, "async_timeline", AsyncMock(side_effect=ArchiveError(kind)))
        with pytest.raises(HomeAssistantError) as err:
            await entity.async_get_events(hass, START, NOW)
        assert err.value.translation_key == key
        assert not archive.write_paused
    await co.statistics.async_record(SN, sample(), NOW)
    actual = type(archive).async_timeline

    async def lose_owner(*args):
        result = await actual(archive, *args)
        co._ownership[SN] = co._ownership.get(SN, 0) + 1
        return result

    monkeypatch.setattr(archive, "async_timeline", lose_owner)
    with pytest.raises(HomeAssistantError) as err:
        await entity.async_get_events(hass, START, NOW)
    assert err.value.translation_key == "calendar_unavailable"
    assert entity_context(SN, "calendar", "ride_calendar", "travel").need is Need.MONTH


def test_subscription_compatibility_uses_only_the_public_feature():
    old = Mock(spec=[])
    update_calendar_listeners(old)
    modern = Mock(spec=["async_update_event_listeners"])
    update_calendar_listeners(modern)
    modern.async_update_event_listeners.assert_called_once_with()


@pytest.mark.skipif(
    not hasattr(CalendarEntity, "async_subscribe_events"),
    reason="This Core supports range reads but not the public calendar subscription API",
)
async def test_modern_core_websocket_updates_off_calendar_on_archive_revisions_only(
    hass, entry, app_client, hass_ws_client, monkeypatch
):
    with patch("custom_components.ninebot.coordinator.NinebotCoordinator._schedule_refresh"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity = calendar(hass)
        co = entry.runtime_data.coordinator
        query = AsyncMock(wraps=co.statistics.archive.async_timeline)
        monkeypatch.setattr(co.statistics.archive, "async_timeline", query)
        client = await hass_ws_client()
        await client.send_json(
            {
                "id": 1,
                "type": "calendar/event/subscribe",
                "entity_id": entity.entity_id,
                "start": START.isoformat(),
                "end": (START + timedelta(days=1)).isoformat(),
            }
        )
        assert (await client.receive_json())["success"] is True
        assert (await client.receive_json())["event"]["events"] == []
        assert query.await_count == 1
        entity._handle_coordinator_update()
        await hass.async_block_till_done()
        assert query.await_count == 1
        await co.statistics.async_record(SN, sample(), NOW)
        await hass.async_block_till_done()
        events = (await client.receive_json())["event"]["events"]
        assert len(events) == 2 and entity.state == "off"
        assert query.await_count == 2
        await co.statistics.async_record(SN, sample(), NOW + timedelta(seconds=1))
        await hass.async_block_till_done()
        assert query.await_count == 2
        await client.send_json({"id": 2, "type": "unsubscribe_events", "subscription": 1})
        assert (await client.receive_json())["success"] is True
        app_client.async_control.assert_not_awaited()
