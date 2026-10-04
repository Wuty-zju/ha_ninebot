"""Validated daily mileage and honest coverage of a returned month list."""

import math
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from .parsing import integer, number, previous_month
from .ride_models import Ride


@dataclass(frozen=True)
class DailyMileage:
    day: date
    distance_km: float


@dataclass(frozen=True)
class MonthSummary:
    month: str
    mileage_km: float | None
    energy_wh: float | None
    ride_count: int | None
    duration_s: float | None
    daily_mileage: tuple[DailyMileage, ...]
    chart_status: str
    returned_count: int
    unique_ride_count: int
    list_complete: bool | None
    coverage: float | None
    returned_distance_m: float | None
    returned_duration_s: float | None
    returned_energy_wh: float | None
    warnings: tuple[str, ...]


def sum_rides(rides: tuple[Ride, ...], field: str) -> float | None:
    """Never silently add only the known values of incomplete records."""
    values = [getattr(ride, field) for ride in rides]
    if any(value is None for value in values):
        return None
    total = sum(values)
    return total if math.isfinite(total) else None


def summarize_month(raw: dict[str, Any], month: str, rides: tuple[Ride, ...]) -> MonthSummary:
    previous_month(month)
    year, month_number = int(month[:4]), int(month[4:])
    expected_days = monthrange(year, month_number)[1]
    mileage = number(raw.get("total_mileages"), 0, 1000000)
    energy = number(raw.get("ec"), 0)
    count = integer(raw.get("times"))
    duration = number(raw.get("duration"), 0, 2678400)
    warnings: list[str] = []
    daily: tuple[DailyMileage, ...] = ()
    chart_status = "not_reported"
    chart = raw.get("detail")
    if chart is not None:
        chart_status = "invalid"
        if isinstance(chart, list) and len(chart) == expected_days:
            values = [number(value, 0, 1000000) for value in chart]
            if all(value is not None for value in values):
                daily = tuple(
                    DailyMileage(date(year, month_number, day), value)
                    for day, value in enumerate(values, 1)
                    if value is not None
                )
                chart_status = "valid" if mileage is not None else "total_unavailable"
                # Compare decimal totals with a small allowance for the final
                # reported total's rounding; never fill/scale a discrepant chart.
                total = sum(Decimal(str(point.distance_km)) for point in daily)
                if mileage is not None and abs(total - Decimal(str(mileage))) > Decimal("0.05"):
                    chart_status = "inconsistent"
        if chart_status != "valid":
            warnings.append(f"daily_mileage_{chart_status}")
    identities = [ride.ride_id for ride in rides if ride.ride_id is not None]
    unique_count = len(set(identities))
    identity_complete = len(identities) == len(rides) == unique_count
    complete: bool | None = None
    coverage = None
    if count is not None:
        if count > len(rides):
            complete = False
            warnings.append("month_list_partial")
        elif count < len(rides):
            warnings.append("reported_count_inconsistent")
        elif identity_complete:
            complete = True
        if count > 0 and identity_complete and len(rides) <= count:
            coverage = len(rides) / count
        elif count == 0 and not rides:
            coverage = 1.0
    else:
        warnings.append("reported_count_unavailable")
    if not identity_complete:
        warnings.append("ride_identity_incomplete")
    return MonthSummary(
        month,
        mileage,
        energy,
        count,
        duration,
        daily,
        chart_status,
        len(rides),
        unique_count,
        complete,
        coverage,
        sum_rides(rides, "distance_m"),
        sum_rides(rides, "duration_s"),
        sum_rides(rides, "energy_raw"),
        tuple(warnings),
    )
