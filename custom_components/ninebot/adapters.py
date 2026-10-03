"""Strict, pure adapters for ninecli's decrypted business data."""

import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .const import BUSINESS_TIMEZONE
from .exceptions import ErrorKind, NinebotError
from .models import Battery, BatteryInfo, LastRide, TravelMonth, VehicleProfile, VehicleStatus

type JsonObject = dict[str, Any]


def number(value: object, low: float = -math.inf, high: float = math.inf) -> float | None:
    """Reject bool, NaN, Infinity and values outside the physical domain."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and low <= result <= high else None


def boolean(value: object) -> bool | None:
    """Accept only documented binary encodings, never Python truthiness."""
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return value == 1
    if isinstance(value, str) and value in ("0", "1"):
        return value == "1"
    return None


def text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def payload(raw: object) -> JsonObject:
    if not isinstance(raw, dict):
        raise NinebotError(ErrorKind.PROTOCOL)
    return raw


def profiles(raw: object) -> tuple[VehicleProfile, ...]:
    """An empty account is valid; malformed rows are not an empty account."""
    if not isinstance(raw, list):
        raise NinebotError(ErrorKind.PROTOCOL)
    result: dict[str, VehicleProfile] = {}
    for row in raw:
        item = payload(row)
        sn = text(item.get("wnumber")) or text(item.get("sn"))
        if sn is None:
            raise NinebotError(ErrorKind.PROTOCOL)
        image = text(item.get("v6_light_img_url")) or text(item.get("img_url"))
        profile = VehicleProfile(
            sn,
            text(item.get("device_name")) or text(item.get("ble_name")) or sn,
            text(item.get("vehicle_name_en")) or text(item.get("vehicle_name")) or "Ninebot",
            image if image and image.startswith("https://") else None,
        )
        # CLI merges owned/shared business lists. Reject ambiguous duplicates.
        if sn in result and result[sn] != profile:
            raise NinebotError(ErrorKind.PROTOCOL)
        result[sn] = profile
    return tuple(result.values())


def status(raw: object) -> VehicleStatus:
    item = payload(raw)
    location = item.get("loc")
    loc: JsonObject = location if isinstance(location, dict) else {}
    lat, lon = number(loc.get("lat"), -90, 90), number(loc.get("lon"), -180, 180)
    if lat is None or lon is None:
        lat = lon = None
    return VehicleStatus(
        battery=number(item.get("dump_energy"), 0, 100),
        locked=boolean(loc.get("lock", item.get("lock_status"))),
        powered=boolean(item.get("pwr")),
        charging=boolean(item.get("charging")),
        range_precise=number(item.get("precise_estimate_mileage"), 0, 5000),
        range_estimated=number(item.get("estimate_mileage"), 0, 5000),
        range_ai=number(item.get("ai_estimate_mileage"), 0, 5000),
        charge_remaining=text(item.get("remain_charge_time")),
        latitude=lat,
        longitude=lon,
    )


def batteries(raw: object) -> BatteryInfo:
    item = payload(raw)
    # battery command/proxy can preserve the battery-info data wrapper.
    if isinstance(item.get("data"), dict):
        item = item["data"]
    rows = item.get("battery_list")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise NinebotError(ErrorKind.PROTOCOL)
    result = []
    keys: set[str] = set()
    vehicle_support = boolean(item.get("have_bms_cycle_support"))
    for index, row in enumerate(rows):
        value = payload(row)
        identity = text(value.get("battery_sn")) or text(value.get("sn"))
        key = identity or f"slot_{index}"
        if key in keys:
            raise NinebotError(ErrorKind.PROTOCOL)
        keys.add(key)
        # Captured App responses put this capability alongside battery_list,
        # not inside each pack. Preserve explicit pack overrides when supplied,
        # but a vehicle-wide denial cannot be promoted to support by one row.
        support = (
            False
            if vehicle_support is False
            else boolean(value["have_bms_cycle_support"])
            if "have_bms_cycle_support" in value
            else vehicle_support
        )
        cycles = number(value.get("bms_cycle"), 0, 100000)
        result.append(
            Battery(
                key,
                identity is not None,
                number(value.get("bms_volt"), 0, 300),
                number(value.get("bat_temp"), -50, 150),
                int(cycles)
                if support is True and cycles is not None and cycles.is_integer()
                else None,
                support,
            )
        )
    return BatteryInfo(tuple(result), number(item.get("charging_power"), 0, 100000))


def month_at(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("An aware timestamp is required")
    return now.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).strftime("%Y%m")


def previous_month(month: str) -> str:
    if len(month) != 6 or not month.isdecimal():
        raise ValueError("Invalid month")
    date = datetime.strptime(month, "%Y%m")
    return f"{date.year - 1}12" if date.month == 1 else f"{date.year}{date.month - 1:02}"


def travel(raw: object, query_month: str) -> TravelMonth:
    previous_month(query_month)  # Validate the caller's month even if data is empty.
    item = payload(raw)
    reported = text(item.get("month"))
    if reported is not None and reported != query_month:
        raise NinebotError(ErrorKind.PROTOCOL)
    rows = item.get("list")
    if rows is not None and not isinstance(rows, list):
        raise NinebotError(ErrorKind.PROTOCOL)
    last = None
    if rows:
        # First-row ordering is not independently established: last_* is opt-in.
        ride = payload(rows[0])
        last = LastRide(
            query_month,
            number(ride.get("mileages"), 0, 100000),
            number(ride.get("ec"), 0),
            text(ride.get("id")) or text(ride.get("detail_id")),
        )
    return TravelMonth(
        query_month, number(item.get("total_mileages"), 0, 1000000), number(item.get("ec"), 0), last
    )
