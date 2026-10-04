"""Normal command buttons and bounded translated local-policy diagnostics."""

from dataclasses import replace

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot.capabilities import (
    CONTROL_BUTTONS,
    CONTROL_STATES,
    CapabilityState,
    ControlCapability,
    VehicleCapabilities,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.mark.parametrize("key,action", CONTROL_BUTTONS)
async def test_direct_command_button_and_policy_status(hass, entry, app_client, key, action):
    hass.config_entries.async_update_entry(
        entry, options={"enable_controls": True, "control_vehicles": ["SyntheticSN"]}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    button = registry.async_get(
        registry.async_get_entity_id("button", "ninebot", f"SyntheticSN_{key}")
    )
    diagnostic = registry.async_get_entity_id(
        "sensor", "ninebot", "SyntheticSN_control_availability"
    )
    assert button.disabled_by is None
    assert hass.states.get(button.entity_id).state == "unavailable"
    assert hass.states.get(diagnostic).state == "unverified"
    assert not any(
        row.domain == "lock" for row in er.async_entries_for_config_entry(registry, entry.entry_id)
    )
    app_client.async_control.assert_not_awaited()

    # This is synthetic capability evidence, not a real permission parser or
    # proof that any physical control has executed.
    coordinator = entry.runtime_data.coordinator
    snapshot = coordinator.data["SyntheticSN"]
    record = ControlCapability(
        action, CapabilityState.ALLOWED, CapabilityState.ALLOWED, True, "mock-reviewed"
    )
    coordinator.async_set_updated_data(
        {
            "SyntheticSN": replace(
                snapshot,
                status=replace(snapshot.status, capabilities=VehicleCapabilities((record,))),
            )
        }
    )
    await hass.async_block_till_done()
    assert hass.states.get(diagnostic).state == "ready"
    assert hass.states.get(diagnostic).attributes[key] == "ready"
    assert (
        len(
            {
                k: v
                for k, v in hass.states.get(diagnostic).attributes.items()
                if k in dict(CONTROL_BUTTONS)
            }
        )
        == 4
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": button.entity_id}, blocking=True
    )
    app_client.async_control.assert_awaited_once_with("SyntheticSN", action)
    app_client.async_get_status.assert_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_configured_default_upgrade_preserves_user_disable_and_reports_local_gate(
    hass, entry, app_client
):
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("ninebot", "SyntheticSN")}
    )
    registry = er.async_get(hass)
    rows = [
        registry.async_get_or_create(
            "button",
            "ninebot",
            f"SyntheticSN_{key}",
            config_entry=entry,
            device_id=device.id,
            disabled_by=er.RegistryEntryDisabler.USER
            if key == "bucket"
            else er.RegistryEntryDisabler.INTEGRATION,
        )
        for key, _ in CONTROL_BUTTONS
    ]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    diagnostic = registry.async_get_entity_id(
        "sensor", "ninebot", "SyntheticSN_control_availability"
    )
    assert hass.states.get(diagnostic).state == "disabled"
    assert all(registry.async_get(row.entity_id).disabled_by == row.disabled_by for row in rows)
    hass.config_entries.async_update_entry(
        entry, options={"enable_controls": True, "control_vehicles": ["SyntheticSN"]}
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.configured_controls_enabled == 3
    assert registry.async_get(rows[1].entity_id).disabled_by is er.RegistryEntryDisabler.USER
    assert all(registry.async_get(rows[i].entity_id).disabled_by is None for i in (0, 2, 3))
    assert hass.states.get(diagnostic).state == "unverified"
    coordinator = entry.runtime_data.coordinator
    coordinator._authenticated = False
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(diagnostic).state == "authentication_required"
    assert hass.states.get(diagnostic).attributes["options"] == list(CONTROL_STATES)
    app_client.async_control.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)
