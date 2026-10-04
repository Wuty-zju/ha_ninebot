import json
from unittest.mock import patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot.diagnostics import async_get_config_entry_diagnostics

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_manual_auth_failure_updates_entities_and_starts_ui_reauth(hass, entry, app_client):
    from homeassistant.exceptions import ConfigEntryAuthFailed

    from custom_components.ninebot.exceptions import NinebotAuthError

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    battery = registry.async_get_entity_id("sensor", "ninebot", "SyntheticSN_battery")
    assert hass.states.get(battery).state == "80.0"
    app_client.async_get_status.side_effect = NinebotAuthError()
    with patch.object(entry, "async_start_reauth") as reauth:
        with pytest.raises(ConfigEntryAuthFailed):
            await entry.runtime_data.coordinator.async_refresh_vehicle("SyntheticSN")
        reauth.assert_called_once_with(hass)
    assert hass.states.get(battery).state == "unavailable"


async def test_local_expiry_notifies_ha_without_cloud_poll(hass, entry, app_client, freezer):
    from datetime import UTC, datetime, timedelta

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    now = datetime(2026, 10, 3, 12, tzinfo=UTC)
    freezer.move_to(now)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    co._async_unsub_refresh()
    registry = er.async_get(hass)
    battery = registry.async_get_entity_id("sensor", "ninebot", "SyntheticSN_battery")
    app_client.async_get_status.reset_mock()
    later = now + timedelta(seconds=361)
    freezer.move_to(later)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done()
    assert hass.states.get(battery).state == "unavailable"
    app_client.async_get_status.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert co._validity_cancel is None


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


async def test_preserve_valid_legacy_identity_and_remove_obsolete_identity(hass, entry, app_client):
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
    assert registry.async_get(deprecated.entity_id) is None
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
    from dataclasses import replace

    from homeassistant.exceptions import HomeAssistantError

    from custom_components.ninebot.capabilities import (
        CapabilityState,
        ControlCapability,
        VehicleCapabilities,
    )

    bell = NinebotButton(entry, "SyntheticSN", "bell", "bell")
    assert not bell.available
    with pytest.raises(HomeAssistantError):
        await bell.async_press()
    app_client.async_control.assert_not_awaited()
    co = entry.runtime_data.coordinator
    snapshot = co.data["SyntheticSN"]
    co.data["SyntheticSN"] = replace(
        snapshot,
        status=replace(
            snapshot.status,
            capabilities=VehicleCapabilities(
                (
                    ControlCapability(
                        "bell",
                        CapabilityState.ALLOWED,
                        CapabilityState.ALLOWED,
                        True,
                        "mock-contract",
                    ),
                )
            ),
        ),
    )
    assert bell.available
    await bell.async_press()
    app_client.async_control.assert_awaited_once_with("SyntheticSN", "bell")
    image = NinebotImage(entry, "SyntheticSN")
    assert image.image_url is None
    assert image.image_last_updated is None
    assert not image.available


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


async def test_options_preserve_selected_missing_and_removed_vehicle_choices(
    hass, entry, app_client
):
    from dataclasses import replace

    hass.config_entries.async_update_entry(
        entry, options={"control_vehicles": ["SyntheticSN", "MissingSelection"]}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    co = entry.runtime_data.coordinator
    co.data["SyntheticSN"] = replace(co.data["SyntheticSN"], present=False)
    form = await hass.config_entries.options.async_init(entry.entry_id)
    selector = next(
        value for key, value in form["data_schema"].schema.items() if str(key) == "control_vehicles"
    )
    assert selector.config["options"] == [
        {"value": "SyntheticSN", "label": "Scooter"},
        {"value": "MissingSelection", "label": "MissingSelection"},
    ]
    assert not co.controls_enabled("SyntheticSN")


async def test_battery_transitions_preserve_registry_history_and_device_assignment(
    hass, entry, app_client
):
    import hashlib
    from dataclasses import replace

    from custom_components.ninebot.adapters import batteries

    def payload(*pairs):
        return {
            "battery_list": [
                {"sn": identity, "bms_volt": voltage, "bat_temp": 25} for identity, voltage in pairs
            ]
        }

    app_client.async_get_battery.return_value = payload(("pack-a", 72))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    registry = er.async_get(hass)
    primary_id = registry.async_get_entity_id("sensor", "ninebot", "SyntheticSN_bms_voltage")
    original = registry.async_get(primary_id)
    registry.async_update_entity(primary_id, name="My voltage")

    async def update(*pairs):
        co.async_set_updated_data(
            {"SyntheticSN": replace(co.data["SyntheticSN"], battery=batteries(payload(*pairs)))}
        )
        await hass.async_block_till_done()

    await update(("pack-a", 72), ("pack-b", 73))
    assert hass.states.get(primary_id).state == "unknown"
    pack_ids = [
        registry.async_get_entity_id(
            "sensor",
            "ninebot",
            f"SyntheticSN_battery_{hashlib.sha256(identity.encode()).hexdigest()[:12]}_bms_voltage",
        )
        for identity in ("pack-a", "pack-b")
    ]
    assert [float(hass.states.get(eid).state) for eid in pack_ids] == [72, 73]
    assert all(registry.async_get(eid).device_id == original.device_id for eid in pack_ids)
    await update(("pack-b", 74), ("pack-a", 75))
    assert [float(hass.states.get(eid).state) for eid in pack_ids] == [75, 74]
    await update(("pack-b", 76))
    assert float(hass.states.get(primary_id).state) == 76
    assert hass.states.get(pack_ids[0]).state == "unknown"
    assert float(hass.states.get(pack_ids[1]).state) == 76
    assert registry.async_get(primary_id).name == "My voltage"
    assert registry.async_get(primary_id).unique_id == original.unique_id
    assert await hass.config_entries.async_unload(entry.entry_id)
    app_client.async_get_battery.return_value = payload(("pack-b", 76), ("pack-a", 75))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(primary_id).state == "unknown"
    assert registry.async_get(primary_id).name == "My voltage"
    assert [float(hass.states.get(eid).state) for eid in pack_ids] == [75, 76]
    rows = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert len({row.unique_id for row in rows}) == len(rows)
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_image_native_cache_tracks_url_changes_not_profile_poll(
    hass, entry, app_client, freezer
):
    from dataclasses import replace
    from datetime import timedelta
    from unittest.mock import AsyncMock

    from homeassistant.components.image import Image, async_get_image
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.ninebot.image import NinebotImage

    registry = er.async_get(hass)
    row = registry.async_get_or_create(
        "image", "ninebot", "SyntheticSN_vehicle_image", config_entry=entry
    )
    app_client.async_list_vehicles.return_value[0]["img_url"] = (
        "https://oms-oss-public.ninebot.com/synthetic-one.png"
    )
    fetch = AsyncMock(
        side_effect=[
            Image(content=b"first", content_type="image/png"),
            Image(content=b"second", content_type="image/png"),
        ]
    )
    with patch.object(NinebotImage, "_async_load_image_from_url", fetch):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        co = entry.runtime_data.coordinator
        image_id = row.entity_id
        first_time = hass.states.get(image_id).state
        assert (await async_get_image(hass, image_id)).content == b"first"
        co._list_freshness = replace(
            co._list_freshness, succeeded_at=co._list_freshness.succeeded_at + timedelta(seconds=60)
        )
        freezer.move_to(co._list_freshness.succeeded_at)
        co.async_set_updated_data(dict(co.data))
        await hass.async_block_till_done()
        assert hass.states.get(image_id).state == first_time
        assert (await async_get_image(hass, image_id)).content == b"first"
        assert fetch.await_count == 1
        old = co.data["SyntheticSN"]
        co.async_set_updated_data(
            {
                "SyntheticSN": replace(
                    old,
                    profile=replace(
                        old.profile,
                        image_url="https://oms-oss-public.ninebot.com/synthetic-two.png",
                    ),
                )
            }
        )
        await hass.async_block_till_done()
        assert hass.states.get(image_id).state != first_time
        assert (await async_get_image(hass, image_id)).content == b"second"
        assert fetch.await_count == 2
        co.async_set_updated_data(
            {"SyntheticSN": replace(old, profile=replace(old.profile, image_url=None))}
        )
        await hass.async_block_till_done()
        assert hass.states.get(image_id).state == "unavailable"
        with pytest.raises(HomeAssistantError):
            await async_get_image(hass, image_id)
        assert fetch.await_count == 2
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_disabled_battery_and_travel_entities_stop_regular_polling_then_reenable(
    hass, entry, app_client
):
    registry = er.async_get(hass)
    rows = [
        registry.async_get_or_create(
            "sensor",
            "ninebot",
            f"SyntheticSN_{key}",
            config_entry=entry,
            disabled_by=er.RegistryEntryDisabler.USER,
        )
        for key in (
            "bms_voltage",
            "batt_temp",
            "month_mileage",
            "last_mileage",
            "last_ride_duration",
            "last_ride_start",
            "last_ride_end",
            "last_ride_max_speed",
            "last_ride_average_speed",
        )
    ]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    assert co.demand("SyntheticSN").groups == {"status"}
    for group in ("status", "battery", "travel"):
        co._next_attempt[("SyntheticSN", group)] = 0
    app_client.async_get_battery.reset_mock()
    app_client.async_get_travel.reset_mock()
    await co.async_refresh()
    await hass.async_block_till_done()
    app_client.async_get_battery.assert_not_awaited()
    app_client.async_get_travel.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)
    registry.async_update_entity(rows[0].entity_id, disabled_by=None)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.coordinator.demand("SyntheticSN").groups == {"status", "battery"}
    assert float(hass.states.get(rows[0].entity_id).state) == 75.3
    assert registry.async_get(rows[1].entity_id).disabled_by is er.RegistryEntryDisabler.USER
    assert registry.async_get(rows[2].entity_id).disabled_by is er.RegistryEntryDisabler.USER
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_enabled_native_ride_event_keeps_travel_dependency(hass, entry, app_client):
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        "ninebot",
        "SyntheticSN_month_mileage",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    event = registry.async_get_or_create("event", "ninebot", "SyntheticSN_ride", config_entry=entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(event.entity_id) is not None
    co = entry.runtime_data.coordinator
    assert "travel" in co.demand("SyntheticSN").groups
    assert co.demand("SyntheticSN").last_ride
    co._next_attempt[("SyntheticSN", "travel")] = 0
    app_client.async_get_travel.reset_mock()
    await co.async_refresh()
    await hass.async_block_till_done()
    app_client.async_get_travel.assert_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_native_gps_tracker_uses_zones_and_removes_coordinates_on_opt_out(
    hass, entry, app_client
):
    from dataclasses import replace

    from custom_components.ninebot.adapters import status

    registry = er.async_get(hass)
    row = registry.async_get_or_create(
        "device_tracker", "ninebot", "SyntheticSN_location", config_entry=entry
    )
    hass.config_entries.async_update_entry(entry, options={"enable_coordinates": True})
    app_client.async_get_status.return_value["loc"].update(
        {"lat": hass.config.latitude, "lon": hass.config.longitude}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(row.entity_id)
    assert state.state == "home"
    assert state.attributes["source_type"] == "gps"
    assert state.attributes["latitude"] == hass.config.latitude
    assert state.attributes["longitude"] == hass.config.longitude
    co = entry.runtime_data.coordinator
    original = co.data["SyntheticSN"]
    co.async_set_updated_data(
        {"SyntheticSN": replace(original, status=status({"loc": {"lat": 12.3456, "lon": 45.6789}}))}
    )
    await hass.async_block_till_done()
    state = hass.states.get(row.entity_id)
    assert state.state == "not_home"
    assert state.attributes["latitude"] == 12.3456 and state.attributes["longitude"] == 45.6789
    co.async_set_updated_data(
        {"SyntheticSN": replace(original, status=status({"loc": {"lat": 91, "lon": 45.6789}}))}
    )
    await hass.async_block_till_done()
    assert hass.states.get(row.entity_id).state == "unavailable"
    # Actual options reload, not a monkey-patched tracker property.
    hass.config_entries.async_update_entry(entry, options={"enable_coordinates": False})
    await hass.async_block_till_done()
    state = hass.states.get(row.entity_id)
    assert state.state == "unavailable"
    assert "latitude" not in state.attributes and "longitude" not in state.attributes
    assert registry.async_get(row.entity_id).unique_id == row.unique_id
    assert await hass.config_entries.async_unload(entry.entry_id)
