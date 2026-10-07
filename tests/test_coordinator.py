import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from unittest.mock import AsyncMock, call, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from custom_components.ninebot.capabilities import (
    CapabilityState,
    ControlCapability,
    VehicleCapabilities,
)
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
    client.vehicle_discovery_complete = True
    client.async_list_vehicles.return_value = [
        {"wnumber": "synthetic-one"},
        {"wnumber": "synthetic-two"},
    ]
    client.async_get_status.return_value = {"dump_energy": 80, "pwr": 1, "loc": {"lock": 1}}
    client.async_get_battery.return_value = {"battery_list": []}
    client.async_get_travel.return_value = {"total_mileages": 0, "ec": 0, "list": None}
    co = NinebotCoordinator(hass, entry, client)
    from custom_components.ninebot.demand import ConsumerContext, Need

    with (
        patch.object(co, "_schedule_refresh"),
        patch.object(entry, "async_start_reauth"),
    ):
        # These tests explicitly drive time/polls, not HA timer callbacks.
        for sn in ("synthetic-one", "synthetic-two"):
            for need in (Need.STATUS, Need.BATTERY, Need.LAST_RIDE):
                co.async_add_listener(lambda: None, context=ConsumerContext(sn, need))
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


@pytest.mark.parametrize(
    "coordinator",
    [{"enable_controls": True, "control_vehicles": ["synthetic-one", "synthetic-two"]}],
    indirect=True,
)
async def test_partial_discovery_is_positive_evidence_not_vehicle_removal(coordinator):
    co = coordinator
    await co._async_update_data()
    old = co.data["synthetic-two"].profile_freshness.succeeded_at
    co.client.vehicle_discovery_complete = False
    co.client.async_list_vehicles.return_value = [{"wnumber": "synthetic-one"}]
    co._next_attempt[("", "profile")] = 0
    await co._async_update_data()
    assert co.data["synthetic-two"].present
    assert co.data["synthetic-two"].profile_freshness.succeeded_at == old
    assert co.data["synthetic-two"].profile_freshness.error is ErrorKind.SERVICE
    assert co.discovery_diagnostics()["complete"] is False
    assert co.fresh("synthetic-one", "profile")
    assert co.controls_enabled("synthetic-one", "bell")
    assert not co.controls_enabled("synthetic-two", "bell")
    assert "profile_query_failed" in co.control_decision("synthetic-two", "bell").blockers
    with patch(
        "custom_components.ninebot.coordinator.dt_util.utcnow",
        return_value=old + timedelta(hours=4),
    ):
        assert not co.fresh("synthetic-two", "profile")
    co.client.vehicle_discovery_complete = True
    co._next_attempt[("", "profile")] = 0
    await co._async_update_data()
    assert not co.data["synthetic-two"].present
    assert co.discovery_diagnostics()["complete"] is True


async def test_empty_month_preserved_while_last_ride_falls_back(coordinator, freezer):
    co = coordinator

    async def travel(sn, month):
        if month == "202610":
            return {"total_mileages": "0.0", "ec": 0, "list": None}
        return {"total_mileages": "303.9", "ec": 7210, "list": [{"mileages": 0.3, "ec": 5}]}

    co.client.async_get_travel.side_effect = travel
    freezer.move_to(datetime(2026, 10, 3, tzinfo=UTC))
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


async def test_late_status_parser_cannot_restore_old_raw_or_other_groups(coordinator):
    from custom_components.ninebot import adapters
    from custom_components.ninebot.raw import Endpoint

    co = coordinator
    await co._async_update_data()
    started, release = asyncio.Event(), asyncio.Event()
    original = co.hass.async_add_executor_job

    async def paused(target, *args):
        if (
            getattr(target, "func", None) is adapters.status
            and target.args[0].get("dump_energy") == 55
        ):
            started.set()
            await release.wait()
        return await original(target, *args)

    co.client.async_get_status.return_value = {"dump_energy": 55}
    with patch.object(co.hass, "async_add_executor_job", side_effect=paused):
        older = asyncio.create_task(co.async_refresh_vehicle("synthetic-one"))
        await started.wait()
        co.client.async_get_battery.return_value = {"battery_list": [{"bms_volt": 75}]}
        await co._group("synthetic-one", "battery", force=True)
        co._barriers["synthetic-one"] = co._barriers.get("synthetic-one", 0) + 1
        co.client.async_get_status.return_value = {"dump_energy": 20}
        assert await co.async_refresh_vehicle("synthetic-one")
        latest = co.raw.get(Endpoint.STATUS, "synthetic-one", now=datetime.now(UTC))
        release.set()
        await older
    assert co.data["synthetic-one"].status.battery == 20
    assert co.data["synthetic-one"].battery.batteries[0].voltage == 75
    assert co.raw.get(Endpoint.STATUS, "synthetic-one", now=datetime.now(UTC)) is latest
    assert latest.payload()["dump_energy"] == 20


@pytest.mark.parametrize("failed", [False, True])
async def test_poll_and_history_share_one_inflight_month_including_failure(coordinator, failed):
    from custom_components.ninebot import adapters
    from custom_components.ninebot.raw import Endpoint

    co = coordinator
    await co._async_update_data()
    sn = "synthetic-one"
    month = adapters.month_at(datetime.now(UTC))
    co.raw.discard_vehicle(sn)
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def travel(vehicle, query_month):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        if failed:
            raise NinebotError(ErrorKind.SERVICE)
        return {"total_mileages": 12, "ec": 30, "list": []}

    co.client.async_get_travel.side_effect = travel
    poll = asyncio.create_task(co._group(sn, "travel", force=True, include_last_ride=False))
    await started.wait()
    query = asyncio.create_task(co.async_query_month(sn, month))
    for _ in range(100):
        if co.broker.diagnostics()["waiters"] == 2:
            break
        await asyncio.sleep(0)
    assert co.broker.diagnostics()["waiters"] == 2
    release.set()
    results = await asyncio.gather(poll, query, return_exceptions=True)
    assert calls == 1
    if failed:
        assert isinstance(results[1], NinebotError)
        assert co.data[sn].travel_freshness.error is ErrorKind.SERVICE
        assert co.raw.get(Endpoint.TRAVEL, sn, month, now=datetime.now(UTC)) is None
    else:
        assert results[1].payload()["total_mileages"] == 12
        assert co.data[sn].travel.mileage == 12
        assert co.statistics.month(sn, month).distance_km == 12


async def test_same_clock_payload_keeps_new_receipt_revision(coordinator, freezer):
    from custom_components.ninebot.raw import Endpoint

    co = coordinator
    await co._async_update_data()
    first = co.raw.get(Endpoint.STATUS, "synthetic-one", now=datetime.now(UTC))
    await co.async_refresh_vehicle("synthetic-one")
    latest = co.raw.get(Endpoint.STATUS, "synthetic-one", now=datetime.now(UTC))
    assert latest.encoded == first.encoded and latest.received_at == first.received_at
    assert latest.request_revision > first.request_revision


async def test_overlapping_normalization_parses_same_content_once(coordinator):
    from custom_components.ninebot import adapters
    from custom_components.ninebot.coordinator import travel_record
    from custom_components.ninebot.raw import Endpoint

    co = coordinator
    await co._async_update_data()
    sn = "synthetic-one"
    month = adapters.month_at(datetime.now(UTC))
    record = co.raw.get(Endpoint.TRAVEL, sn, month, now=datetime.now(UTC))
    co._normalized.clear()
    started, release = asyncio.Event(), asyncio.Event()
    original = co.hass.async_add_executor_job
    calls = 0

    async def paused(target, *args):
        nonlocal calls
        if getattr(target, "func", None) is travel_record:
            calls += 1
            started.set()
            await release.wait()
        return await original(target, *args)

    from functools import partial

    with patch.object(co.hass, "async_add_executor_job", side_effect=paused):
        one = asyncio.create_task(
            co._normalize(record, sn, month, partial(travel_record, record, month))
        )
        await started.wait()
        two = asyncio.create_task(
            co._normalize(record, sn, month, partial(travel_record, record, month))
        )
        for _ in range(100):
            if co._shared_waiters.get(next(iter(co._normalizations.values())), 0) == 2:
                break
            await asyncio.sleep(0)
        release.set()
        result = await asyncio.gather(one, two)
    assert calls == 1 and result[0] is result[1]
    assert not co._normalizations


async def test_late_profile_parser_cannot_remove_newly_confirmed_vehicle(coordinator):
    from custom_components.ninebot import adapters

    co = coordinator
    await co._async_update_data()
    started, release = asyncio.Event(), asyncio.Event()
    original = co.hass.async_add_executor_job

    async def paused(target, *args):
        if target is adapters.profiles and len(args[0]) == 1:
            started.set()
            await release.wait()
        return await original(target, *args)

    co.client.async_list_vehicles.return_value = [{"wnumber": "synthetic-one"}]
    co._next_attempt[("", "profile")] = 0
    with patch.object(co.hass, "async_add_executor_job", side_effect=paused):
        older = asyncio.create_task(co._list(datetime.now(UTC)))
        await started.wait()
        co.client.async_list_vehicles.return_value = [
            {"wnumber": "synthetic-one"},
            {"wnumber": "synthetic-two"},
        ]
        await co._list(datetime.now(UTC))
        release.set()
        await older
    assert co.data["synthetic-two"].present
    assert co.fresh("synthetic-two", "status")


async def test_ownership_loss_during_ledger_preparation_cannot_commit_or_observe(coordinator):
    from custom_components.ninebot import adapters
    from custom_components.ninebot.ride_lifecycle import RideLifecycle

    co = coordinator
    await co._async_update_data()
    sn = "synthetic-one"
    month = adapters.month_at(datetime.now(UTC))
    before = co.statistics.month(sn, month)
    started, release = asyncio.Event(), asyncio.Event()
    original = co.hass.async_add_executor_job

    async def paused(target, *args):
        if getattr(target, "__name__", "") == "update" and args[1].mileage == 99:
            started.set()
            await release.wait()
        return await original(target, *args)

    co.client.async_get_travel.return_value = {"total_mileages": 99, "list": []}
    with (
        patch.object(co.hass, "async_add_executor_job", side_effect=paused),
        patch.object(RideLifecycle, "observe") as observe,
    ):
        old = asyncio.create_task(co._group(sn, "travel", force=True, include_last_ride=False))
        await started.wait()
        co._ownership[sn] = co._ownership.get(sn, 0) + 1
        release.set()
        await old
        observe.assert_not_called()
    assert co.statistics.month(sn, month) is before
    assert co.data[sn].travel.mileage != 99


async def test_history_queue_rechecks_profile_before_wire(coordinator, freezer):
    co = coordinator
    await co._async_update_data()
    started, release = asyncio.Event(), asyncio.Event()

    async def hold(sn):
        started.set()
        await release.wait()
        return {"dump_energy": 70}

    co.client.async_get_status.side_effect = hold
    co.client.async_get_travel.reset_mock()
    active = asyncio.create_task(co.async_refresh_vehicle("synthetic-one"))
    await started.wait()
    query = asyncio.create_task(co.async_query_month("synthetic-one", "202001"))
    for _ in range(100):
        if co.broker.diagnostics()["pending"] == 2:
            break
        await asyncio.sleep(0)
    assert co.broker.diagnostics()["pending"] == 2
    freezer.tick(timedelta(hours=4))
    release.set()
    await active
    with pytest.raises(HomeAssistantError) as caught:
        await query
    assert caught.value.translation_key == "query_unavailable"
    co.client.async_get_travel.assert_not_awaited()


async def test_wrong_status_identity_cannot_replace_telemetry_raw_or_success_time(coordinator):
    from custom_components.ninebot.raw import Endpoint

    co = coordinator
    await co._async_update_data()
    before = co.data["synthetic-one"]
    record = co.raw.get(Endpoint.STATUS, "synthetic-one", now=datetime.now(UTC))
    co.client.async_get_status.return_value = {"sn": "synthetic-two", "dump_energy": 3}
    await co.async_refresh_vehicle("synthetic-one")
    after = co.data["synthetic-one"]
    assert after.status == before.status
    assert after.status_freshness.succeeded_at == before.status_freshness.succeeded_at
    assert after.status_freshness.error is ErrorKind.PROTOCOL
    # Preserve bounded cached-value freshness after a partial failure. The
    # explicit query-error gate still prevents dispatching controls.
    assert co.fresh("synthetic-one", "status")
    assert co.fresh("synthetic-one", "battery")
    assert co.fresh("synthetic-two", "status")
    assert co.raw.get(Endpoint.STATUS, "synthetic-one", now=datetime.now(UTC)) is record
    assert co._authenticated
    assert not co.controls_enabled("synthetic-one", "bell")
    assert "status_query_failed" in co.control_decision("synthetic-one", "bell").blockers


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


async def test_rediscovery_requeries_all_groups_without_reviving_old_status(coordinator):
    co = coordinator
    await co._async_update_data()
    co.client.async_list_vehicles.return_value = [{"wnumber": "synthetic-two"}]
    co._next_attempt[("", "profile")] = 0
    await co._async_update_data()
    assert not co.data["synthetic-one"].present
    for group in ("status", "battery", "travel"):
        co._next_attempt[("synthetic-one", group)] = float("inf")
    co.client.async_get_status.side_effect = NinebotError(ErrorKind.CONNECTION)
    co.client.async_get_battery.reset_mock()
    co.client.async_get_travel.reset_mock()
    co.client.async_list_vehicles.return_value.append({"wnumber": "synthetic-one"})
    co._next_attempt[("", "profile")] = 0
    await co._async_update_data()
    assert co.data["synthetic-one"].present
    assert co.data["synthetic-one"].status.battery is None
    assert not co.fresh("synthetic-one", "status")
    assert co.fresh("synthetic-one", "battery")
    co.client.async_get_battery.assert_awaited_once_with("synthetic-one")
    assert co.client.async_get_travel.await_args_list[0].args[0] == "synthetic-one"


async def test_controls_disabled_without_explicit_option(coordinator):
    co = coordinator
    await co._async_update_data()
    with pytest.raises(HomeAssistantError):
        await co.async_control("synthetic-one", "bell")
    co.client.async_control.assert_not_awaited()


@pytest.mark.parametrize(
    "coordinator", [{"enable_controls": True, "control_vehicles": ["synthetic-one"]}], indirect=True
)
async def test_unknown_permissions_reach_cloud_only_with_user_consent(coordinator):
    co = coordinator
    co.client.async_get_status.return_value = {
        "permissions": None,
        "support": True,
        "loc": {"lock": 1},
        "pwr": 1,
    }
    await co._async_update_data()
    for action in ("bell", "buck", "engine/start", "engine/stop"):
        assert co.controls_enabled("synthetic-one", action)
        assert co.control_decision("synthetic-one", action).permission is CapabilityState.UNKNOWN
        await co.async_control("synthetic-one", action)
    assert co.client.async_control.await_count == 4
    assert not co.controls_enabled("synthetic-one", "arbitrary")
    with pytest.raises(HomeAssistantError):
        await co.async_control("synthetic-one", "arbitrary")
    assert co.client.async_control.await_count == 4


@pytest.mark.parametrize("coordinator", [True], indirect=True)
async def test_gate_is_action_specific_and_rechecks_permission_after_queue_wait(coordinator):
    co = coordinator
    await co._async_update_data()
    assert co.controls_enabled("synthetic-one", "bell")
    assert not co.controls_enabled("synthetic-two", "bell")
    snapshot = co.data["synthetic-one"]
    co.data["synthetic-one"] = replace(
        snapshot,
        status=replace(
            snapshot.status,
            capabilities=VehicleCapabilities(
                (ControlCapability("buck", permission=CapabilityState.DENIED),)
            ),
        ),
    )
    assert not co.controls_enabled("synthetic-one", "buck")
    assert co.controls_enabled("synthetic-one", "bell")
    await co._mutex.acquire()
    task = asyncio.create_task(co.async_control("synthetic-one", "bell"))
    await asyncio.sleep(0)
    snapshot = co.data["synthetic-one"]
    co.data["synthetic-one"] = replace(
        snapshot,
        status=replace(
            snapshot.status,
            capabilities=VehicleCapabilities(
                (ControlCapability("bell", permission=CapabilityState.DENIED),)
            ),
        ),
    )
    co._mutex.release()
    with pytest.raises(HomeAssistantError):
        await task
    co.client.async_control.assert_not_awaited()
    co.data["synthetic-one"] = snapshot
    later = datetime.now(UTC) + timedelta(days=1)
    with patch("custom_components.ninebot.coordinator.dt_util.utcnow", return_value=later):
        assert not co.controls_enabled("synthetic-one", "bell")
    co.data["synthetic-one"] = replace(
        snapshot, status_freshness=replace(snapshot.status_freshness, error=ErrorKind.SERVICE)
    )
    assert not co.controls_enabled("synthetic-one", "bell")


async def test_raw_capture_unknown_fields_policy_failure_and_unload(coordinator):
    from custom_components.ninebot.raw import Endpoint

    co = coordinator
    co.client.async_get_status.return_value = {"dump_energy": 80, "future_value": {"x": 10}}
    await co._async_update_data()
    record = co.raw.get(Endpoint.STATUS, "synthetic-one", now=datetime.now(UTC))
    assert record.payload()["future_value"] == {"x": 10}
    co.client.async_get_status.return_value = {"dump_energy": 75, "too_deep": [[None]] * 25001}
    await co.async_refresh_vehicle("synthetic-one")
    assert co.data["synthetic-one"].status.battery == 75
    assert co.raw.rejected == 1
    assert co.raw.get(Endpoint.STATUS, "synthetic-one", now=datetime.now(UTC)) is record
    await co.async_close()
    assert co.raw.retained_bytes == 0


async def test_history_query_reuses_polling_payload_without_changing_current_state(coordinator):
    from custom_components.ninebot.raw import Endpoint

    co = coordinator
    await co._async_update_data()
    month = co.data["synthetic-one"].travel.month
    before = dict(co.data)
    co.client.async_get_travel.reset_mock()
    record = await co.async_query_month("synthetic-one", month)
    assert record.endpoint is Endpoint.TRAVEL and record.query_month == month
    co.client.async_get_travel.assert_not_awaited()
    assert co.data == before
    co.client.async_get_travel.return_value = {"list": [{"travel_id": "synthetic-ride"}]}
    first, second = await asyncio.gather(
        co.async_query_month("synthetic-one", "202001"),
        co.async_query_month("synthetic-one", "202001"),
    )
    assert first is second
    co.client.async_get_travel.assert_awaited_once_with("synthetic-one", "202001")
    assert co.data == before
    co.client.async_get_trip_detail.return_value = {"duration": 2}
    detail = await co.async_query_detail("synthetic-one", "synthetic-ride", "202001")
    assert detail.endpoint is Endpoint.TRIP_DETAIL and detail.query_month == "202001"
    assert await co.async_query_detail("synthetic-one", "synthetic-ride", "202001") is detail
    co.client.async_get_trip_detail.assert_awaited_once()
    other = await co.async_query_detail("synthetic-one", "synthetic-ride", "202002")
    assert other is not detail and co.client.async_get_trip_detail.await_count == 2


async def test_newer_action_month_is_reused_by_poll_without_extending_timestamp(
    coordinator, freezer
):
    co = coordinator
    now = datetime(2026, 9, 26, tzinfo=UTC)
    freezer.move_to(now)
    co.client.async_get_travel.return_value = {"list": [{"travel_id": "one"}], "total_mileages": 2}
    await co._async_update_data()
    freezer.move_to(now + timedelta(seconds=601))
    co.client.async_get_travel.return_value = {"list": [{"travel_id": "two"}], "total_mileages": 3}
    record = await co.async_query_month("synthetic-one", "202609")
    assert co.data["synthetic-one"].travel.mileage == 2
    co.client.async_get_travel.reset_mock()
    freezer.move_to(now + timedelta(seconds=650))
    await co._group("synthetic-one", "travel")
    co.client.async_get_travel.assert_not_awaited()
    assert co.data["synthetic-one"].travel.mileage == 3
    assert co.data["synthetic-one"].travel_freshness.succeeded_at == record.received_at
    assert co._next_attempt[("synthetic-one", "travel")] == record.received_at.timestamp() + 600


async def test_history_query_auth_policy_queue_and_unload(coordinator):
    co = coordinator
    await co._async_update_data()
    with pytest.raises(NinebotError):
        await co.async_query_detail("synthetic-one", "", "202001")
    with pytest.raises(HomeAssistantError):
        await co.async_query_month("missing", "202001")
    co.client.async_get_travel.return_value = [None] * 25001
    with pytest.raises(NinebotError):
        await co.async_query_month("synthetic-one", "202001")
    co.client.async_get_travel.side_effect = NinebotAuthError()
    with pytest.raises(ConfigEntryAuthFailed):
        await co.async_query_month("synthetic-one", "202001")
    assert not co._authenticated and co._query_pending == 0
    co._authenticated = True
    co.client.async_get_travel.side_effect = None
    co.client.async_get_travel.reset_mock()
    started, release = asyncio.Event(), asyncio.Event()

    async def hold(sn, month):
        started.set()
        await release.wait()
        return {"list": []}

    co.client.async_get_travel.side_effect = hold
    tasks = [asyncio.create_task(co.async_query_month("synthetic-one", "202001")) for _ in range(4)]
    await started.wait()
    with pytest.raises(HomeAssistantError) as error:
        await co.async_query_month("synthetic-one", "202001")
    assert error.value.translation_key == "busy"
    await co.async_close()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert co._query_pending == 0
    co.client.async_get_travel.assert_awaited_once_with("synthetic-one", "202001")


async def test_removed_ownership_clears_private_query_cache(coordinator):
    from custom_components.ninebot.raw import Endpoint

    co = coordinator
    await co._async_update_data()
    assert co.raw.get(Endpoint.STATUS, "synthetic-one", now=datetime.now(UTC))
    co.client.async_list_vehicles.return_value = [{"wnumber": "synthetic-two"}]
    co._next_attempt[("", "profile")] = 0
    await co._async_update_data()
    assert co.raw.get(Endpoint.STATUS, "synthetic-one", now=datetime.now(UTC)) is None
    with pytest.raises(HomeAssistantError):
        await co.async_query_month("synthetic-one", "202001")


async def test_cancelled_raw_preparation_cannot_repopulate_unloaded_cache(coordinator):
    import threading

    from custom_components.ninebot.raw import build_record

    co = coordinator
    await co._async_update_data()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def slow_prepare(*args, **kwargs):
        started.set()
        release.wait(2)
        record = build_record(*args, **kwargs)
        finished.set()
        return record

    with patch("custom_components.ninebot.coordinator.build_record", side_effect=slow_prepare):
        task = asyncio.create_task(co.async_refresh_vehicle("synthetic-one"))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            await co.async_close()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)
    assert co.raw.retained_bytes == 0


@pytest.mark.parametrize("coordinator", [True], indirect=True)
@pytest.mark.parametrize("readback_kind", [None, ErrorKind.SERVICE])
async def test_control_timeout_never_retried(coordinator, readback_kind):
    co = coordinator
    await co._async_update_data()
    co.client.async_get_status.reset_mock()
    if readback_kind:
        co.client.async_get_status.side_effect = NinebotError(readback_kind)
    co.client.async_control.side_effect = NinebotError(ErrorKind.CONNECTION)
    with pytest.raises(HomeAssistantError) as error:
        await co.async_control("synthetic-one", "bell")
    assert error.value.translation_key == "control_uncertain"
    co.client.async_control.assert_awaited_once()
    co.client.async_get_status.assert_awaited_once_with("synthetic-one")
    result = co.control_results.diagnostics("synthetic-one")["bell"]
    assert result["outcome"] == "uncertain"
    assert result["error"] == "connection"
    assert result["readback"] == ("failed" if readback_kind else "refreshed")
    assert result["readback_error"] == (readback_kind.value if readback_kind else None)
    assert result["finished_at"] is not None
    assert result["physical_outcome_verified"] is False
    co.config_entry.async_start_reauth.assert_not_called()


@pytest.mark.parametrize("coordinator", [True], indirect=True)
async def test_control_cannot_claim_readback_when_vehicle_disappeared(coordinator):
    co = coordinator
    await co._async_update_data()
    co.client.async_get_status.reset_mock()

    async def disappear(sn, action):
        co.data[sn] = replace(co.data[sn], present=False)

    co.client.async_control.side_effect = disappear
    with pytest.raises(HomeAssistantError) as error:
        await co.async_control("synthetic-one", "bell")
    assert error.value.translation_key == "control_readback_failed"
    co.client.async_control.assert_awaited_once()
    co.client.async_get_status.assert_not_awaited()
    result = co.control_results.diagnostics("synthetic-one")["bell"]
    assert result["outcome"] == "accepted"
    assert result["readback"] == "skipped"
    assert result["physical_outcome_verified"] is False


@pytest.mark.parametrize("coordinator", [True], indirect=True)
async def test_completed_manual_refresh_cannot_substitute_for_post_command_readback(coordinator):
    co = coordinator
    await co._async_update_data()
    co.client.async_get_status.reset_mock()
    first, second = asyncio.Event(), asyncio.Event()
    release_first, release_second = asyncio.Event(), asyncio.Event()
    requests = 0

    async def status(sn):
        nonlocal requests
        requests += 1
        if requests == 1:
            first.set()
            await release_first.wait()
        else:
            second.set()
            await release_second.wait()
        return {"dump_energy": 55}

    co.client.async_get_status.side_effect = status
    manual = asyncio.create_task(co.async_refresh_vehicle("synthetic-one"))
    await first.wait()
    command = asyncio.create_task(co.async_control("synthetic-one", "bell"))
    try:
        await asyncio.sleep(0)  # Command queues behind the active manual query.
        release_first.set()
        await asyncio.wait_for(second.wait(), 2)
        assert await manual is True
        assert not co._forced["synthetic-one"].done()
        release_second.set()
        await command
        assert requests == 2
        assert not co._forced
        assert co.control_results.diagnostics("synthetic-one")["bell"]["readback"] == "refreshed"
    finally:
        release_first.set()
        release_second.set()
        await asyncio.gather(manual, command, return_exceptions=True)


@pytest.mark.parametrize("coordinator", [True], indirect=True)
@pytest.mark.parametrize("phase", ["command", "readback"])
async def test_control_cancellation_keeps_phase_evidence_without_extra_io(coordinator, phase):
    co = coordinator
    await co._async_update_data()
    co.client.async_get_status.reset_mock()
    started = asyncio.Event()

    async def blocked(*args):
        started.set()
        await asyncio.Event().wait()

    if phase == "command":
        co.client.async_control.side_effect = blocked
    else:
        co.client.async_get_status.side_effect = blocked
    task = asyncio.create_task(co.async_control("synthetic-one", "bell"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    co.client.async_control.assert_awaited_once()
    assert co.client.async_get_status.await_count == (phase == "readback")
    result = co.control_results.diagnostics("synthetic-one")["bell"]
    assert result["outcome"] == ("cancelled" if phase == "command" else "accepted")
    assert result["readback"] == ("skipped" if phase == "command" else "cancelled")
    assert result["finished_at"] is not None
    assert not co._forced and not co._active and co._control_pending == 0
    assert not co._mutex.locked()


@pytest.mark.parametrize("coordinator", [True], indirect=True)
async def test_control_auth_error_starts_reauth_without_resending(coordinator):
    co = coordinator
    await co._async_update_data()
    co.client.async_get_status.reset_mock()
    co.client.async_control.side_effect = NinebotAuthError()
    with pytest.raises(HomeAssistantError) as error:
        await co.async_control("synthetic-one", "bell")
    assert error.value.translation_key == "control_uncertain"
    co.config_entry.async_start_reauth.assert_called_once_with(co.hass)
    co.client.async_control.assert_awaited_once()
    co.client.async_get_status.assert_not_awaited()
    assert not co.fresh("synthetic-one", "status")
    result = co.control_results.diagnostics("synthetic-one")["bell"]
    assert result["outcome"] == "authentication_required"
    assert result["error"] == "auth" and result["readback"] == "skipped"


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
async def test_optional_previous_month_failure_cannot_change_current_totals(
    coordinator, auth, freezer
):
    co = coordinator

    async def travel(sn, month):
        if month == "202610":
            return {"total_mileages": 0, "ec": 0, "list": []}
        raise NinebotAuthError() if auth else NinebotError(ErrorKind.CONNECTION)

    co.client.async_get_travel.side_effect = travel
    freezer.move_to(datetime(2026, 10, 3, tzinfo=UTC))
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
    result = co.control_results.diagnostics("synthetic-one")["bell"]
    assert result["outcome"] == "accepted" and result["error"] is None
    assert result["readback"] == "failed"
    assert result["readback_error"] == ("auth" if auth else "connection")


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


async def test_freshness_measures_actual_request_completion_and_retry_is_bounded(coordinator):
    from unittest.mock import MagicMock

    co = coordinator
    await co._async_update_data()
    start = datetime(2026, 10, 3, tzinfo=UTC)
    now = MagicMock(return_value=start)

    async def status(sn):
        now.return_value = start + timedelta(seconds=20)
        return {"dump_energy": 75}

    co.client.async_get_status.side_effect = status
    with patch("custom_components.ninebot.coordinator.dt_util.utcnow", now):
        await co.async_refresh_vehicle("synthetic-one")
    freshness = co.data["synthetic-one"].status_freshness
    assert freshness.attempted_at == start
    assert freshness.succeeded_at == start + timedelta(seconds=20)
    assert co._next_attempt[("synthetic-one", "status")] == start.timestamp() + 20 + co.interval
    co._attempt_finished("synthetic-one", "status", start.timestamp(), 120, False)
    delay = co._next_attempt[("synthetic-one", "status")] - start.timestamp()
    assert 24 <= delay <= 36
    for _ in range(20):
        co._attempt_finished("synthetic-one", "status", start.timestamp(), 120, False)
    assert co._next_attempt[("synthetic-one", "status")] <= start.timestamp() + 120


@pytest.mark.parametrize("coordinator", [True], indirect=True)
async def test_control_diagnostics_and_execution_share_all_gates(coordinator):
    co = coordinator
    await co._async_update_data()
    assert co.control_decision("synthetic-one", "bell").allowed
    co.backend.control_actions = frozenset()
    denied = co.control_decision("synthetic-one", "bell")
    assert denied.blockers == ("transport_unsupported",)
    assert not co.controls_enabled("synthetic-one", "bell")
    with pytest.raises(HomeAssistantError):
        await co.async_control("synthetic-one", "bell")
    co.client.async_control.assert_not_awaited()
    assert "vehicle_not_present" in co.control_decision("missing", "bell").blockers
    co._authenticated = False
    assert "authentication_required" in co.control_decision("synthetic-one", "bell").blockers
    await co.async_close()
    assert "runtime_stopped" in co.control_decision("synthetic-one", "bell").blockers


async def test_contexts_skip_unneeded_groups_and_scope_month_fallback(coordinator, freezer):
    from custom_components.ninebot.demand import ConsumerContext, Need

    co = coordinator
    co.client.async_get_battery.return_value = {"battery_list": [{}]}
    now = datetime(2026, 10, 4, tzinfo=UTC)
    freezer.move_to(now)
    await co._async_update_data()
    freezer.move_to(now + timedelta(seconds=601))
    for sn in co.data:
        for group in ("status", "battery", "travel"):
            co._next_attempt[(sn, group)] = 0
    for method in (
        co.client.async_get_status,
        co.client.async_get_battery,
        co.client.async_get_travel,
    ):
        method.reset_mock()
    with patch.object(
        co, "async_contexts", return_value=(ConsumerContext("synthetic-one", Need.STATUS),)
    ):
        await co._async_update_data()
    co.client.async_get_status.assert_awaited_once_with("synthetic-one")
    co.client.async_get_battery.assert_not_awaited()
    co.client.async_get_travel.assert_not_awaited()
    with patch.object(
        co, "async_contexts", return_value=(ConsumerContext("synthetic-one", Need.MONTH),)
    ):
        await co._async_update_data()
    co.client.async_get_travel.assert_awaited_once_with("synthetic-one", "202610")
    co.client.async_get_travel.reset_mock()
    co._next_attempt[("synthetic-one", "travel")] = 0
    with patch.object(
        co, "async_contexts", return_value=(ConsumerContext("synthetic-one", Need.RIDE_EVENT),)
    ):
        await co._async_update_data()
    assert co.client.async_get_travel.await_args_list == [
        call("synthetic-one", "202610"),
        call("synthetic-one", "202609"),
    ]


async def test_new_vehicle_and_failed_battery_discovery_do_not_need_existing_entities(
    coordinator, freezer
):
    co = coordinator
    now = datetime(2026, 10, 4, tzinfo=UTC)
    freezer.move_to(now)
    co.client.async_get_battery.side_effect = NinebotError(ErrorKind.CONNECTION)
    with patch.object(co, "async_contexts", return_value=()):
        await co._async_update_data()
        assert all(value.battery_freshness.succeeded_at is None for value in co.data.values())
        freezer.move_to(now + timedelta(seconds=60))
        co.client.async_get_battery.side_effect = None
        co.client.async_get_battery.return_value = {"battery_list": [{}]}
        co.client.async_get_status.reset_mock()
        co.client.async_get_travel.reset_mock()
        await co._async_update_data()
        assert all(value.battery_freshness.succeeded_at is not None for value in co.data.values())
        co.client.async_get_status.assert_not_awaited()
        co.client.async_get_travel.assert_not_awaited()
        co.client.async_get_battery.reset_mock()
        freezer.move_to(now + timedelta(seconds=601))
        await co._async_update_data()
        co.client.async_get_battery.assert_not_awaited()
        co.client.async_list_vehicles.return_value.append({"wnumber": "new-vehicle"})
        co._next_attempt[("", "profile")] = 0
        await co._async_update_data()
    co.client.async_get_status.assert_awaited_once_with("new-vehicle")
    co.client.async_get_battery.assert_awaited_once_with("new-vehicle")
    assert co.client.async_get_travel.await_count == 2


@pytest.mark.parametrize("coordinator", [{"enable_estimation": True}], indirect=True)
async def test_retired_soc_model_does_not_force_endpoint_requests(coordinator):
    co = coordinator
    co.client.async_get_battery.return_value = {"battery_list": [{}]}
    await co._async_update_data()
    for sn in co.data:
        for group in ("status", "battery", "travel"):
            co._next_attempt[(sn, group)] = 0
    co.client.reset_mock()
    with patch.object(co, "async_contexts", return_value=()):
        await co._async_update_data()
    co.client.async_get_status.assert_not_awaited()
    co.client.async_get_battery.assert_not_awaited()
    co.client.async_get_travel.assert_not_awaited()


async def test_local_month_boundary_expires_travel_without_cloud_io(coordinator):
    co = coordinator
    before = datetime(2026, 9, 30, 15, 59, 59, tzinfo=UTC)
    with patch("custom_components.ninebot.coordinator.dt_util.utcnow", return_value=before):
        await co._async_update_data()
        with patch("custom_components.ninebot.coordinator.async_track_point_in_utc_time") as timer:
            co._schedule_validity_check()
            notify, deadline = timer.call_args.args[1:]
            assert deadline == before + timedelta(seconds=1)
            co.client.reset_mock()
            with patch(
                "custom_components.ninebot.coordinator.dt_util.utcnow", return_value=deadline
            ):
                notify(deadline)
                assert not co.fresh("synthetic-one", "travel")
            co.client.async_get_status.assert_not_awaited()
            co.client.async_get_travel.assert_not_awaited()


async def test_raw_capture_propagates_metadata_without_discarding_valid_telemetry(coordinator):
    from custom_components.ninebot.backend import BackendResult
    from custom_components.ninebot.raw import Endpoint

    co = coordinator
    result = BackendResult(
        {"dump_energy": 80},
        Endpoint.STATUS,
        datetime.now(UTC),
        backend_version="0.1.8",
        endpoint_version="v5",
    )
    record = await co._capture(result, "synthetic-one")
    assert record.backend_version == "0.1.8"
    assert record.endpoint_version == "v5"
    assert record.unknown_schema_complete
    bad = replace(result, payload={"dump_energy": 80, "unknown": "a" * 1048577})
    assert await co._capture(bad, "synthetic-one") is None
    assert co.raw.diagnostics(datetime.now(UTC))["rejection_reasons"] == {"size": 1}
    assert co.raw.get(Endpoint.STATUS, "synthetic-one", now=datetime.now(UTC)) is record


async def test_local_day_boundary_notifies_without_cloud_requests(coordinator):
    co = coordinator
    before = datetime(2026, 9, 25, 15, 59, 59, tzinfo=UTC)
    with patch("custom_components.ninebot.coordinator.dt_util.utcnow", return_value=before):
        await co._async_update_data()
        with patch("custom_components.ninebot.coordinator.async_track_point_in_utc_time") as timer:
            co._schedule_validity_check()
            notify, deadline = timer.call_args.args[1:]
            assert deadline == before + timedelta(seconds=1)
            co.client.reset_mock()
            with patch(
                "custom_components.ninebot.coordinator.dt_util.utcnow", return_value=deadline
            ):
                notify(deadline)
            co.client.async_get_travel.assert_not_awaited()
            co.client.async_get_status.assert_not_awaited()


@pytest.mark.parametrize(
    "day,expected", [(1, ["202610", "202609"]), (2, ["202610", "202609"]), (3, ["202610"])]
)
async def test_day_demand_rollover_is_bounded_cached_and_keeps_current_totals(
    coordinator, freezer, day, expected
):
    co = coordinator
    freezer.move_to(datetime(2026, 10, day, 4, tzinfo=UTC))
    co.client.async_get_travel.return_value = {"total_mileages": 10, "times": 0, "list": []}
    await co._list(datetime.now(UTC))
    await co._group("synthetic-one", "travel", include_last_ride=False, include_previous_month=True)
    assert [call.args[1] for call in co.client.async_get_travel.await_args_list] == expected
    assert co.data["synthetic-one"].travel.month == "202610"
    assert co.data["synthetic-one"].travel.last_ride is None
    co.client.async_get_travel.reset_mock()
    await co._group("synthetic-one", "travel", include_last_ride=False, include_previous_month=True)
    co.client.async_get_travel.assert_not_awaited()
    co.client.async_get_trip_detail.assert_not_awaited()
    co.client.async_control.assert_not_awaited()


async def test_adjacent_query_latency_does_not_renew_current_month_receipt(coordinator, freezer):
    co = coordinator
    now = datetime(2026, 10, 1, 4, tzinfo=UTC)
    freezer.move_to(now)
    await co._list(now)

    async def delayed_previous(sn, month):
        if month == "202609":
            freezer.tick(timedelta(seconds=30))
        return {"times": 0, "list": [], "total_mileages": 0}

    co.client.async_get_travel.side_effect = delayed_previous
    await co._group("synthetic-one", "travel", include_last_ride=False, include_previous_month=True)
    assert co.data["synthetic-one"].travel_freshness.succeeded_at == now
    assert co.statistics.month("synthetic-one", "202610").received_at == now.isoformat()
    assert (
        co.statistics.month("synthetic-one", "202609").received_at
        == (now + timedelta(seconds=30)).isoformat()
    )
