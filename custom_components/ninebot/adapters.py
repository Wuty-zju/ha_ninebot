"""Strict, pure adapters for ninecli's decrypted business data."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .const import BUSINESS_TIMEZONE
from .exceptions import ErrorKind, NinebotError
from .models import Battery, BatteryInfo, LastRide, TravelMonth, VehicleProfile, VehicleStatus
from .parsing import boolean as boolean
from .parsing import number as number
from .parsing import payload as payload
from .parsing import previous_month as previous_month
from .parsing import text as text
from .travel import latest_ride, parse_rides

type JsonObject = dict[str, Any]


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


def travel(raw: object, query_month: str) -> TravelMonth:
    previous_month(query_month)  # Validate the caller's month even if data is empty.
    item = payload(raw)
    reported = text(item.get("month"))
    if reported is not None and reported != query_month:
        raise NinebotError(ErrorKind.PROTOCOL)
    rows = item.get("list")
    if rows is not None and not isinstance(rows, list):
        raise NinebotError(ErrorKind.PROTOCOL)
    rides = parse_rides(raw, query_month)
    selected = latest_ride(rides)
    last = None
    if selected is not None or rides:
        # Preserve legacy "last returned" values when timestamps are absent.
        # New timestamp-dependent representations must require proven ordering.
        ride = selected or rides[0]
        last = LastRide(
            query_month,
            ride.distance_m / 1000 if ride.distance_m is not None else None,
            ride.energy_raw,
            ride.ride_id,
            ride,
        )
    return TravelMonth(
        query_month,
        number(item.get("total_mileages"), 0, 1000000),
        number(item.get("ec"), 0),
        last,
        rides,
    )
