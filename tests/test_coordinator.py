import asyncio
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from custom_components.ninebot.coordinator import NinebotCoordinator
from custom_components.ninebot.exceptions import ErrorKind, NinebotAuthError, NinebotError


@pytest.fixture
async def coordinator(tmp_path, request):
    hass = HomeAssistant(str(tmp_path))
    entry = ConfigEntry(
        domain="ninebot",
        data={},
        options={"enable_controls": True, "control_vehicles": ["synthetic-one"]}
        if getattr(request, "param", False)
        else {},
        version=2,
        minor_version=1,
        source="user",
        title="Ninebot",
        unique_id="fake-business",
        discovery_keys=MappingProxyType({}),
        subentries_data=[],
    )
    client = AsyncMock()
    client.async_list_vehicles.return_value = [
        {"wnumber": "synthetic-one"},
        {"wnumber": "synthetic-two"},
    ]
    client.async_get_status.return_value = {"dump_energy": 80, "pwr": 1, "loc": {"lock": 1}}
    client.async_get_battery.return_value = {"battery_list": []}
    client.async_get_travel.return_value = {"total_mileages": 0, "ec": 0, "list": None}
    co = NinebotCoordinator(hass, entry, client)
    yield co
    await co.async_close()
    await hass.async_stop(force=True)


async def test_one_vehicle_failure_leaves_other_groups_available(coordinator):
    co = coordinator

    async def status(sn):
        if sn == "synthetic-one":
            raise NinebotError(ErrorKind.CONNECTION)
        return {"dump_energy": 79, "charging": 0, "loc": {"lock": 0}}

    co.client.async_get_status.side_effect = status
    result = await co._async_update_data()
    assert result["synthetic-one"].status.battery is None
    assert not co.fresh("synthetic-one", "status")
    assert co.fresh("synthetic-one", "battery")
    assert co.fresh("synthetic-two", "status")
    assert result["synthetic-two"].status.locked is False


async def test_empty_month_preserved_while_last_ride_falls_back(coordinator):
    co = coordinator

    async def travel(sn, month):
        if month == "202610":
            return {"total_mileages": "0.0", "ec": 0, "list": None}
        return {"total_mileages": "303.9", "ec": 7210, "list": [{"mileages": 0.3, "ec": 5}]}

    co.client.async_get_travel.side_effect = travel
    with patch(
        "custom_components.ninebot.coordinator.dt_util.utcnow",
        return_value=datetime(2026, 10, 3, tzinfo=UTC),
    ):
        result = await co._async_update_data()
    current = result["synthetic-one"].travel
    assert current.month == "202610"
    assert current.mileage == current.energy_raw == 0
    assert current.last_ride.month == "202609"
    assert current.last_ride.mileage == 0.3


async def test_manual_refresh_forces_status_and_coalesces(coordinator):
    co = coordinator
    await co._async_update_data()
    co.client.async_get_status.reset_mock()
    wait = asyncio.Event()

    async def status(sn):
        await wait.wait()
        return {"dump_energy": 55}

    co.client.async_get_status.side_effect = status
    one = asyncio.create_task(co.async_refresh_vehicle("synthetic-one"))
    await asyncio.sleep(0)
    two = asyncio.create_task(co.async_refresh_vehicle("synthetic-one"))
    await asyncio.sleep(0)
    wait.set()
    await asyncio.gather(one, two)
    co.client.async_get_status.assert_awaited_once_with("synthetic-one")
    assert co.data["synthetic-one"].status.battery == 55


async def test_fields_disappear_and_stale_is_not_infinite(coordinator):
    co = coordinator
    await co._async_update_data()
    co.client.async_get_status.return_value = {}
    await co.async_refresh_vehicle("synthetic-one")
    assert co.data["synthetic-one"].status.battery is None
    later = datetime.now(UTC) + timedelta(days=2)
    with patch("custom_components.ninebot.coordinator.dt_util.utcnow", return_value=later):
        assert not co.fresh("synthetic-one", "status")
        assert not co.fresh("synthetic-one", "travel")


async def test_auth_is_account_failure_service_error_is_not(coordinator):
    co = coordinator
    co.client.async_get_status.side_effect = NinebotAuthError()
    with pytest.raises(ConfigEntryAuthFailed):
        await co._async_update_data()
    assert not co._mutex.locked()


async def test_removed_vehicle_kept_and_new_vehicle_discovered(coordinator):
    co = coordinator
    await co._async_update_data()
    co._next_attempt[("", "profile")] = 0
    co.client.async_list_vehicles.return_value = [{"wnumber": "new"}]
    result = await co._async_update_data()
    assert not result["synthetic-one"].present
    assert result["new"].present
    assert not co.fresh("synthetic-one", "profile")


async def test_controls_disabled_without_explicit_option(coordinator):
    co = coordinator
    await co._async_update_data()
    with pytest.raises(HomeAssistantError):
        await co.async_control("synthetic-one", "bell")
    co.client.async_control.assert_not_awaited()


@pytest.mark.parametrize("coordinator", [True], indirect=True)
async def test_control_timeout_never_retried(coordinator):
    co = coordinator
    await co._async_update_data()
    co.client.async_control.side_effect = NinebotError(ErrorKind.CONNECTION)
    with pytest.raises(HomeAssistantError):
        await co.async_control("synthetic-one", "bell")
    co.client.async_control.assert_awaited_once()
