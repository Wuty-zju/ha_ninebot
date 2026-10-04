"""Remove only reviewed obsolete identities belonging to this entry's vehicle.

This runs after a successful first refresh, through HA's public registry API.
Transiently missing data and unrecognized identities never imply obsolescence.
"""

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .compat import device_entry_ids
from .const import DOMAIN
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


@callback
def async_remove_obsolete_entities(hass: HomeAssistant, entry: NinebotConfigEntry) -> int:
    """Do not remove unrelated entries, shared devices or user-created sensors."""
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    removed = 0
    for row in er.async_entries_for_config_entry(registry, entry.entry_id):
        if row.platform != DOMAIN or row.domain not in OBSOLETE_KEYS or not row.device_id:
            continue
        device = devices.async_get(row.device_id)
        if device is None or device_entry_ids(device) != frozenset({entry.entry_id}):
            continue
        identifiers = [sn for domain, sn in device.identifiers if domain == DOMAIN]
        if len(identifiers) != 1:
            continue
        sn = identifiers[0]
        if any(
            row.unique_id in {f"{sn}_{key}", f"ninebot_{sn}_{key}".lower()}
            for key in OBSOLETE_KEYS[row.domain]
        ):
            registry.async_remove(row.entity_id)
            removed += 1
    return removed
