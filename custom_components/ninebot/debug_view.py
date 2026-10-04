"""Bounded parsed observations for an explicitly enabled per-vehicle view."""

import re
from typing import TYPE_CHECKING, Any

from .models import VehicleSnapshot
from .observations import RAW_FIELDS
from .parsing import JsonScalar, raw_scalar

if TYPE_CHECKING:
    from .coordinator import NinebotCoordinator
    from .estimation import EnergyModel

DEBUG_STATES = ("debug_disabled", "ok", "partial", "stale", "error")
DEBUG_ATTRIBUTES = frozenset({"groups", "parsed", "observations", "issues", "formatted"})


def numeric_observation(raw: object) -> JsonScalar:
    value = raw_scalar(raw)
    if isinstance(value, str) and (len(value) > 6 or not value.replace(".", "", 1).isdigit()):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) > 10**9:
        return None
    return value


def debug_view(
    snapshot: VehicleSnapshot, co: "NinebotCoordinator", model: "EnergyModel"
) -> tuple[str, dict[str, Any]]:
    """Never deserialize raw payloads, identifiers, GPS, trails or unknown keys."""
    groups = {}
    for name in ("profile", "status", "battery", "travel"):
        stamp = getattr(snapshot, f"{name}_freshness")
        groups[name] = {
            "fresh": co.fresh(snapshot.profile.sn, name),
            "received_at": stamp.succeeded_at.isoformat() if stamp.succeeded_at else None,
            "error": stamp.error.value if stamp.error else None,
        }
    present = [group["fresh"] for group in groups.values()]
    state = "ok" if all(present) else "partial" if any(present) else "stale"
    if any(group["error"] for group in groups.values()) and not any(present):
        state = "error"
    status, battery, month = snapshot.status, snapshot.battery, snapshot.travel
    remaining = status.charge_remaining
    safe_remaining = (
        remaining
        if remaining
        and len(remaining) <= 64
        and re.fullmatch(r"[\d\s:.,天小时分钟秒dhmins]+", remaining, re.IGNORECASE)
        else None
    )
    parsed: dict[str, Any] = {
        "status": {
            "soc_percent": status.battery,
            "charging": status.charging,
            "powered": status.powered,
            "locked": status.locked,
            "precise_range_km": status.range_precise,
            "estimated_range_km": status.range_estimated,
            "ai_range_km": status.range_ai,
            "remaining_charge_text": safe_remaining,
            "remaining_charge_status": "unparsed" if status.charge_remaining else "not_reported",
        },
        "battery": {
            "charging_power_w": battery.charging_power_raw,
            "returned_pack_count": len(battery.batteries),
            "packs": [
                {
                    "voltage_v": pack.voltage,
                    "temperature_c": pack.temperature,
                    "cycles": pack.cycles,
                    "cycle_supported": pack.cycle_supported,
                    "cycle_raw": numeric_observation(pack.cycle_raw),
                    "health_score_raw": numeric_observation(pack.score_raw),
                    "electricity_raw": numeric_observation(pack.electricity_raw),
                }
                for pack in battery.batteries[:4]
            ],
        },
        "travel": {
            "month": month.month if month else None,
            "mileage_km": month.mileage if month else None,
            "energy_wh": month.energy_raw if month else None,
            "reported_rides": month.reported_ride_count if month else None,
            "duration_s": month.reported_duration_s if month else None,
            "returned_rides": len(month.rides) if month else None,
            "list_complete": month.summary.list_complete if month and month.summary else None,
            "chart_status": month.summary.chart_status if month and month.summary else None,
        },
        "estimation": {
            "quality": model.quality,
            "generation": model.generation,
            "nominal_voltage_v": model.voltage,
            "capacity_ah": model.capacity,
        },
    }
    # Only scalar numeric codes from audited paths; arbitrary text and long
    # numeric identifiers are not useful in this additional debug presentation.
    observations = {}
    for field in RAW_FIELDS:
        value = numeric_observation(getattr(snapshot, field.group).observations.get(field.path))
        observations[f"{field.group}.{field.path}"] = value
    issues = []
    if any(pack.cycle_supported is False for pack in battery.batteries):
        issues.append("cycle_support_false")
    if len(battery.batteries) > 4:
        issues.append("debug_pack_list_truncated")
    if not status.charge_remaining:
        issues.append("remaining_charge_not_reported")
    return state, {
        "groups": groups,
        "parsed": parsed,
        "observations": observations,
        "issues": issues,
    }
