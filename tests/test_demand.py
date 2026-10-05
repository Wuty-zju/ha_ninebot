"""Consumer dependency scenarios, not cloud traffic or production registries."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ninebot.adapters import batteries
from custom_components.ninebot.demand import ConsumerContext, Need, entity_context, polling_demand
from custom_components.ninebot.models import Freshness, VehicleProfile, VehicleSnapshot

NOW = datetime(2026, 10, 4, tzinfo=UTC)


def settled():
    fresh = Freshness(attempted_at=NOW, succeeded_at=NOW)
    return VehicleSnapshot(
        VehicleProfile("one", "name", "model"),
        battery=batteries({"battery_list": [{}]}),
        status_freshness=fresh,
        battery_freshness=fresh,
        travel_freshness=fresh,
    )


def demand(snapshot, *needs, now=NOW):
    return polling_demand(snapshot, [ConsumerContext("one", need) for need in needs], now=now)


@pytest.mark.parametrize(
    "need,groups,last",
    [
        (Need.PROFILE, set(), False),
        (Need.STATUS, {"status"}, False),
        (Need.BATTERY, {"battery"}, False),
        (Need.MONTH, {"travel"}, False),
        (Need.LAST_RIDE, {"travel"}, True),
        (Need.RIDE_EVENT, {"travel"}, True),
        (Need.CONTROL, {"status"}, False),
    ],
)
def test_each_consumer_requires_only_its_dependencies(need, groups, last):
    result = demand(settled(), need)
    assert result.groups == groups
    assert result.last_ride is last


def test_discovery_listeners_and_other_vehicles_do_not_imply_endpoint_demand():
    result = polling_demand(
        settled(),
        [None, ("one", "ride"), ConsumerContext("other", Need.BATTERY)],
        now=NOW,
    )
    assert not result.groups
    assert not result.last_ride
    assert "one" not in str(result.diagnostics())


def test_bootstrap_battery_failure_and_hourly_inventory_probe_are_distinct():
    first = VehicleSnapshot(VehicleProfile("one", "name", "model"))
    assert demand(first).groups == {"status", "battery", "travel"}
    assert demand(first).last_ride
    failed = replace(settled(), battery_freshness=Freshness(attempted_at=NOW))
    assert demand(failed).groups == {"battery"}
    empty = replace(settled(), battery=batteries({"battery_list": []}))
    assert not demand(empty, now=NOW + timedelta(seconds=3599)).groups
    assert demand(empty, now=NOW + timedelta(hours=1)).groups == {"battery"}
    attempted = replace(
        empty,
        battery_freshness=replace(empty.battery_freshness, attempted_at=NOW + timedelta(hours=1)),
    )
    assert not demand(attempted, now=NOW + timedelta(hours=1, seconds=60)).groups
    # Explicit consumer still uses ordinary per-group backoff, not hourly probing.
    assert demand(attempted, Need.BATTERY, now=NOW + timedelta(hours=1, seconds=60)).groups == {
        "battery"
    }
    multi_unknown = replace(empty, battery=batteries({"battery_list": [{}, {}]}))
    assert demand(multi_unknown, now=NOW + timedelta(hours=1)).groups == {"battery"}
    multi_known = replace(empty, battery=batteries({"battery_list": [{"sn": "a"}, {"sn": "b"}]}))
    assert not demand(multi_known, now=NOW + timedelta(hours=1)).groups


def test_removed_vehicle_never_creates_demand_even_for_model_and_event():
    result = demand(replace(settled(), present=False), Need.RIDE_EVENT)
    assert not result.groups and not result.last_ride
    assert result.reasons == ("vehicle_absent",)


@pytest.mark.parametrize(
    "platform,key,group,need",
    [
        ("sensor", "month_mileage", "travel", Need.MONTH),
        ("sensor", "last_mileage", "travel", Need.LAST_RIDE),
        ("event", "ride", "travel", Need.RIDE_EVENT),
        ("button", "bucket", "profile", Need.CONTROL),
        ("button", "refresh", "profile", Need.PROFILE),
        ("number", "main_battery_voltage", "profile", Need.PROFILE),
        ("device_tracker", "location", "status", Need.STATUS),
        ("sensor", "bms_voltage", "battery", Need.BATTERY),
    ],
)
def test_entity_context_preserves_the_actual_function_dependency(platform, key, group, need):
    assert entity_context("one", platform, key, group) == ConsumerContext("one", need)
