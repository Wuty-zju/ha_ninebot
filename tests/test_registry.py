"""Obsolete identity removal is explicit, scoped and independent of UI names."""

from unittest.mock import patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.ninebot.registry import async_remove_obsolete_entities

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_cleanup_removes_only_exact_obsolete_rows_on_owned_vehicle(hass, entry):
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("ninebot", "SyntheticSN")}
    )
    registry = er.async_get(hass)

    def row(platform, integration, uid, device_id=device.id):
        return registry.async_get_or_create(
            platform, integration, uid, config_entry=entry, device_id=device_id
        )

    removed = [
        row("sensor", "ninebot", "ninebot_syntheticsn_gsm_csq"),
        row("sensor", "ninebot", "SyntheticSN_battery_outflow_energy_total"),
        row("number", "ninebot", "SyntheticSN_battery_max_range"),
        row("lock", "ninebot", "SyntheticSN_lock"),
        row("lock", "ninebot", "ninebot_syntheticsn_vehicle_lock_control"),
    ]
    registry.async_update_entity(removed[0].entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    retained = [
        row("sensor", "ninebot", "ninebot_syntheticsn_battery"),
        row("sensor", "ninebot", "SyntheticSN_month_energy_raw"),
        row("binary_sensor", "ninebot", "SyntheticSN_unlocked"),
        row("sensor", "ninebot", "unknown_gsm_csq"),
        row("sensor", "template", "SyntheticSN_gsm_csq"),
        row("sensor", "ninebot", "SyntheticSN_gsm_csq", None),
    ]
    registry.async_update_entity(retained[0].entity_id, name="My battery")
    assert async_remove_obsolete_entities(hass, entry) == 5
    assert all(registry.async_get(item.entity_id) is None for item in removed)
    assert all(registry.async_get(item.entity_id) is not None for item in retained)
    assert registry.async_get(retained[0].entity_id).name == "My battery"
    assert async_remove_obsolete_entities(hass, entry) == 0


async def test_cleanup_does_not_guess_shared_or_ambiguous_device_owner(hass, entry):
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("ninebot", "SyntheticSN"), ("ninebot", "AnotherSN")},
    )
    registry = er.async_get(hass)
    row = registry.async_get_or_create(
        "lock", "ninebot", "SyntheticSN_lock", config_entry=entry, device_id=device.id
    )
    assert async_remove_obsolete_entities(hass, entry) == 0
    devices.async_update_device(device.id, new_identifiers={("ninebot", "SyntheticSN")})
    with patch(
        "custom_components.ninebot.registry.device_entry_ids",
        return_value=frozenset({entry.entry_id, "other-account"}),
    ):
        assert async_remove_obsolete_entities(hass, entry) == 0
    assert registry.async_get(row.entity_id) is not None


async def test_failed_first_refresh_does_not_cleanup_and_reload_never_recreates_lock(
    hass, entry, app_client
):
    from custom_components.ninebot.exceptions import ErrorKind, NinebotError

    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("ninebot", "SyntheticSN")}
    )
    registry = er.async_get(hass)
    obsolete = registry.async_get_or_create(
        "lock", "ninebot", "SyntheticSN_lock", config_entry=entry, device_id=device.id
    )
    app_client.async_list_vehicles.side_effect = NinebotError(ErrorKind.CONNECTION)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert registry.async_get(obsolete.entity_id)
    app_client.async_list_vehicles.side_effect = None
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(obsolete.entity_id) is None
    assert not any(
        row.domain == "lock" for row in er.async_entries_for_config_entry(registry, entry.entry_id)
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert not any(
        row.domain == "lock" for row in er.async_entries_for_config_entry(registry, entry.entry_id)
    )
    app_client.async_control.assert_not_awaited()


async def test_visible_default_upgrade_preserves_user_choices_and_diagnostics(
    hass, entry, app_client
):
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("ninebot", "SyntheticSN")}
    )
    registry = er.async_get(hass)

    def row(domain, uid, disabled=er.RegistryEntryDisabler.INTEGRATION, owned=True):
        return registry.async_get_or_create(
            domain,
            "ninebot",
            uid,
            config_entry=entry,
            device_id=device.id if owned else None,
            disabled_by=disabled,
        )

    promoted = [
        row("sensor", "ninebot_syntheticsn_endurance"),
        row("sensor", "SyntheticSN_last_ride_end"),
        row("image", "SyntheticSN_vehicle_image"),
        row("sensor", "SyntheticSN_month_energy_raw"),
        row("device_tracker", "SyntheticSN_location"),
        row("event", "SyntheticSN_ride"),
        row("button", "SyntheticSN_bell"),
    ]
    registry.async_update_entity(promoted[0].entity_id, name="My range")
    retained = [
        row("sensor", "SyntheticSN_last_ride_start", er.RegistryEntryDisabler.USER),
        row("sensor", "unrecognized_range_estimated", owned=False),
    ]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.standard_entities_enabled == len(promoted)
    assert all(registry.async_get(item.entity_id).disabled_by is None for item in promoted)
    assert registry.async_get(promoted[0].entity_id).name == "My range"
    assert all(
        registry.async_get(item.entity_id).disabled_by == item.disabled_by for item in retained
    )
    # A user's later disable decision takes precedence on every future reload.
    registry.async_update_entity(promoted[0].entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.standard_entities_enabled == 0
    assert registry.async_get(promoted[0].entity_id).disabled_by is er.RegistryEntryDisabler.USER
    app_client.async_control.assert_not_awaited()
