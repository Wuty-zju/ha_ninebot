"""Remove only reviewed obsolete identities belonging to this entry's vehicle.

This runs after a successful first refresh, through HA's public registry API.
Transiently missing data and unrecognized identities never imply obsolescence.
"""

import re
from collections.abc import Iterator
from typing import Any

from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .capabilities import CONTROL_BUTTONS
from .compat import device_entry_ids
from .const import CONF_CONTROL_VEHICLES, CONF_CONTROLS, CONF_DEBUG, CONF_ESTIMATION, DOMAIN
from .debug_entities import DEBUG_SENSOR_KEYS
from .identity import (
    device_sn,
    legacy_uids,
    scoped_uid,
)
from .observations import ENTITY_FIELDS, RAW_FIELDS
from .runtime import NinebotConfigEntry
from .sensor import DAY_FIELDS, SENSORS, battery_descriptions

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
            "device_name",
            "sn",
            "vehicle_lock_raw",
            "month_returned_rides",
            "last_battery_used_raw",
            "cycle_raw",
            "pack_electricity_raw",
            "estimation_quality",
            "history_scanned_months",
            "history_indexed_rides",
            "history_mileage",
            "history_energy",
            "history_duration",
            *(field.key for field in RAW_FIELDS if field not in ENTITY_FIELDS),
        ]
    ),
    "button": frozenset({"history_resume"}),
    "lock": frozenset({"lock", "vehicle_lock_control"}),
    "number": frozenset({"battery_max_range"}),
}


def visible_keys(entry: NinebotConfigEntry, sn: str) -> dict[str, frozenset[str]]:
    """Only actual current factories/legacy aliases, not arbitrary UID suffixes.

    Visibility is separate from the controls/coordinates execution opt-ins.
    Neither revealing a button nor enabling a tracker grants access to data.
    """
    snapshot = entry.runtime_data.coordinator.data[sn]
    sensors = {
        key
        for description in (*SENSORS, *battery_descriptions(snapshot))
        for key in (description.key, *description.aliases)
    } | {"control_availability", "raw_data_summary", "bms_voltage", "batt_temp", "bms_cycles"}
    sensors.update(DAY_FIELDS)
    if entry.options.get(CONF_DEBUG) is True:
        sensors.update(DEBUG_SENSOR_KEYS)
    else:
        sensors.difference_update(DEBUG_SENSOR_KEYS)
    numbers: set[str] = set()
    if entry.options.get(CONF_ESTIMATION):
        sensors.add("battery_rated_energy")
        numbers.update(("main_battery_voltage", "battery_capacity"))
    return {
        "sensor": frozenset(sensors),
        "number": frozenset(numbers),
        "binary_sensor": frozenset(
            {
                "charging",
                "power",
                "main_power",
                "cycle_support",
                "battery_find_my_support",
            }
        ),
        "image": frozenset({"vehicle_image"}),
        "device_tracker": frozenset({"location"}),
        "event": frozenset({"ride"}),
        "calendar": frozenset({"ride_calendar"}),
        "button": frozenset({"refresh", "info", *(key for key, _ in CONTROL_BUTTONS)}),
        "lock": frozenset({"vehicle_lock", "seat_lock"}),
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
        if (sn := device_sn(entry, device)) is not None:
            yield row, sn


def _matches(
    row: er.RegistryEntry, sn: str, keys: dict[str, frozenset[str]], entry: NinebotConfigEntry
) -> bool:
    return any(
        row.unique_id in {*legacy_uids(sn, key), scoped_uid(entry, sn, key)}
        for key in keys.get(row.domain, ())
    )


def _obsolete_generated(row: er.RegistryEntry, sn: str) -> bool:
    """Full identities from our retired factories, not arbitrary prefix deletion."""
    if row.domain != "sensor":
        return False
    suffix = (
        r"(?:estimated_(?:nominal|delta|(?:in|out)_(?:step|daily|monthly|total))_v2_g[0-9]{1,7}"
        r"|battery_[0-9a-f]{12}_(?:cycle_raw|pack_electricity_raw))"
    )
    return any(
        re.fullmatch(re.escape(prefix) + suffix, row.unique_id) is not None
        for prefix in (f"{sn}_", f"ninebot_{sn}_".lower())
    )


@callback
def async_remove_obsolete_entities(hass: HomeAssistant, entry: NinebotConfigEntry) -> int:
    """Do not remove unrelated entries, shared devices or user-created sensors."""
    registry = er.async_get(hass)
    removed = 0
    for row, sn in _owned_entities(hass, entry):
        if _matches(row, sn, OBSOLETE_KEYS, entry) or _obsolete_generated(row, sn):
            registry.async_remove(row.entity_id)
            removed += 1
    return removed


@callback
def async_configure_debug_entities(hass: HomeAssistant, entry: NinebotConfigEntry) -> None:
    """Gate reviewed debug identities without erasing user registry preferences."""
    enabled = entry.options.get(CONF_DEBUG) is True
    registry = er.async_get(hass)
    keys = {"sensor": DEBUG_SENSOR_KEYS}
    for row, sn in _owned_entities(hass, entry):
        if not _matches(row, sn, keys, entry):
            continue
        disabled, hidden = row.disabled_by, row.hidden_by
        if enabled:
            if disabled is er.RegistryEntryDisabler.INTEGRATION:
                disabled = None
            if hidden is er.RegistryEntryHider.INTEGRATION:
                hidden = None
        else:
            if disabled is None:
                disabled = er.RegistryEntryDisabler.INTEGRATION
            if hidden is None:
                hidden = er.RegistryEntryHider.INTEGRATION
        if disabled != row.disabled_by or hidden != row.hidden_by:
            registry.async_update_entity(row.entity_id, disabled_by=disabled, hidden_by=hidden)


@callback
def async_enable_standard_entities(hass: HomeAssistant, entry: NinebotConfigEntry) -> int:
    """Upgrade our disabled defaults while preserving every user disable choice."""
    registry = er.async_get(hass)
    enabled = 0
    for row, sn in _owned_entities(hass, entry):
        snapshot = entry.runtime_data.coordinator.data.get(sn)
        if (
            snapshot is not None
            and snapshot.present
            and _matches(row, sn, visible_keys(entry, sn), entry)
        ):
            disabled = row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            hidden = row.hidden_by is er.RegistryEntryHider.INTEGRATION
            if disabled or hidden:
                registry.async_update_entity(
                    row.entity_id,
                    disabled_by=None if disabled else row.disabled_by,
                    hidden_by=None if hidden else row.hidden_by,
                )
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
            and _matches(row, sn, keys, entry)
        ):
            registry.async_update_entity(row.entity_id, disabled_by=None)
            enabled += 1
    return enabled


async def async_migrate_entity_ids(hass: HomeAssistant, entry: NinebotConfigEntry) -> None:
    identities = entry.runtime_data.identities
    if identities is None:
        return
    candidates = []
    conversions = []
    aliases = {
        "main_power": "power",
        "remaining_range": "endurance",
        "info": "refresh",
    }
    for row, sn in _owned_entities(hass, entry):
        if sn not in entry.runtime_data.coordinator.data or sn not in identities.seeds:
            continue
        old_keys = (
            ("unlocked", "vehicle_lock")
            if row.domain == "binary_sensor"
            else ("seat_lock_raw",)
            if row.domain == "sensor"
            else ()
        )
        if any(
            row.unique_id in {*legacy_uids(sn, key), scoped_uid(entry, sn, key)} for key in old_keys
        ):
            conversions.append((row, sn, "seat_lock" if row.domain == "sensor" else "vehicle_lock"))
            continue
        keys = visible_keys(entry, sn).get(row.domain, frozenset())
        # Naming migration includes configured-but-currently-inactive parameters.
        if row.domain == "number":
            keys |= frozenset({"main_battery_voltage", "battery_capacity"})
        elif row.domain == "sensor":
            keys |= frozenset({"battery_rated_energy"})
        elif row.domain == "lock":
            converted = [
                key
                for key in ("vehicle_lock", "seat_lock")
                if any(
                    plan["uid"] == row.unique_id
                    and plan["reason"].startswith("lock_conversion")
                    and plan["new"] == identities.seeds[sn].entity_id("lock", key)
                    for plan in identities.migrations.values()
                )
            ]
            if len(converted) == 1:
                candidates.append((row, sn, converted[0]))
                continue
        for key in keys:
            if row.unique_id in {*legacy_uids(sn, key), scoped_uid(entry, sn, key)}:
                candidates.append((row, sn, aliases.get(key, key)))
                break
    await identities.async_migrate(candidates, force=True)
    await identities.async_convert_locks(conversions)
    # Core stores the original suggested unit in the registry. Changing the
    # description alone would leave existing rides displaying minutes.
    for row, sn in _owned_entities(hass, entry):
        domain_options = dict(row.options.get(DOMAIN, {}))
        if row.domain != "sensor" or domain_options.get("ride_hours_revision") == 2:
            continue
        duration_keys = (
            "month_duration",
            "last_ride_duration",
            "today_ride_duration",
            "yesterday_ride_duration",
        )
        if not any(
            row.unique_id in {*legacy_uids(sn, key), scoped_uid(entry, sn, key)}
            for key in duration_keys
        ):
            continue
        registry = er.async_get(hass)
        registry.async_update_entity_options(
            row.entity_id,
            "sensor",
            {
                **row.options.get("sensor", {}),
                "unit_of_measurement": UnitOfTime.HOURS,
                "display_precision": 2,
            },
        )
        registry.async_update_entity_options(
            row.entity_id, DOMAIN, {**row.options.get(DOMAIN, {}), "ride_hours_revision": 2}
        )

    # Existing user precision settings are preserved. Promote only the default
    # display precision once for the retained and restored cloud-range sensors.
    for row, sn in _owned_entities(hass, entry):
        domain_options = dict(row.options.get(DOMAIN, {}))
        if row.domain != "sensor" or domain_options.get("cloud_range_revision") == 1:
            continue
        if not any(
            row.unique_id in {*legacy_uids(sn, key), scoped_uid(entry, sn, key)}
            for key in ("endurance", "remaining_range", "range_estimated", "range_ai")
        ):
            continue
        registry = er.async_get(hass)
        options: dict[str, Any] = dict(row.options.get("sensor", {}))
        if "display_precision" not in options:
            registry.async_update_entity_options(
                row.entity_id, "sensor", {**options, "display_precision": 1}
            )
        registry.async_update_entity_options(
            row.entity_id, DOMAIN, {**row.options.get(DOMAIN, {}), "cloud_range_revision": 1}
        )
