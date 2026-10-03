import json

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot.diagnostics import async_get_config_entry_diagnostics

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_full_setup_physical_values_and_unload(hass, entry, app_client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    for platform, key, expected in [
        ("sensor", "battery", 80),
        ("sensor", "endurance", 90),
        ("sensor", "bms_voltage", 75.3),
        ("sensor", "batt_temp", 25),
        ("sensor", "month_mileage", 0),
    ]:
        entity_id = registry.async_get_entity_id(platform, "ninebot", f"SyntheticSN_{key}")
        assert entity_id.startswith(f"{platform}.")
        state = hass.states.get(entity_id)
        assert state and float(state.state) == expected
    for key, value in [("charging", "off"), ("power", "on"), ("unlocked", "off")]:
        entity_id = registry.async_get_entity_id("binary_sensor", "ninebot", f"SyntheticSN_{key}")
        assert hass.states.get(entity_id).state == value
    assert registry.async_get_entity_id("sensor", "ninebot", "SyntheticSN_bms_cycles") is None
    raw_id = registry.async_get_entity_id("sensor", "ninebot", "SyntheticSN_month_energy_raw")
    assert registry.async_get(raw_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert registry.async_get(raw_id).unit_of_measurement is None
    assert hass.states.get(raw_id) is None
    diag = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(diag)
    for private in (
        "SyntheticSN",
        "fake-account",
        "synthetic-business",
        "latitude",
        "longitude",
        "tokens",
    ):
        assert private not in serialized
    assert await hass.config_entries.async_unload(entry.entry_id)
    app_client.async_close.assert_awaited()


async def test_preserve_custom_name_disabled_and_legacy_ids(hass, entry, app_client):
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("ninebot", "SyntheticSN")}
    )
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor",
        "ninebot",
        "ninebot_syntheticsn_battery",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="custom_battery",
    )
    registry.async_update_entity(old.entity_id, name="My battery")
    deprecated = registry.async_get_or_create(
        "sensor",
        "ninebot",
        "ninebot_syntheticsn_battery_outflow_energy_total",
        config_entry=entry,
        device_id=device.id,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get(old.entity_id).state) == 80
    assert registry.async_get(old.entity_id).name == "My battery"
    assert registry.async_get(deprecated.entity_id).disabled_by is er.RegistryEntryDisabler.USER
    assert registry.async_get_entity_id("sensor", "ninebot", "SyntheticSN_battery") is None


async def test_dynamic_car_and_readonly_refresh(hass, entry, app_client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    app_client.async_list_vehicles.return_value.append(
        {"wnumber": "AnotherSN", "device_name": "Second"}
    )
    co._next_attempt[("", "profile")] = 0
    await co.async_refresh()
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", "ninebot", "AnotherSN_battery")
    refresh = registry.async_get_entity_id("button", "ninebot", "SyntheticSN_refresh")
    app_client.async_get_status.reset_mock()
    await hass.services.async_call("button", "press", {"entity_id": refresh}, blocking=True)
    app_client.async_get_status.assert_awaited_once_with("SyntheticSN")
    app_client.async_control.assert_not_awaited()


async def test_optional_entities_explicit_models_and_mock_controls(hass, entry, app_client):
    from custom_components.ninebot.button import NinebotButton
    from custom_components.ninebot.device_tracker import NinebotTracker
    from custom_components.ninebot.image import NinebotImage
    from custom_components.ninebot.lock import NinebotLock
    from custom_components.ninebot.number import ModelNumber
    from custom_components.ninebot.sensor import EstimatedSensor

    hass.config_entries.async_update_entry(
        entry,
        options={
            "enable_estimation": True,
            "enable_coordinates": True,
            "enable_controls": True,
            "control_vehicles": ["SyntheticSN"],
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    voltage = ModelNumber(entry, "SyntheticSN", "main_battery_voltage", "voltage", "V", 300)
    capacity = ModelNumber(entry, "SyntheticSN", "battery_capacity", "capacity", "Ah", 500)
    assert voltage.native_value is None
    await voltage.async_set_native_value(72)
    await capacity.async_set_native_value(20)
    assert voltage.native_value == 72
    model = entry.runtime_data.models.model("SyntheticSN")
    estimated = EstimatedSensor(entry, "SyntheticSN", "nominal", model.generation)
    assert estimated.available
    assert estimated.native_value == 1.44
    assert estimated.extra_state_attributes["model_version"] == 2
    total = EstimatedSensor(entry, "SyntheticSN", "out_total", model.generation)
    assert total.native_value is None
    old = EstimatedSensor(entry, "SyntheticSN", "out_total", model.generation - 1)
    assert not old.available
    tracker = NinebotTracker(entry, "SyntheticSN")
    assert tracker.available
    assert tracker.latitude == tracker.longitude == 0
    assert tracker.entity_picture is None
    lock = NinebotLock(entry, "SyntheticSN")
    assert lock.is_locked is True
    assert lock.extra_state_attributes["experimental_controls_enabled"]
    await lock.async_unlock()
    await lock.async_lock()
    bell = NinebotButton(entry, "SyntheticSN", "bell", "bell")
    assert bell.available
    await bell.async_press()
    assert app_client.async_control.await_args_list[0].args == ("SyntheticSN", "engine/start")
    assert app_client.async_control.await_args_list[1].args == ("SyntheticSN", "engine/stop")
    assert app_client.async_control.await_args_list[2].args == ("SyntheticSN", "bell")
    image = NinebotImage(entry, "SyntheticSN")
    assert image.image_url is None
    assert image.image_last_updated is not None


def test_multiple_batteries_need_stable_identity():
    from custom_components.ninebot.adapters import batteries
    from custom_components.ninebot.models import VehicleProfile, VehicleSnapshot
    from custom_components.ninebot.sensor import battery_descriptions

    identified = VehicleSnapshot(
        VehicleProfile("synthetic", "name", "model"),
        battery=batteries(
            {
                "battery_list": [
                    {
                        "sn": "one",
                        "bms_volt": 72,
                        "bat_temp": 25,
                        "have_bms_cycle_support": True,
                        "bms_cycle": 10,
                    },
                    {
                        "sn": "two",
                        "bms_volt": 73,
                        "bat_temp": 26,
                        "have_bms_cycle_support": False,
                        "bms_cycle": 100,
                    },
                ]
            }
        ),
    )
    descriptions = battery_descriptions(identified)
    assert len(descriptions) == 5
    values = [d.value(identified) for d in descriptions]
    assert values == [72, 25, 10, 73, 26]
    unknown = VehicleSnapshot(identified.profile, battery=batteries({"battery_list": [{}, {}]}))
    assert battery_descriptions(unknown) == []


async def test_legacy_unavailable_never_inherits_estimated_total(hass, entry, app_client):
    from custom_components.ninebot.sensor import LegacySensor

    assert await hass.config_entries.async_setup(entry.entry_id)
    sensor = LegacySensor(
        entry,
        "SyntheticSN",
        "battery_outflow_energy_total",
        "ninebot_syntheticsn_battery_outflow_energy_total",
    )
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["status"] == "deprecated"
