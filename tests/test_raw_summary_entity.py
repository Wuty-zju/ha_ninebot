"""Optional raw debug presentation never writes values or adds query demand."""

import json

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.ninebot.raw import Endpoint, build_record

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_raw_summary_default_and_enabled_state_are_small_private_and_local(
    hass, entry, app_client
):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", "ninebot", "SyntheticSN_raw_data_summary")
    assert registry.async_get(entity_id).disabled_by is None
    assert hass.states.get(entity_id) is not None
    coordinator = entry.runtime_data.coordinator
    before = coordinator.demand("SyntheticSN")
    counts = [
        method.await_count
        for method in (
            app_client.async_list_vehicles,
            app_client.async_get_status,
            app_client.async_get_battery,
            app_client.async_get_travel,
        )
    ]
    coordinator.raw.put(
        build_record(
            Endpoint.STATUS,
            {"dump_energy": 73, "private-identity-key": "private-value", "loc": {"lat": 31.123}},
            dt_util.utcnow(),
        ),
        "SyntheticSN",
    )
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state.isdecimal()
    assert state.attributes["unknown_field_count"] > 0
    # Icon translations are frontend metadata; HA need not put them in state.
    assert set(state.attributes) - {"icon"} == {
        "schema_path_count",
        "unknown_field_count",
        "redacted_field_count",
        "retained_bytes",
        "friendly_name",
    }
    encoded = json.dumps(state.attributes)
    assert not any(value in encoded for value in ("private", "31.123", "dump_energy", "loc"))
    assert coordinator.demand("SyntheticSN") == before
    assert counts == [
        method.await_count
        for method in (
            app_client.async_list_vehicles,
            app_client.async_get_status,
            app_client.async_get_battery,
            app_client.async_get_travel,
        )
    ]
    assert await hass.config_entries.async_unload(entry.entry_id)
