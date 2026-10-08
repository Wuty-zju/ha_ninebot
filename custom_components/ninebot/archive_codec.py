"""Reviewed, bounded domain persistence; no raw payload, GPS or credentials."""

import json
from calendar import monthrange
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from .models import LastRide, TravelMonth, VehicleProfile
from .month_summary import DailyMileage, MonthSummary
from .parsing import number, previous_month
from .raw import Endpoint
from .ride_models import FieldState, Ride
from .travel import latest_ride, opaque_id

MAX_ROW_BYTES = 128 * 1024
RIDE_VALUES = (
    "started_at",
    "ended_at",
    "distance_m",
    "duration_s",
    "energy_raw",
    "used_electricity_raw",
    "speed_raw",
    "server_max_speed_m_s",
    "server_average_speed_raw",
)
RIDE_METADATA = (
    "issues",
    "field_provenance",
    "parser_contract",
    "field_states",
    "field_sources",
    "detail_field_states",
    "precision",
)


def stamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("Invalid archive timestamp")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or not 2000 <= result.year <= 2099:
        raise ValueError("Invalid archive timestamp")
    return result.astimezone(UTC)


def encoded(value: dict[str, Any]) -> str:
    result = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    if len(result.encode()) > MAX_ROW_BYTES:
        raise ValueError("Archive row budget")
    return result


def decoded(value: str) -> dict[str, Any]:
    if len(value.encode()) > MAX_ROW_BYTES:
        raise ValueError("Archive row budget")
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError("Invalid archive object")
    return result


def ride_data(ride: Ride) -> dict[str, Any]:
    """Explicit whitelist; adding a domain property never silently persists it."""
    for value in (ride.started_at, ride.ended_at):
        if value is not None and (value.tzinfo is None or not 2000 <= value.year <= 2099):
            raise ValueError("Invalid archive timestamp")
    return {
        "query_month": ride.query_month,
        "source": ride.source.value,
        "ride_id": ride.ride_id,
        "detail_id": ride.detail_id,
        **{
            name: getattr(ride, name).astimezone(UTC).isoformat()
            if name in {"started_at", "ended_at"} and getattr(ride, name) is not None
            else getattr(ride, name)
            for name in RIDE_VALUES
        },
        **{name: getattr(ride, name) for name in RIDE_METADATA},
    }


def restored_ride(raw: dict[str, Any]) -> Ride:
    allowed = {"query_month", "source", "ride_id", "detail_id", *RIDE_VALUES, *RIDE_METADATA}
    if set(raw) != allowed:
        raise ValueError("Invalid archive ride fields")
    previous_month(raw["query_month"])
    source = Endpoint(raw["source"])
    if source not in {Endpoint.TRAVEL, Endpoint.TRIP_DETAIL}:
        raise ValueError("Invalid ride source")
    for name in ("ride_id", "detail_id"):
        value = raw[name]
        if value is not None and (not isinstance(value, str) or opaque_id(value) != value):
            raise ValueError("Invalid archive identity")
    values: dict[str, Any] = {}
    for name in RIDE_VALUES:
        value = raw[name]
        if name in {"started_at", "ended_at"}:
            values[name] = stamp(value) if value is not None else None
        else:
            if value is not None and (type(value) not in {int, float} or number(value, 0) is None):
                raise ValueError("Invalid archive metric")
            values[name] = value
    if values["started_at"] and values["ended_at"] and values["ended_at"] < values["started_at"]:
        raise ValueError("Invalid archive time order")
    for name in RIDE_METADATA:
        value = raw[name]
        if name == "parser_contract":
            if not isinstance(value, str) or not 1 <= len(value) <= 128:
                raise ValueError("Invalid parser contract")
            values[name] = value
            continue
        if not isinstance(value, (list, tuple)) or len(value) > 64:
            raise ValueError("Invalid archive metadata")
        if name == "issues":
            if any(not isinstance(item, str) or len(item) > 128 for item in value):
                raise ValueError("Invalid archive issues")
            values[name] = tuple(value)
            continue
        pairs = []
        for pair in value:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("Invalid archive pair")
            key, item = pair
            if not isinstance(key, str) or not 1 <= len(key) <= 64:
                raise ValueError("Invalid metadata key")
            if name in {"field_states", "detail_field_states"}:
                item = FieldState(item)
            elif name == "precision":
                if type(item) is not int or not 0 <= item <= 15:
                    raise ValueError("Invalid precision")
            elif not isinstance(item, str) or len(item) > 256:
                raise ValueError("Invalid provenance")
            pairs.append((key, item))
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("Duplicate metadata key")
        values[name] = tuple(sorted(pairs))
    return Ride(raw["query_month"], source, raw["ride_id"], raw["detail_id"], **values)


def merged_ride(before: Ride | None, incoming: Ride) -> Ride:
    """New valid facts amend an identity; absent/null/invalid facts stay explicit.

    The latest observation's presence states are retained even when an older
    valid value survives. This is different from claiming it was reported now.
    """
    if before is None:
        return restored_ride(decoded(encoded(ride_data(incoming))))
    if incoming.ride_id != before.ride_id:
        raise ValueError("Archive identity mismatch")
    provenance, sources = dict(before.field_provenance), dict(before.field_sources)
    incoming_provenance, incoming_sources = (
        dict(incoming.field_provenance),
        dict(incoming.field_sources),
    )
    values = {}
    precision = dict(before.precision)
    incoming_precision = dict(incoming.precision)
    for name in RIDE_VALUES:
        after = getattr(incoming, name)
        values[name] = after if after is not None else getattr(before, name)
        if after is not None:
            provenance.pop(name, None)
            sources.pop(name, None)
            if name in incoming_provenance:
                provenance[name] = incoming_provenance[name]
            if name in incoming_sources:
                sources[name] = incoming_sources[name]
            raw_key = {
                "distance_m": "mileages",
                "duration_s": "duration",
                "energy_raw": "ec",
                "speed_raw": "speed",
            }.get(name)
            if raw_key is not None and raw_key in incoming_precision:
                precision[raw_key] = (
                    max(precision.get(raw_key, 0), incoming_precision[raw_key])
                    if getattr(before, name) == after
                    else incoming_precision[raw_key]
                )
    detail_id = incoming.detail_id or before.detail_id
    issues = set(incoming.issues)
    if incoming.started_at is None or incoming.ended_at is None:
        issues.update(
            set(before.issues) & {"conflicting_time_representations", "reversed_timestamps"}
        )
    if {"conflicting_ride_ids", "conflicting_detail_ids"} & set(before.issues + incoming.issues):
        detail_id = None
        issues.add("archive_detail_identity_conflict")
    if "archive_detail_identity_conflict" in before.issues or (
        before.detail_id and incoming.detail_id and before.detail_id != incoming.detail_id
    ):
        detail_id = None
        issues.add("archive_detail_identity_conflict")
    if values["started_at"] is not None and values["ended_at"] is not None:
        issues.discard("duration_time_difference")
        duration = values["duration_s"]
        if (
            duration is not None
            and abs((values["ended_at"] - values["started_at"]).total_seconds() - duration) > 1
        ):
            issues.add("duration_time_difference")
    result = replace(
        incoming,
        **values,
        detail_id=detail_id,
        detail_field_states=incoming.detail_field_states or before.detail_field_states,
        issues=tuple(sorted(issues)),
        field_provenance=tuple(sorted(provenance.items())),
        field_sources=tuple(sorted(sources.items())),
        precision=tuple(sorted(precision.items())),
    )
    return restored_ride(decoded(encoded(ride_data(result))))


def month_data(travel: TravelMonth) -> dict[str, Any]:
    if travel.summary is None:
        raise ValueError("Archive requires a month summary")
    summary = asdict(travel.summary)
    summary["daily_mileage"] = [
        {"day": point.day.isoformat(), "distance_km": point.distance_km}
        for point in travel.summary.daily_mileage
    ]
    return {"month": travel.month, "summary": summary, "precision": travel.precision}


def restored_month(raw: dict[str, Any], rides: tuple[Ride, ...] = ()) -> TravelMonth:
    if set(raw) != {"month", "summary", "precision"}:
        raise ValueError("Invalid archive month fields")
    previous_month(raw["month"])
    summary = raw["summary"]
    if not isinstance(summary, dict) or set(summary) != set(MonthSummary.__dataclass_fields__):
        raise ValueError("Invalid archive summary")
    if summary["month"] != raw["month"]:
        raise ValueError("Archive month mismatch")
    points = summary["daily_mileage"]
    if not isinstance(points, list) or len(points) > 31:
        raise ValueError("Invalid archive daily series")
    daily = []
    for point in points:
        if not isinstance(point, dict) or set(point) != {"day", "distance_km"}:
            raise ValueError("Invalid archive day")
        day = date.fromisoformat(point["day"])
        value = point["distance_km"]
        if (
            day.strftime("%Y%m") != raw["month"]
            or type(value) not in {int, float}
            or number(value, 0, 1000000) is None
        ):
            raise ValueError("Invalid archive day")
        daily.append(DailyMileage(day, value))
    expected = monthrange(int(raw["month"][:4]), int(raw["month"][4:]))[1]
    if daily and [point.day.day for point in daily] != list(range(1, expected + 1)):
        raise ValueError("Invalid archive calendar")
    warnings = summary["warnings"]
    if (
        not isinstance(warnings, (list, tuple))
        or len(warnings) > 64
        or any(not isinstance(value, str) or not 1 <= len(value) <= 128 for value in warnings)
    ):
        raise ValueError("Invalid archive warnings")
    result = MonthSummary(**{**summary, "daily_mileage": tuple(daily), "warnings": tuple(warnings)})
    for name in (
        "mileage_km",
        "energy_wh",
        "duration_s",
        "coverage",
        "returned_distance_m",
        "returned_duration_s",
        "returned_energy_wh",
    ):
        value = getattr(result, name)
        if value is not None and (type(value) not in {int, float} or number(value, 0) is None):
            raise ValueError("Invalid archive aggregate")
    if result.coverage is not None and result.coverage > 1:
        raise ValueError("Invalid archive coverage")
    for name in ("ride_count", "returned_count", "unique_ride_count"):
        value = getattr(result, name)
        if (name != "ride_count" and value is None) or (
            value is not None and (type(value) is not int or value < 0)
        ):
            raise ValueError("Invalid archive count")
    if result.unique_ride_count > result.returned_count:
        raise ValueError("Invalid archive identities")
    if result.list_complete is not None and type(result.list_complete) is not bool:
        raise ValueError("Invalid archive completeness")
    if result.list_complete is True and (
        result.ride_count != result.returned_count
        or result.returned_count != result.unique_ride_count
    ):
        raise ValueError("Invalid archive completeness")
    if result.chart_status not in {
        "valid",
        "invalid",
        "not_reported",
        "inconsistent",
        "total_unavailable",
    }:
        raise ValueError("Invalid archive chart")
    if (
        result.chart_status in {"valid", "inconsistent", "total_unavailable"}
        and len(daily) != expected
    ):
        raise ValueError("Invalid archive chart")
    if result.chart_status in {"invalid", "not_reported"} and daily:
        raise ValueError("Invalid archive chart")
    if result.chart_status == "valid":
        total = sum(Decimal(str(point.distance_km)) for point in daily)
        if result.mileage_km is None or abs(total - Decimal(str(result.mileage_km))) > Decimal(
            "0.05"
        ):
            raise ValueError("Invalid archive daily total")
    precision = raw["precision"]
    if not isinstance(precision, (list, tuple)) or len(precision) > 64:
        raise ValueError("Invalid archive precision")
    pairs = []
    for pair in precision:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise ValueError("Invalid archive precision")
        key, value = pair
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= 64
            or type(value) is not int
            or not 0 <= value <= 15
        ):
            raise ValueError("Invalid archive precision")
        pairs.append((key, value))
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("Invalid archive precision")
    last = latest_ride(rides)
    return TravelMonth(
        raw["month"],
        result.mileage_km,
        result.energy_wh,
        last_ride=LastRide(
            raw["month"],
            last.distance_m / 1000 if last.distance_m is not None else None,
            last.energy_raw,
            last.ride_id,
            last,
        )
        if last
        else None,
        rides=rides,
        reported_ride_count=result.ride_count,
        reported_duration_s=result.duration_s,
        summary=result,
        precision=tuple(sorted(pairs)),
    )


def profile_data(profile: VehicleProfile) -> dict[str, str]:
    # Images may have signed query parameters; observations can contain VIN
    # and personal values. Offline discovery only needs the identity/name/model.
    return {"sn": profile.sn, "name": profile.name, "model": profile.model}


def restored_profile(raw: dict[str, Any]) -> VehicleProfile:
    if set(raw) != {"sn", "name", "model"} or any(
        not isinstance(value, str) or not value or len(value) > 256 for value in raw.values()
    ):
        raise ValueError("Invalid archive profile")
    if any(ord(char) < 32 for value in raw.values() for char in value):
        raise ValueError("Invalid archive profile")
    return VehicleProfile(**raw)
