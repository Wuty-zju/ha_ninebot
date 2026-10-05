"""Audited, bounded scalar observations; personal fields are never copied here."""

from dataclasses import dataclass
from typing import Literal

from .parsing import JsonObject, JsonScalar, raw_scalar

type ObservationGroup = Literal["profile", "status", "battery"]


@dataclass(frozen=True)
class RawField:
    key: str
    group: ObservationGroup
    path: str


RAW_FIELDS = (
    RawField("activation_time_raw", "profile", "active_date"),
    RawField("activation_status_raw", "profile", "actived"),
    RawField("business_type_raw", "profile", "businessType"),
    RawField("vehicle_type_raw", "profile", "vehicle_type"),
    RawField("shared_vehicle_index_raw", "profile", "common_user_vehicle_index"),
    RawField("shared_version_raw", "profile", "common_user_version"),
    RawField("shared_user_raw", "profile", "is_common_user"),
    RawField("special_image_raw", "profile", "is_img_special"),
    RawField("location_delay_raw", "profile", "loc_delay_time"),
    RawField("support_raw", "profile", "support"),
    RawField("latest_support_raw", "profile", "latest_support"),
    RawField("shared_permissions_raw", "profile", "common_user_permissions"),
    RawField("service_remaining_days_raw", "profile", "smart_service_surplus_days"),
    RawField("odometer_raw", "profile", "total_mileage"),
    RawField("battery_present_raw", "status", "battery_exist"),
    RawField("seat_lock_raw", "status", "barrel_lock_status"),
    RawField("acc_raw", "status", "loc.acc"),
    RawField("service_expired_raw", "status", "is_smart_service_expired"),
    RawField("range_preference_raw", "status", "left_mileage_user_choose"),
    RawField("precise_range_preference_raw", "status", "precise_mileage_user_choose"),
    RawField("status_shared_user_raw", "status", "is_common_user"),
    RawField("permissions_raw", "status", "permissions"),
    RawField("charge_timestamp_raw", "status", "remain_charge_timestamp"),
    RawField("battery_count_raw", "battery", "battery_count"),
    RawField("battery_type_raw", "battery", "battery_type"),
    RawField("main_electricity_raw", "battery", "battery_main.electricity"),
    RawField("root_electricity_raw", "battery", "electricity"),
    RawField("charging_protection_raw", "battery", "charging_protection.status"),
)

EXTRA_PATHS = {
    "status": ("remain_charge_time",),
    "battery": (
        "remain_charge_time",
        "charging",
        "have_bms_cycle_support",
        "battery_find_my_support",
    ),
    "profile": ("color", "vehicle_name_zh"),
}

# A scalar inventory is not a mandate to create one entity for every code.
# Protocol flags and duplicate readings remain available in the debug view.
ENTITY_FIELDS = tuple(
    field
    for field in RAW_FIELDS
    if field.key
    in {
        "battery_present_raw",
        "seat_lock_raw",
        "acc_raw",
        "service_expired_raw",
        "service_remaining_days_raw",
        "odometer_raw",
    }
)


def scalar_observations(raw: JsonObject, group: ObservationGroup) -> dict[str, JsonScalar]:
    """Preserve null/absent distinction, but neither arbitrary paths nor secrets."""
    result: dict[str, JsonScalar] = {}
    paths = [field.path for field in RAW_FIELDS if field.group == group]
    for path in (*paths, *EXTRA_PATHS[group]):
        value: object = raw
        for key in path.split("."):
            if not isinstance(value, dict) or key not in value:
                break
            value = value[key]
        else:
            result[path] = raw_scalar(value)
    return result
