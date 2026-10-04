"""Remove only reviewed obsolete identities belonging to this entry's vehicle.

This runs after a successful first refresh, through HA's public registry API.
Transiently missing data and unrecognized identities never imply obsolescence.
"""

from collections.abc import Iterator

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .capabilities import CONTROL_BUTTONS
from .compat import device_entry_ids
from .const import CONF_CONTROL_VEHICLES, CONF_CONTROLS, DOMAIN
from .runtime import NinebotConfigEntry

OBSOLETE_KEYS = {
    "sensor": frozenset(
        [
            "battery_calculated",
            "battery_energy_delta",
            "battery_inflow_energy_daily",
            "battery_inflow_energy_monthly",
            "battery_inflow_energy_step",
            "battery_inflow_energy_total",
            "battery_inflow_power",
            "battery_nominal_energy",
            "battery_outflow_energy_daily",
            "battery_outflow_energy_monthly",
            "battery_outflow_energy_step",
            "battery_outflow_energy_total",
            "battery_outflow_power",
            "gsm_csq",
            "gsm_report_time",
            "gsm_report_timestamp",
            "gsm_rssi",
            "last_energy",
            "location",
            "month_energy",
        ]
    ),
    "lock": frozenset({"lock", "vehicle_lock_control"}),
    "number": frozenset({"battery_max_range"}),
}

# Confirmed current-state measurements and the public vehicle image are normal
# features. Raw diagnostics, GPS, events and hardware controls keep their own
# explicit opt-in policies.
VISIBLE_KEYS = {
    "sensor": frozenset(
        {
            "range_estimated",
            "range_ai",
            "last_mileage",
            "last_ride_duration",
            "last_ride_start",
            "last_ride_end",
            "last_ride_max_speed",
            "last_ride_average_speed",
        }
    ),
    "image": frozenset({"vehicle_image"}),
}


def _owned_entities(
    hass: HomeAssistant, entry: NinebotConfigEntry
) -> Iterator[tuple[er.RegistryEntry, str]]:
    devices = dr.async_get(hass)
    for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        if row.platform != DOMAIN or not row.device_id:
            continue
        device = devices.async_get(row.device_id)
        if device is None or device_entry_ids(device) != frozenset({entry.entry_id}):
            continue
        identifiers = [sn for domain, sn in device.identifiers if domain == DOMAIN]
        if len(identifiers) == 1:
            yield row, identifiers[0]


def _matches(row: er.RegistryEntry, sn: str, keys: dict[str, frozenset[str]]) -> bool:
    return any(
        row.unique_id in {f"{sn}_{key}", f"ninebot_{sn}_{key}".lower()}
        for key in keys.get(row.domain, ())
    )


@callback
def async_remove_obsolete_entities(hass: HomeAssistant, entry: NinebotConfigEntry) -> int:
    """Do not remove unrelated entries, shared devices or user-created sensors."""
    registry = er.async_get(hass)
    removed = 0
    for row, sn in _owned_entities(hass, entry):
        if _matches(row, sn, OBSOLETE_KEYS):
            registry.async_remove(row.entity_id)
            removed += 1
    return removed


@callback
def async_enable_standard_entities(hass: HomeAssistant, entry: NinebotConfigEntry) -> int:
    """Upgrade our disabled defaults while preserving every user disable choice."""
    registry = er.async_get(hass)
    enabled = 0
    for row, sn in _owned_entities(hass, entry):
        snapshot = entry.runtime_data.coordinator.data.get(sn)
        if (
            row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            and snapshot is not None
            and snapshot.present
            and _matches(row, sn, VISIBLE_KEYS)
        ):
            registry.async_update_entity(row.entity_id, disabled_by=None)
            enabled += 1
    return enabled


@callback
def async_enable_configured_controls(hass: HomeAssistant, entry: NinebotConfigEntry) -> int:
    """Entity registration is separate from the execution permission gate."""
    if entry.options.get(CONF_CONTROLS) is not True:
        return 0
    keys = {"button": frozenset(key for key, _ in CONTROL_BUTTONS)}
    registry = er.async_get(hass)
    enabled = 0
    for row, sn in _owned_entities(hass, entry):
        snapshot = entry.runtime_data.coordinator.data.get(sn)
        if (
            sn in entry.options.get(CONF_CONTROL_VEHICLES, [])
            and row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            and snapshot is not None
            and snapshot.present
            and _matches(row, sn, keys)
        ):
            registry.async_update_entity(row.entity_id, disabled_by=None)
            enabled += 1
    return enabled
