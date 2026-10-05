"""Real HA flow validation and upgrade boundaries, entirely offline."""

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot.battery_parameters import BatteryParameters
from custom_components.ninebot.entity import async_setup_dynamic
from custom_components.ninebot.registry import async_remove_obsolete_entities
from custom_components.ninebot.sensor import RatedEnergySensor, range_source, remaining_range
from custom_components.ninebot.storage import ModelStorage

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def open_parameters(hass, entry):
    form = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        form["flow_id"], {"poll_interval": 120, "enable_estimation": True, "configure_model": True}
    )


async def test_device_selector_accepts_device_id_and_rejects_labels_without_native_option_error(
    hass, entry, app_client
):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device(identifiers={("ninebot", "SyntheticSN")})
    form = await open_parameters(hass, entry)
    field = next(iter(form["data_schema"].schema.values()))
    assert field.selector_type == "device"
    assert form["data_schema"]({"model_vehicle": device.id})["model_vehicle"] == device.id
    form = await hass.config_entries.options.async_configure(
        form["flow_id"], {"model_vehicle": "Scooter"}
    )
    assert form["errors"] == {"model_vehicle": "model_unavailable"}
    foreign = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("other", "SyntheticSN")}
    )
    form = await hass.config_entries.options.async_configure(
        form["flow_id"], {"model_vehicle": foreign.id}
    )
    assert form["errors"] == {"model_vehicle": "model_unavailable"}
    form = await hass.config_entries.options.async_configure(
        form["flow_id"], {"model_vehicle": device.id}
    )
    assert form["step_id"] == "model_parameters"
    old_uid = RatedEnergySensor(entry, "SyntheticSN").unique_id
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"voltage": 72, "capacity": 20}
    )
    await hass.async_block_till_done()
    assert result["type"].value == "create_entry"
    sensor = RatedEnergySensor(entry, "SyntheticSN")
    assert sensor.unique_id == old_uid and sensor.native_value == 1.44
    entry.runtime_data.models.configure("SyntheticSN", {"capacity": 30})
    assert sensor.unique_id == old_uid and sensor.native_value == 2.16


async def test_parameter_store_ignores_legacy_counters_and_noop_does_not_save(hass):
    store = ModelStorage(hass, "synthetic")
    store._store.async_load = AsyncMock(
        return_value={
            "model_version": 2,
            "models": {
                "car": {
                    "voltage": 72,
                    "capacity": 20,
                    "generation": 123,
                    "values": {"out_total": 999},
                    "baseline_soc": 99,
                    "max_range": 500,
                }
            },
        }
    )
    await store.async_load()
    assert store.model("car").dump() == {"voltage": 72, "capacity": 20}
    assert store._data()["model_version"] == 3
    store.schedule_save = MagicMock()
    store.configure("car", {"voltage": 72})
    store.schedule_save.assert_not_called()
    for values in ({"voltage": 60, "capacity": -1}, {"max_range": 100}, {"capacity": True}):
        with pytest.raises(ValueError):
            store.configure("car", values)
        assert store.model("car") == BatteryParameters(72, 20)


async def test_exact_generation_cleanup_does_not_delete_lookalikes_or_shared_devices(hass, entry):
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("ninebot", "SyntheticSN")}
    )
    registry = er.async_get(hass)

    def row(uid):
        return registry.async_get_or_create(
            "sensor", "ninebot", uid, config_entry=entry, device_id=device.id
        )

    removed = [
        row(uid)
        for uid in (
            "SyntheticSN_estimated_out_total_v2_g0",
            "ninebot_syntheticsn_estimated_in_daily_v2_g100",
            "SyntheticSN_estimated_nominal_v2_g2",
            "SyntheticSN_range_ai",
            "SyntheticSN_cycle_raw",
        )
    ]
    retained = [
        row(uid)
        for uid in (
            "SyntheticSN_estimated_unrecognized_v2_g1",
            "OtherSN_estimated_out_total_v2_g1",
            "SyntheticSN_estimated_out_total_v2_g1_extra",
            "SyntheticSN_battery_rated_energy",
        )
    ]
    with patch(
        "custom_components.ninebot.registry.device_entry_ids",
        return_value=frozenset({entry.entry_id, "other"}),
    ):
        assert async_remove_obsolete_entities(hass, entry) == 0
    assert async_remove_obsolete_entities(hass, entry) == len(removed)
    assert all(registry.async_get(v.entity_id) is None for v in removed)
    assert all(registry.async_get(v.entity_id) for v in retained)


async def test_topology_cache_skips_reconstruction_and_still_discovers_new_packs(
    hass, entry, app_client
):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    factory = MagicMock(return_value=[])
    async_setup_dynamic(
        hass, entry, MagicMock(), factory, signature=lambda s: len(s.battery.batteries)
    )
    assert factory.call_count == 1
    co.async_update_listeners()
    assert factory.call_count == 1
    old = co.data["SyntheticSN"]
    co.data["SyntheticSN"] = replace(old, battery=replace(old.battery, batteries=()))
    co.async_update_listeners()
    assert factory.call_count == 2


async def test_remaining_range_source_is_explicit_and_zero_does_not_fall_back(
    hass, entry, app_client
):
    assert await hass.config_entries.async_setup(entry.entry_id)
    s = entry.runtime_data.coordinator.data["SyntheticSN"]
    for precise, estimated, ai, value, source in (
        (0, 40, 50, 0, "range_precise"),
        (None, 40, 50, 40, "range_estimated"),
        (None, None, 50, 50, "range_ai"),
        (None, None, None, None, None),
    ):
        s = replace(
            s,
            status=replace(s.status, range_precise=precise, range_estimated=estimated, range_ai=ai),
        )
        assert remaining_range(s) == value and range_source(s) == source
