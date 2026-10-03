"""A strict diagnostic whitelist. No raw payload, identity or position."""

from importlib.metadata import version
from typing import Any

from homeassistant.core import HomeAssistant

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
                "battery_count": len(snapshot.battery.batteries),
                "cycle_support": [
                    battery.cycle_supported for battery in snapshot.battery.batteries
                ],
            }
        )
    return {
        "integration_version": VERSION,
        "ninecli_version": version("ninecli"),
        "identity_scheme": entry.data.get("identity_scheme"),
        "identity_conflict_count": len(runtime.identity_conflicts),
        "vehicles": vehicles,
    }
