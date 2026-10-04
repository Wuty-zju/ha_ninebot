"""Recorded charging scalars replayed offline, plus synthetic identity/privacy cases."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot import adapters
from custom_components.ninebot.models import VehicleProfile, VehicleSnapshot
from custom_components.ninebot.observations import RAW_FIELDS, scalar_observations
from custom_components.ninebot.parsing import display_scalar, integer, raw_scalar
from custom_components.ninebot.sensor import SENSORS, battery_descriptions

FIXTURES = Path(__file__).parent / "fixtures/ninecli/0.1.7"


def recorded(name):
    return json.loads((FIXTURES / name).read_text())


def test_charging_replay_keeps_empty_estimate_and_conflicting_layered_values():
    status = adapters.status(recorded("status-charging.json"), expected_sn="fixture-charging")
    battery = adapters.batteries(recorded("battery-charging.json"))
    assert status.charging is True and status.battery == 80
    assert battery.charging_power_raw == 310
    assert battery.batteries[0].voltage == 79.6
    assert status.charge_remaining is None
    assert status.observations["remain_charge_time"] == ""
    assert status.observations["remain_charge_timestamp"] == 0
    assert battery.observations["remain_charge_time"] == ""
    assert battery.observations["battery_main.electricity"] == "93"
    assert battery.observations["battery_count"] == "0" and len(battery.batteries) == 1
    assert battery.batteries[0].cycles is None
    assert battery.batteries[0].cycle_raw == "100"
    assert battery.batteries[0].score_raw == 0
    snapshot = VehicleSnapshot(VehicleProfile("fixture", "Vehicle", "Model"), status, battery)
    descriptions = {d.key: d for d in (*SENSORS, *battery_descriptions(snapshot))}
    for key, expected, unit, device_class, state_class in [
        ("charging_power_raw", 310, "W", "power", "measurement"),
        ("health_score", 0, None, None, None),
        ("cycle_raw", "100", None, None, None),
        ("main_electricity_raw", "93", None, None, None),
        ("battery_count_raw", "0", None, None, None),
        ("returned_pack_count", 1, None, None, None),
    ]:
        d = descriptions[key]
        assert d.value(snapshot) == expected
        assert (d.native_unit_of_measurement, d.device_class, d.state_class) == (
            unit,
            device_class,
            state_class,
        )
        assert d.entity_registry_enabled_default
    assert (
        descriptions["remaining_charge_time"].attributes(snapshot)["interpretation"]
        == "not_reported"
    )
    assert descriptions["cycle_raw"].attributes(snapshot)["cycle_supported"] is False


def test_month_aggregates_and_energy_identity_do_not_use_partial_ride_sums():
    snapshot = VehicleSnapshot(
        VehicleProfile("fixture", "Vehicle", "Model"),
        travel=adapters.travel(
            {
                "times": 128,
                "duration": 59934,
                "total_mileages": "303.9",
                "ec": 7210,
                "list": [{"mileages": "0.30", "duration": 83, "ec": "5"}],
            },
            "202609",
        ),
    )
    descriptions = {d.key: d for d in SENSORS}
    assert descriptions["month_ride_count"].value(snapshot) == 128
    assert descriptions["month_duration"].value(snapshot) == 59934
    assert descriptions["month_duration"].native_unit_of_measurement == "s"
    assert descriptions["month_duration"].device_class == "duration"
    for key, expected in [("month_energy_raw", 7210), ("last_energy_raw", 5)]:
        d = descriptions[key]
        assert d.value(snapshot) == expected
        assert d.device_class == "energy" and d.native_unit_of_measurement == "Wh"
        assert d.state_class is None and d.entity_category is None
    for value in (True, 1.5, -1, "nan", {}, []):
        assert (
            adapters.travel({"times": value, "duration": value}, "202609").reported_ride_count
            is None
        )
    assert integer(0) == 0 and integer("128") == 128


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), {}, [], "x" * 256, "unsafe\nvalue", 10**19]
)
def test_raw_scalar_bounds(value):
    assert raw_scalar(value) is None


def test_scalar_types_absent_null_and_privacy_are_preserved_deliberately():
    assert raw_scalar(0) == 0 and raw_scalar("0") == "0" and raw_scalar(False) is False
    assert display_scalar(False) == "false" and display_scalar("") is None
    values = scalar_observations(
        {
            "battery_exist": 1,
            "permissions": None,
            "loc": {"acc": 0, "lat": 31.123},
            "password": "secret",
            "token": "secret",
            "owner_user_phone": "secret",
        },
        "status",
    )
    assert values == {"battery_exist": 1, "permissions": None, "loc.acc": 0}
    assert "remain_charge_time" not in values
    profile = adapters.profiles(
        [{"wnumber": "fixture", "owner_user_phone": "secret", "color": "silver"}]
    )[0]
    assert profile.observations == {"color": "silver"}
    aliases = adapters.profiles(
        [
            {"wnumber": "fixture", "support": 1},
            {"wnumber": "fixture", "support": 2},
        ]
    )
    assert len(aliases) == 1 and aliases[0].observations["support"] == 1
    snapshot = VehicleSnapshot(profile)
    for field in RAW_FIELDS:
        d = next(d for d in SENSORS if d.key == field.key)
        assert d.value(snapshot) is None
        assert d.attributes(snapshot)["interpretation"] == "not_reported"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_all_current_defaults_enable_without_granting_coordinates_or_controls(
    hass, entry, app_client
):
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("ninebot", "SyntheticSN")}
    )
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        "sensor",
        "ninebot",
        "SyntheticSN_charging_power_raw",
        config_entry=entry,
        device_id=device.id,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
        hidden_by=er.RegistryEntryHider.INTEGRATION,
        suggested_object_id="existing_charging_power",
    )
    registry.async_update_entity(legacy.entity_id, name="Personal charging power")
    app_client.async_get_battery.return_value = recorded("battery-charging.json")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    rows = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert all(row.disabled_by is None and row.hidden_by is None for row in rows)
    assert registry.async_get(legacy.entity_id).unique_id == legacy.unique_id
    assert registry.async_get(legacy.entity_id).name == "Personal charging power"
    assert float(hass.states.get(legacy.entity_id).state) == 310
    assert hass.states.get(legacy.entity_id).attributes["unit_of_measurement"] == "W"
    for platform, key in [("button", "bell"), ("device_tracker", "location")]:
        entity_id = registry.async_get_entity_id(platform, "ninebot", f"SyntheticSN_{key}")
        state = hass.states.get(entity_id)
        assert state.state == "unavailable"
        assert "latitude" not in state.attributes and "longitude" not in state.attributes
    app_client.async_control.assert_not_awaited()
    co = entry.runtime_data.coordinator
    co.async_set_updated_data(
        {
            "SyntheticSN": replace(
                co.data["SyntheticSN"], battery=adapters.batteries({"charging_power": 0})
            )
        }
    )
    await hass.async_block_till_done()
    assert float(hass.states.get(legacy.entity_id).state) == 0
    assert await hass.config_entries.async_unload(entry.entry_id)
