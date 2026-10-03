"""Battery observation identity, separate from unverified physical device identity."""

import hashlib
import json

from .models import Battery, BatteryInfo


def current_battery(info: BatteryInfo) -> Battery | None:
    """Legacy vehicle measurements describe its sole currently reported pack.

    An array's first row is not a primary-pack contract. A later replacement
    remains a vehicle measurement; its history must not be reassigned to a pack.
    """
    return info.batteries[0] if len(info.batteries) == 1 else None


def identified_battery(info: BatteryInfo, identity: str) -> Battery | None:
    """Retain a pack measurement across ordering changes, never slot fallback."""
    return next((pack for pack in info.batteries if pack.identified and pack.key == identity), None)


def battery_signature(info: BatteryInfo) -> str:
    """An order-independent observation signature, not a physical pack guarantee.

    Unknown packs contribute only their count. Canonical typed tuples avoid
    collisions between serial separators, slot labels and actual serials.
    """
    identities = sorted(
        (pack.identified, pack.key if pack.identified else "") for pack in info.batteries
    )
    return hashlib.sha256(json.dumps(identities, separators=(",", ":")).encode()).hexdigest()


def battery_summary(info: BatteryInfo) -> dict[str, int | bool | str]:
    """Explain grouping without serials, hashes, measurements or guessed topology."""
    identified = sum(pack.identified for pack in info.batteries)
    return {
        "reported_row_count": len(info.batteries),
        "identified_row_count": identified,
        "unidentified_row_count": len(info.batteries) - identified,
        "vehicle_measurement_unambiguous": current_battery(info) is not None,
        "device_assignment": "vehicle",
        "component_model": "unverified",
    }
