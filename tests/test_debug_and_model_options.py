"""Offline feature configuration, privacy, budgets and model identity."""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot import compat
from custom_components.ninebot.config_flow import parameter_validator
from custom_components.ninebot.debug_view import DEBUG_ATTRIBUTES, debug_view, numeric_observation
from custom_components.ninebot.estimation import EnergyModel
from custom_components.ninebot.exceptions import ErrorKind
from custom_components.ninebot.models import Battery, BatteryInfo, Freshness, VehicleProfile
from custom_components.ninebot.storage import ModelStorage

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_debug_opt_in_reuses_identity_and_never_adds_query_demand(hass, entry, app_client):
    hass.config_entries.async_update_entry(entry, options={"debug_mode": True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    row_id = er.async_get(hass).async_get_entity_id(
        "sensor", "ninebot", "SyntheticSN_raw_data_summary"
    )
    before = co.demand("SyntheticSN")
    methods = [
        app_client.async_get_status,
        app_client.async_get_battery,
        app_client.async_get_travel,
    ]
    counts = [method.await_count for method in methods]
    snapshot = co.data["SyntheticSN"]
    # Deliberately hostile text in approved paths must not escape into this view.
    co.data["SyntheticSN"] = replace(
        snapshot,
        profile=VehicleProfile(
            "SyntheticSN",
            "Scooter",
            "MODEL",
            "https://example.invalid?token=PRIVATE",
            {"businessType": "PRIVATE"},
        ),
        status=replace(
            snapshot.status,
            latitude=31.12345,
            longitude=120.98765,
            charge_remaining="PRIVATE PASSWORD",
            observations={"permissions": "PRIVATE"},
        ),
        battery=BatteryInfo(
            tuple(
                Battery(
                    f"PRIVATE-{i}", True, 79.6, 24, None, False, "PRIVATE", "PRIVATE", "PRIVATE"
                )
                for i in range(5)
            ),
            310,
        ),
    )
    co.async_update_listeners()
    await hass.async_block_till_done()
    state = hass.states.get(row_id)
    assert state.state == "ok"
    assert state.attributes["parsed"]["battery"]["charging_power_w"] == 310
    assert len(state.attributes["parsed"]["battery"]["packs"]) == 4
    encoded = json.dumps(dict(state.attributes), ensure_ascii=False, allow_nan=False)
    assert len(encoded.encode()) < 16384
    assert not any(
        value in encoded for value in ["PRIVATE", "31.12345", "120.98765", "SyntheticSN"]
    )
    assert counts == [method.await_count for method in methods]
    assert co.demand("SyntheticSN") == before
    assert DEBUG_ATTRIBUTES <= hass.data["sensor"].get_entity(row_id)._unrecorded_attributes
    hass.config_entries.async_update_entry(entry, options={"debug_mode": False})
    await hass.async_block_till_done()
    state = hass.states.get(row_id)
    assert state.state == "debug_disabled"
    assert "parsed" not in state.attributes
    assert er.async_get(hass).async_get(row_id).unique_id == "SyntheticSN_raw_data_summary"


async def test_options_and_number_share_storage_and_single_batch_generation(
    hass, entry, app_client
):
    hass.config_entries.async_update_entry(entry, options={"enable_estimation": True})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    model = entry.runtime_data.models.model("SyntheticSN")
    old_generation = model.generation
    form = await hass.config_entries.options.async_init(entry.entry_id)
    form = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            "poll_interval": 120,
            "enable_estimation": True,
            "debug_mode": True,
            "configure_model": True,
        },
    )
    assert form["step_id"] == "model_vehicle"
    form = await hass.config_entries.options.async_configure(
        form["flow_id"], {"model_vehicle": "SyntheticSN"}
    )
    assert form["step_id"] == "model_parameters"
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"voltage": 72, "capacity": 20}
    )
    assert result["type"].value == "create_entry"
    await hass.async_block_till_done()
    store = entry.runtime_data.models
    assert store.model("SyntheticSN").generation == old_generation + 1
    assert store.model("SyntheticSN").nominal == 1.44
    assert "voltage" not in entry.options and "capacity" not in entry.options
    assert "configure_model" not in entry.options
    registry = er.async_get(hass)
    number_id = registry.async_get_entity_id("number", "ninebot", "SyntheticSN_battery_capacity")
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number_id, "value": 30}, blocking=True
    )
    assert store.model("SyntheticSN").capacity == 30
    assert store.model("SyntheticSN").generation == old_generation + 2
    quality_id = registry.async_get_entity_id("sensor", "ninebot", "SyntheticSN_estimation_quality")
    assert registry.async_get(quality_id).disabled_by is None
    assert hass.states.get(quality_id).state == "baseline_reset"


async def test_model_transaction_rejects_partial_configuration(hass):
    store = ModelStorage(hass, "synthetic")
    store.schedule_save = MagicMock()
    old = store.model("car")
    old.configure("voltage", 72)
    with pytest.raises(ValueError):
        store.configure("car", {"voltage": 60, "capacity": -1})
    assert store.model("car") is old and old.voltage == 72
    store.schedule_save.assert_not_called()
    store.configure("car", {"voltage": 72})
    assert store.model("car").generation == old.generation


@pytest.mark.parametrize("value", [True, "NaN", {}, -1, 301])
def test_model_flow_parameter_bounds(value):
    with pytest.raises(vol.Invalid):
        parameter_validator(300)(value)


def test_recorder_capability_fallback_and_raw_value_safety():
    with patch.object(compat, "Entity", SimpleNamespace()):
        assert compat.unrecorded_attributes(DEBUG_ATTRIBUTES) == frozenset()
    with patch.object(compat, "Entity", SimpleNamespace(_unrecorded_attributes=frozenset())):
        assert compat.unrecorded_attributes(DEBUG_ATTRIBUTES) == DEBUG_ATTRIBUTES
    for value in ["private-token", "13400000000", "9" * 256, {}, 10**12]:
        assert numeric_observation(value) is None
    assert numeric_observation("100") == "100"
    assert numeric_observation(False) is False


async def test_debug_health_states_follow_group_freshness(hass, entry, app_client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    co = entry.runtime_data.coordinator
    snapshot = co.data["SyntheticSN"]
    with patch.object(co, "fresh", side_effect=lambda sn, group: group == "status"):
        assert debug_view(snapshot, co, EnergyModel())[0] == "partial"
    with patch.object(co, "fresh", return_value=False):
        assert debug_view(snapshot, co, EnergyModel())[0] == "stale"
        broken = replace(snapshot, status_freshness=Freshness(error=ErrorKind.CONNECTION))
        assert debug_view(broken, co, EnergyModel())[0] == "error"
