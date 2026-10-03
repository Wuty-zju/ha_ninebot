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


@pytest.mark.parametrize("invalid", ["missing", "wrong-identity"])
async def test_setup_invalid_session_never_starts_queries(hass, entry, app_client, invalid):
    from unittest.mock import patch

    from homeassistant.exceptions import ConfigEntryAuthFailed

    from custom_components.ninebot import async_setup_entry
    from custom_components.ninebot.exceptions import ErrorKind, NinebotError

    with patch("custom_components.ninebot.session_uid") as read:
        if invalid == "missing":
            read.side_effect = NinebotError(ErrorKind.PROTOCOL)
        else:
            read.return_value = "different-business"
        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(hass, entry)
    app_client.async_list_vehicles.assert_not_awaited()


async def test_initial_refresh_failure_closes_runtime(hass, entry, app_client):
    from homeassistant.config_entries import ConfigEntryState

    from custom_components.ninebot.exceptions import ErrorKind, NinebotError

    app_client.async_list_vehicles.side_effect = NinebotError(ErrorKind.CONNECTION)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    app_client.async_close.assert_awaited_once()
    assert entry.runtime_data.coordinator._stopping
    assert not entry.runtime_data.coordinator._active


async def test_platform_unload_refusal_leaves_runtime_alive(hass, entry, app_client):
    from unittest.mock import patch

    from custom_components.ninebot import async_unload_entry

    assert await hass.config_entries.async_setup(entry.entry_id)
    app_client.async_close.reset_mock()
    with patch.object(hass.config_entries, "async_unload_platforms", return_value=False):
        assert await async_unload_entry(hass, entry) is False
    app_client.async_close.assert_not_awaited()
    assert not entry.runtime_data.coordinator._stopping


async def test_options_reload_reaps_old_runtime_and_reuses_session_manager(hass, entry, app_client):
    from custom_components.ninebot import manager_for

    assert manager_for(hass) is manager_for(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    old = entry.runtime_data
    hass.config_entries.async_update_entry(entry, options={"poll_interval": 240})
    await hass.async_block_till_done()
    assert old.coordinator._stopping
    assert old.coordinator._shutdown_task.done()
    assert entry.runtime_data is not old
    assert entry.runtime_data.coordinator.interval == 240
    assert entry.runtime_data.session is old.session


async def test_duplicate_sensor_identities_preserve_values_and_repair(hass, entry, app_client):
    from homeassistant.helpers import issue_registry as ir

    from custom_components.ninebot.entity import NinebotEntity

    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("ninebot", "SyntheticSN")}
    )
    registry = er.async_get(hass)
    a = registry.async_get_or_create(
        "sensor", "ninebot", "ninebot_syntheticsn_battery", config_entry=entry, device_id=device.id
    )
    b = registry.async_get_or_create(
        "sensor", "ninebot", "SyntheticSN_battery", config_entry=entry, device_id=device.id
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get(a.entity_id).state) == 80
    assert float(hass.states.get(b.entity_id).state) == 80
    conflict = next(iter(entry.runtime_data.identity_conflicts))
    assert ("ninebot", conflict) in ir.async_get(hass).issues
    assert "SyntheticSN" not in conflict
    registry.async_remove(b.entity_id)
    entity = NinebotEntity(entry, "SyntheticSN", "battery", "sensor", "status")
    assert entity.unique_id == a.unique_id
    assert not entry.runtime_data.identity_conflicts
    assert ("ninebot", conflict) not in ir.async_get(hass).issues


async def test_unknown_binary_state_never_becomes_unlocked(hass, entry, app_client):
    from custom_components.ninebot.binary_sensor import DESCRIPTIONS, NinebotBinarySensor

    app_client.async_get_status.return_value = {"loc": {"lock": "invalid"}}
    assert await hass.config_entries.async_setup(entry.entry_id)
    entity = NinebotBinarySensor(entry, "SyntheticSN", DESCRIPTIONS[2])
    assert entity.is_on is None


async def test_restart_preserves_model_totals_but_does_not_bridge_disabled_interval(
    hass, entry, app_client
):
    from datetime import UTC, datetime, timedelta

    hass.config_entries.async_update_entry(entry, options={"enable_estimation": True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    store = entry.runtime_data.models
    model = store.model("SyntheticSN")
    model.configure("voltage", 72)
    model.configure("capacity", 20)
    now = datetime.now(UTC)
    model.sample(80, now - timedelta(seconds=120), "vehicle_soc:unidentified")
    model.sample(79, now - timedelta(seconds=60), "vehicle_soc:unidentified")
    generation = model.generation
    before = model.values["out_total"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    app_client.async_get_status.return_value = {"dump_energy": 60}
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    restored = entry.runtime_data.models.model("SyntheticSN")
    assert restored.generation == generation
    assert restored.values["out_total"] == before
    assert restored.baseline_soc == 60
    assert restored.quality == "baseline_only"


async def test_legacy_lock_code_keeps_original_semantics(hass, entry, app_client):
    from custom_components.ninebot.sensor import SENSORS, NinebotSensor

    assert await hass.config_entries.async_setup(entry.entry_id)
    description = next(item for item in SENSORS if item.key == "vehicle_lock_raw")
    entity = NinebotSensor(entry, "SyntheticSN", description)
    assert entity.native_value == 0
    app_client.async_get_status.return_value = {"loc": {"lock": 0}}
    await entry.runtime_data.coordinator.async_refresh_vehicle("SyntheticSN")
    assert entity.native_value == 1


async def test_unmatched_device_keeps_identity_and_repairs_clear_after_confirmed_discovery(
    hass, entry, app_client
):
    from homeassistant.helpers import issue_registry as ir

    from custom_components.ninebot.entity import async_audit_device_identities

    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("ninebot", "DifferentLegacySN")},
        name="Scooter",
    )
    old = er.async_get(hass).async_get_or_create(
        "sensor",
        "ninebot",
        "ninebot_differentlegacysn_battery",
        config_entry=entry,
        device_id=device.id,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    issue_id = next(
        key for key in entry.runtime_data.identity_conflicts if key.startswith("legacy_device_")
    )
    assert ("ninebot", issue_id) in ir.async_get(hass).issues
    assert "DifferentLegacySN" not in issue_id
    assert er.async_get(hass).async_get(old.entity_id).unique_id == old.unique_id
    assert hass.states.get(old.entity_id).state == "unavailable"
    assert er.async_get(hass).async_get_entity_id("sensor", "ninebot", "SyntheticSN_battery")
    # Persistent repairs must clear even if a new runtime has lost its in-memory set.
    entry.runtime_data.identity_conflicts.clear()
    app_client.async_list_vehicles.return_value.append({"wnumber": "DifferentLegacySN"})
    co = entry.runtime_data.coordinator
    co._next_attempt[("", "profile")] = 0
    await co.async_refresh()
    await hass.async_block_till_done()
    async_audit_device_identities(hass, entry)
    assert ("ninebot", issue_id) not in ir.async_get(hass).issues
    assert float(hass.states.get(old.entity_id).state) == 80


async def test_legacy_cycle_entity_recovers_supported_values_without_duplicate(
    hass, entry, app_client
):
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("ninebot", "SyntheticSN")}
    )
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor", "ninebot", "SyntheticSN_bms_cycles", config_entry=entry, device_id=device.id
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(old.entity_id).state == "unknown"
    app_client.async_get_battery.return_value = {
        "battery_list": [{"bms_cycle": 12, "have_bms_cycle_support": True}]
    }
    co = entry.runtime_data.coordinator
    co._next_attempt[("SyntheticSN", "battery")] = 0
    await co.async_refresh()
    await hass.async_block_till_done()
    assert float(hass.states.get(old.entity_id).state) == 12
    assert (
        registry.async_get_entity_id("sensor", "ninebot", "SyntheticSN_bms_cycles") == old.entity_id
    )
    app_client.async_get_battery.return_value = {"battery_list": [{}, {}]}
    co._next_attempt[("SyntheticSN", "battery")] = 0
    await co.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(old.entity_id).state == "unknown"
