"""Recorded history replay in isolated HA; never contacts cloud or vehicles."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ninebot import async_setup
from custom_components.ninebot.compat import device_entry_ids, is_child_device
from custom_components.ninebot.exceptions import ErrorKind, NinebotAuthError, NinebotError
from custom_components.ninebot.services import DETAIL_SCHEMA, TRIPS_SCHEMA, resolve_vehicle

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
FIXTURES = Path(__file__).parent / "fixtures/ninecli/0.1.7"


@pytest.fixture
async def query_device(hass, entry, app_client, freezer):
    freezer.move_to(datetime(2026, 9, 26, tzinfo=UTC))
    app_client.async_get_travel.return_value = json.loads(
        (FIXTURES / "travel-nonempty.json").read_text()
    )
    app_client.async_get_trip_detail.return_value = json.loads(
        (FIXTURES / "trip-detail.json").read_text()
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return next(
        device.id
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if ("ninebot", "SyntheticSN") in device.identifiers
    )


async def call(hass, service, data):
    return await hass.services.async_call(
        "ninebot", service, data, blocking=True, return_response=True
    )


async def test_registered_without_entry_and_after_unload(hass, entry, app_client, query_device):
    for service in ("get_trips", "get_trip_detail"):
        assert hass.services.supports_response("ninebot", service) is SupportsResponse.ONLY
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "ninebot", "get_trips", {"device_id": query_device, "month": "202609"}, blocking=True
        )
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(HomeAssistantError, match="Vehicle query unavailable"):
        await call(hass, "get_trips", {"device_id": query_device, "month": "202609"})
    app_client.async_control.assert_not_awaited()


async def test_setup_actions_without_loaded_account(hass):
    assert await async_setup(hass, {})
    assert hass.services.has_service("ninebot", "get_trips")
    with pytest.raises(HomeAssistantError) as error:
        await call(hass, "get_trips", {"device_id": "missing", "month": "202609"})
    assert error.value.translation_key == "query_device"


async def test_list_reuses_poll_cache_local_pagination_and_safe_response(
    hass, entry, app_client, query_device
):
    co = entry.runtime_data.coordinator
    old = co.data["SyntheticSN"]
    app_client.async_get_travel.reset_mock()
    response = await call(
        hass, "get_trips", {"device_id": query_device, "month": "202609", "page": 2, "limit": 3}
    )
    assert response["pagination"] == {
        "page": 2,
        "limit": 3,
        "returned": 3,
        "available_in_response": 20,
        "total_known": None,
        "has_more": True,
        "upstream_complete": "unknown",
    }
    assert response["rides"][0]["ride_id"] == "fixture-ride-04"
    assert response["month_energy_unit"] == "unknown"
    encoded = json.dumps(response, allow_nan=False)
    for private in (
        "SyntheticSN",
        "fake-account",
        "trail",
        "latitude",
        "longitude",
        "img",
        'detail"',
    ):
        assert private not in encoded
    app_client.async_get_travel.assert_not_awaited()
    app_client.async_get_trip_detail.assert_not_awaited()
    assert co.data["SyntheticSN"] is old
    empty = await call(
        hass, "get_trips", {"device_id": query_device, "month": "202609", "page": 999}
    )
    assert empty["rides"] == [] and not empty["pagination"]["has_more"]


async def test_detail_defaults_no_gps_then_explicit_opt_in_and_bound_cache(
    hass, entry, app_client, query_device
):
    data = {"device_id": query_device, "ride_id": "fixture-ride-01", "query_month": "202609"}
    first = await call(hass, "get_trip_detail", data)
    ride = first["ride"]
    assert ride["average_speed_m_s"] == pytest.approx(300 / 83)
    assert ride["max_speed_m_s"] == pytest.approx(23 / 3.6)
    assert ride["server_average_speed_raw"] == 0
    assert len(ride["speed_samples"]) == 5
    assert "track" not in ride and "start_location" not in ride
    assert all(sample["unit"] == "unknown" for sample in ride["speed_samples"])
    await call(hass, "get_trip_detail", data)
    app_client.async_get_trip_detail.assert_awaited_once()
    with pytest.raises(HomeAssistantError, match="Enable coordinates"):
        await call(hass, "get_trip_detail", {**data, "include_track": True})
    hass.config_entries.async_update_entry(entry, options={"enable_coordinates": True})
    await hass.async_block_till_done()
    second = await call(hass, "get_trip_detail", {**data, "include_track": True, "max_points": 2})
    assert second["ride"]["coordinate_system"] == "unknown"
    assert second["ride"]["track_pagination"] == {
        "returned": 2,
        "total_known": 5,
        "truncated": True,
    }
    assert len(second["ride"]["speed_samples"]) == 2
    assert second["ride"]["track"][0]["longitude"] == 120
    await call(hass, "get_trip_detail", {**data, "include_track": True, "max_points": 2})
    assert app_client.async_get_trip_detail.await_count == 2
    app_client.async_control.assert_not_awaited()
    json.dumps(second, allow_nan=False)


async def test_list_details_fanout_limit_and_shared_cache(hass, entry, app_client, query_device):
    data = {"device_id": query_device, "month": "202609", "include_detail": True}
    with pytest.raises(HomeAssistantError, match="five or less"):
        await call(hass, "get_trips", data)
    hass.config_entries.async_update_entry(entry, options={"enable_coordinates": True})
    await hass.async_block_till_done()
    with pytest.raises(HomeAssistantError, match="requires include_detail"):
        await call(
            hass, "get_trips", {"device_id": query_device, "month": "202609", "include_track": True}
        )
    raw = app_client.async_get_travel.return_value
    detail = app_client.async_get_trip_detail.return_value

    def matching(sn, ride_id):
        summary = next(row for row in raw["list"] if row["travel_id"] == ride_id)
        return {
            **detail,
            **{
                field: summary[field]
                for field in ("start_time", "end_time", "duration", "mileages", "speed")
            },
        }

    app_client.async_get_trip_detail.side_effect = matching
    response = await call(hass, "get_trips", {**data, "limit": 5})
    assert len(response["rides"]) == 5
    assert app_client.async_get_trip_detail.await_count == 5
    assert all("track" not in ride for ride in response["rides"])
    await call(hass, "get_trip_detail", {"device_id": query_device, "ride_id": "fixture-ride-01"})
    assert app_client.async_get_trip_detail.await_count == 5


@pytest.mark.parametrize(
    "data",
    [
        {"month": "202600"},
        {"month": "202613"},
        {"month": "202610"},
        {"month": "199912"},
        {"month": 202609},
        {"month": "２０２６０９"},
        {"month": "202609", "page": True},
        {"month": "202609", "page": 1.2},
        {"month": "202609", "limit": 101},
        {"month": "202609", "account": "override"},
    ],
)
def test_invalid_inputs_rejected_before_backend(data, freezer):
    freezer.move_to(datetime(2026, 9, 26, tzinfo=UTC))
    with pytest.raises(vol.Invalid):
        TRIPS_SCHEMA({"device_id": "device", **data})
    for invalid in ("", "x\n", "x" * 257, True, 12):
        with pytest.raises(vol.Invalid):
            DETAIL_SCHEMA({"device_id": "device", "ride_id": invalid})


async def test_ride_lookup_no_guess_cross_month_or_duplicate(hass, entry, app_client, query_device):
    data = {"device_id": query_device, "ride_id": "not-in-index"}
    with pytest.raises(HomeAssistantError, match="verified detail mapping"):
        await call(hass, "get_trip_detail", data)
    app_client.async_get_trip_detail.assert_not_awaited()
    app_client.async_get_travel.return_value = {"list": None}
    with pytest.raises(HomeAssistantError, match="verified detail mapping"):
        await call(
            hass, "get_trip_detail", {**data, "ride_id": "fixture-ride-01", "query_month": "202608"}
        )
    app_client.async_get_travel.assert_any_await("SyntheticSN", "202608")
    co = entry.runtime_data.coordinator
    co.raw.clear()
    row = {"travel_id": "duplicate", "duration": 1}
    app_client.async_get_travel.return_value = {"list": [row, row]}
    with pytest.raises(HomeAssistantError, match="verified detail mapping"):
        await call(hass, "get_trip_detail", {**data, "ride_id": "duplicate"})
    co.raw.clear()
    app_client.async_get_travel.return_value = {"list": [{"id": "legacy-only"}]}
    with pytest.raises(HomeAssistantError, match="verified detail mapping"):
        await call(hass, "get_trip_detail", {**data, "ride_id": "legacy-only"})
    app_client.async_get_trip_detail.assert_not_awaited()


async def test_foreign_child_unknown_disabled_and_ambiguous_devices(
    hass, entry, app_client, query_device
):
    registry = dr.async_get(hass)
    other = MockConfigEntry(domain="ninebot", entry_id="other", unique_id="other")
    other.add_to_hass(hass)
    foreign = MockConfigEntry(domain="other", entry_id="foreign")
    foreign.add_to_hass(hass)
    for owner, identifier in [(foreign.entry_id, "ForeignSN"), (entry.entry_id, "BatteryPack")]:
        device = registry.async_get_or_create(
            config_entry_id=owner, identifiers={("ninebot", identifier)}
        )
        with pytest.raises(HomeAssistantError):
            resolve_vehicle(hass, device.id)
    device = registry.async_get(query_device)
    for fake in (
        SimpleNamespace(parent_device_id="parent"),
        SimpleNamespace(parent_device_id=None, disabled_by="user"),
        SimpleNamespace(
            parent_device_id=None,
            disabled_by=None,
            identifiers={("ninebot", "SyntheticSN")},
            config_entries={entry.entry_id, other.entry_id},
        ),
    ):
        with patch.object(registry, "async_get", return_value=fake):
            with pytest.raises(HomeAssistantError, match="unambiguous Ninebot"):
                resolve_vehicle(hass, query_device)
    assert device.identifiers


def test_registry_compatibility_without_version_string():
    assert device_entry_ids(SimpleNamespace(config_entries={"old", "second"})) == {"old", "second"}
    assert device_entry_ids(SimpleNamespace(config_entry_id="new", config_entries={"wrong"})) == {
        "new"
    }
    for device in (
        SimpleNamespace(),
        SimpleNamespace(config_entries="invalid"),
        SimpleNamespace(config_entry_id=None),
    ):
        assert not device_entry_ids(device)
    assert is_child_device(SimpleNamespace(parent_device_id="parent"))
    assert not is_child_device(SimpleNamespace())


async def test_sanitized_errors_auth_reauth_and_unload_during_parse(
    hass, entry, app_client, query_device
):
    data = {"device_id": query_device, "ride_id": "fixture-ride-01"}
    app_client.async_get_trip_detail.side_effect = NinebotError(ErrorKind.CONNECTION)
    with pytest.raises(HomeAssistantError, match="connection"):
        await call(hass, "get_trip_detail", data)
    app_client.async_get_trip_detail.side_effect = NinebotAuthError()
    with patch.object(entry, "async_start_reauth") as reauth:
        with pytest.raises(ConfigEntryAuthFailed):
            await call(hass, "get_trip_detail", data)
        reauth.assert_called_once_with(hass)
    assert not entry.runtime_data.coordinator.fresh("SyntheticSN", "profile")


async def test_wrong_detail_and_invalid_shape_fail_closed(hass, entry, app_client, query_device):
    data = {"device_id": query_device, "ride_id": "fixture-ride-01"}
    raw = app_client.async_get_trip_detail.return_value
    for wrong in ({}, {**raw, "end_time": raw["end_time"] + 99}, {**raw, "travel_id": "different"}):
        entry.runtime_data.coordinator.raw.clear()
        app_client.async_get_trip_detail.return_value = wrong
        with pytest.raises(HomeAssistantError, match="protocol"):
            await call(hass, "get_trip_detail", data)
    app_client.async_control.assert_not_awaited()


async def test_ownership_rechecked_after_executor_await(hass, entry, app_client, query_device):
    from custom_components.ninebot import services

    original = services.month_data
    entered, release = asyncio.Event(), asyncio.Event()
    original_executor = hass.async_add_executor_job

    async def executor(target, *args):
        if target is original:
            entered.set()
            await release.wait()
        return await original_executor(target, *args)

    with patch.object(hass, "async_add_executor_job", side_effect=executor):
        task = asyncio.create_task(
            call(hass, "get_trips", {"device_id": query_device, "month": "202609"})
        )
        await entered.wait()
        co = entry.runtime_data.coordinator
        co.data["SyntheticSN"] = replace(co.data["SyntheticSN"], present=False)
        release.set()
        with pytest.raises(HomeAssistantError, match="Vehicle query unavailable"):
            await task
