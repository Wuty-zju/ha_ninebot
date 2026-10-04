"""A strict diagnostic whitelist. No raw payload, identity or position."""

import platform
from importlib.metadata import version
from typing import Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .battery import battery_summary
from .capabilities import CONTROL_ACTIONS
from .compat import child_registry_api_available
from .const import VERSION
from .runtime import NinebotConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NinebotConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    vehicles = []
    for snapshot in runtime.coordinator.data.values():
        groups = {}
        for group in ("status", "battery", "travel"):
            freshness = getattr(snapshot, f"{group}_freshness")
            groups[group] = {
                "attempted_at": freshness.attempted_at.isoformat()
                if freshness.attempted_at
                else None,
                "succeeded_at": freshness.succeeded_at.isoformat()
                if freshness.succeeded_at
                else None,
                "error": freshness.error.value if freshness.error else None,
            }
        vehicles.append(
            {
                "present": snapshot.present,
                "groups": groups,
                "polling_demand": runtime.coordinator.demand(snapshot.profile.sn).diagnostics(),
                "battery_count": len(snapshot.battery.batteries),
                "battery_model": battery_summary(snapshot.battery),
                "cycle_support": [
                    battery.cycle_supported for battery in snapshot.battery.batteries
                ],
                "controls": {
                    action: runtime.coordinator.controls_enabled(snapshot.profile.sn, action)
                    for action in ("bell", "buck", "engine/start", "engine/stop")
                },
                "control_policy": {
                    action: runtime.coordinator.control_decision(
                        snapshot.profile.sn, action
                    ).diagnostics()
                    for action in sorted(CONTROL_ACTIONS)
                },
            }
        )
    return {
        "integration_version": VERSION,
        "ninecli_version": version("ninecli"),
        "ha_version": HA_VERSION,
        "python_version": platform.python_version(),
        "platform": {"system": platform.system(), "architecture": platform.machine()},
        "compatibility": {"child_registry_api": child_registry_api_available()},
        "backend_support": {
            "query_endpoints": sorted(
                endpoint.value for endpoint in runtime.coordinator.backend.endpoints
            ),
            "control_actions": sorted(runtime.coordinator.backend.control_actions),
        },
        "identity_scheme": entry.data.get("identity_scheme"),
        "identity_conflict_count": len(runtime.identity_conflicts),
        "obsolete_entities_removed": runtime.obsolete_entities_removed,
        "standard_entities_enabled": runtime.standard_entities_enabled,
        "configured_controls_enabled": runtime.configured_controls_enabled,
        "vehicles": vehicles,
        "raw_schema": runtime.coordinator.raw.diagnostics(dt_util.utcnow()),
        "ride_events": runtime.events.diagnostics() if runtime.events else None,
    }
