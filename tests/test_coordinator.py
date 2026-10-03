import asyncio
from dataclasses import replace
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
        options=(
            {"enable_controls": True, "control_vehicles": ["synthetic-one"]}
            if getattr(request, "param", False) is True
            else getattr(request, "param", {})
        ),
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


async def test_list_failure_retries_initial_setup_but_preserves_prior_data(coordinator):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    co = coordinator
    co.client.async_list_vehicles.side_effect = NinebotError(ErrorKind.SERVICE)
    with pytest.raises(UpdateFailed):
        await co._async_update_data()
    assert co._list_freshness.succeeded_at is None
    assert co._list_freshness.error == ErrorKind.SERVICE
    co.client.async_list_vehicles.side_effect = None
    co._next_attempt[("", "profile")] = 0
    await co._async_update_data()
    prior = co._list_freshness.succeeded_at
    co._next_attempt[("", "profile")] = 0
    co.client.async_list_vehicles.side_effect = NinebotError(ErrorKind.SERVICE)
    await co._async_update_data()
    assert co._list_freshness.succeeded_at == prior
    assert co.fresh("synthetic-two", "profile")
    assert co.fresh("synthetic-two", "status")
    assert not co.fresh("synthetic-two", "invalid-group")


async def test_list_auth_failure_invalidates_account(coordinator):
    co = coordinator
    await co._async_update_data()
    co._next_attempt[("", "profile")] = 0
    co.client.async_list_vehicles.side_effect = NinebotAuthError()
    with pytest.raises(ConfigEntryAuthFailed):
        await co._async_update_data()
    assert not co.fresh("synthetic-one", "status")


async def test_independent_battery_travel_errors_preserve_last_good_with_ttl(coordinator):
    co = coordinator
    await co._async_update_data()
    battery_at = co.data["synthetic-one"].battery_freshness.succeeded_at
    travel_at = co.data["synthetic-one"].travel_freshness.succeeded_at
    co.client.async_get_battery.side_effect = NinebotError(ErrorKind.CONNECTION)
    co.client.async_get_travel.side_effect = NinebotError(ErrorKind.SERVICE)
    for group in ("battery", "travel"):
        co._next_attempt[("synthetic-one", group)] = 0
    await co._async_update_data()
    snapshot = co.data["synthetic-one"]
    assert snapshot.battery_freshness.succeeded_at == battery_at
    assert snapshot.travel_freshness.succeeded_at == travel_at
    assert snapshot.battery_freshness.error == ErrorKind.CONNECTION
    assert snapshot.travel_freshness.error == ErrorKind.SERVICE
    assert co.fresh("synthetic-two", "status")
    later = battery_at + timedelta(seconds=1801)
    with patch("custom_components.ninebot.coordinator.dt_util.utcnow", return_value=later):
        assert not co.fresh("synthetic-one", "battery")
        assert not co.fresh("synthetic-one", "travel")


@pytest.mark.parametrize("group", ["battery", "travel"])
async def test_detail_auth_failure_triggers_account_reauth(coordinator, group):
    co = coordinator
    await co._async_update_data()
    co._next_attempt[("synthetic-one", group)] = 0
    getattr(co.client, f"async_get_{group}").side_effect = NinebotAuthError()
    with pytest.raises(ConfigEntryAuthFailed):
        await co._async_update_data()
    assert not co.fresh("synthetic-two", "status")


@pytest.mark.parametrize("auth", [False, True])
async def test_optional_previous_month_failure_cannot_change_current_totals(coordinator, auth):
    co = coordinator

    async def travel(sn, month):
        if month == "202610":
            return {"total_mileages": 0, "ec": 0, "list": []}
        raise NinebotAuthError() if auth else NinebotError(ErrorKind.CONNECTION)

    co.client.async_get_travel.side_effect = travel
    with patch(
        "custom_components.ninebot.coordinator.dt_util.utcnow",
        return_value=datetime(2026, 10, 3, tzinfo=UTC),
    ):
        if auth:
            with pytest.raises(ConfigEntryAuthFailed):
                await co._async_update_data()
        else:
            await co._async_update_data()
            current = co.data["synthetic-one"].travel
            assert current.mileage == current.energy_raw == 0
            assert current.last_ride is None
            assert co.fresh("synthetic-one", "travel")


async def test_manual_refresh_auth_invalidates_account_and_cleans_task(coordinator):
    co = coordinator
    await co._async_update_data()
    co.client.async_get_status.side_effect = NinebotAuthError()
    with pytest.raises(ConfigEntryAuthFailed):
        await co.async_refresh_vehicle("synthetic-one")
    assert not co._forced
    assert not co.fresh("synthetic-two", "status")
    co.client.async_get_status.reset_mock()
    await co.async_refresh_vehicle("missing")
    co.client.async_get_status.assert_not_awaited()


@pytest.mark.parametrize("coordinator", [True], indirect=True)
@pytest.mark.parametrize("auth", [False, True])
async def test_control_readback_failure_does_not_repeat_action(coordinator, auth):
    co = coordinator
    await co._async_update_data()
    co.client.async_get_status.side_effect = (
        NinebotAuthError() if auth else NinebotError(ErrorKind.CONNECTION)
    )
    with pytest.raises(HomeAssistantError) as error:
        await co.async_control("synthetic-one", "bell")
    assert error.value.translation_key == "control_readback_failed"
    co.client.async_control.assert_awaited_once_with("synthetic-one", "bell")
    assert co._control_pending == 0


@pytest.mark.parametrize("coordinator", [True], indirect=True)
async def test_control_queue_bounded_and_unload_cancels_queued_actions(coordinator):
    co = coordinator
    await co._async_update_data()
    await co._mutex.acquire()
    tasks = [asyncio.create_task(co.async_control("synthetic-one", "bell")) for _ in range(4)]
    await asyncio.sleep(0)
    with pytest.raises(HomeAssistantError) as error:
        await co.async_control("synthetic-one", "bell")
    assert error.value.translation_key == "busy"
    await co.async_close()
    co._mutex.release()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    co.client.async_control.assert_not_awaited()
    assert co._control_pending == 0
    assert not co._active


async def test_cancel_unload_waits_for_shutdown_and_reuses_cleanup_task(coordinator):
    co = coordinator
    await co._async_update_data()
    started, release = asyncio.Event(), asyncio.Event()

    async def close():
        started.set()
        await release.wait()

    co.client.async_close.side_effect = close
    task = asyncio.create_task(co.async_close())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await co.async_close()
    co.client.async_close.assert_awaited_once()
    co.client.async_get_status.reset_mock()
    await co.async_refresh_vehicle("synthetic-one")
    await co._async_update_data()
    co.client.async_get_status.assert_not_awaited()


async def test_shutdown_cancels_active_poll_and_manual_refresh(coordinator):
    co = coordinator
    await co._async_update_data()
    started = asyncio.Event()

    async def status(sn):
        started.set()
        await asyncio.Event().wait()

    co.client.async_get_status.side_effect = status
    co._next_attempt[("synthetic-one", "status")] = 0
    polling = asyncio.create_task(co._async_update_data())
    await started.wait()
    manual = asyncio.create_task(co.async_refresh_vehicle("synthetic-two"))
    await asyncio.sleep(0)
    await co.async_close()
    results = await asyncio.gather(polling, manual, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert not co._active and not co._forced
    assert not co._mutex.locked()


@pytest.mark.parametrize("coordinator", [{"enable_estimation": True}], indirect=True)
async def test_estimation_waits_for_battery_then_rebaselines_without_fake_energy(coordinator):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from custom_components.ninebot.estimation import EnergyModel

    co = coordinator
    models = {sn: EnergyModel(72, 20) for sn in ("synthetic-one", "synthetic-two")}
    co.models = SimpleNamespace(models=models, model=models.__getitem__, schedule_save=MagicMock())
    co.client.async_get_battery.side_effect = NinebotError(ErrorKind.CONNECTION)
    start = datetime(2026, 10, 3, tzinfo=UTC)
    with patch("custom_components.ninebot.coordinator.dt_util.utcnow", return_value=start):
        await co._async_update_data()
    model = models["synthetic-one"]
    assert model.baseline_soc is None
    assert "out_total" not in model.values
    co.client.async_get_battery.side_effect = None
    co.client.async_get_battery.return_value = {"battery_list": [{"sn": "fake-pack"}]}
    later = start + timedelta(seconds=120)
    with patch("custom_components.ninebot.coordinator.dt_util.utcnow", return_value=later):
        await co._async_update_data()
    assert model.baseline_soc == 80
    generation = model.generation
    source = model.source
    with patch("custom_components.ninebot.coordinator.dt_util.utcnow", return_value=later):
        await co._async_update_data()
    assert model.quality == "baseline_only"
    co.client.async_get_status.return_value = {"dump_energy": 79}
    with patch(
        "custom_components.ninebot.coordinator.dt_util.utcnow",
        return_value=later + timedelta(seconds=120),
    ):
        await co.async_refresh_vehicle("synthetic-one")
    assert model.values["out_total"] == pytest.approx(0.0144)
    assert model.generation == generation
    assert model.source == source
    co.data["synthetic-one"] = replace(
        co.data["synthetic-one"],
        battery_freshness=__import__(
            "custom_components.ninebot.models", fromlist=["Freshness"]
        ).Freshness(),
    )
    with patch(
        "custom_components.ninebot.coordinator.dt_util.utcnow",
        return_value=later + timedelta(seconds=240),
    ):
        await co.async_refresh_vehicle("synthetic-one")
    assert model.baseline_soc is None
    assert model.values["out_total"] == pytest.approx(0.0144)
