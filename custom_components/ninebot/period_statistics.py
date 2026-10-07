"""Honest business-day projections: server chart and complete ride windows."""

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from .const import BUSINESS_TIMEZONE, DETAIL_INTERVAL
from .parsing import previous_month
from .statistics_store import TravelStatisticsStore, timestamp

type DayMetric = Literal["distance_km", "ride_count", "duration_s", "energy_wh"]
DAY_METRICS: tuple[DayMetric, ...] = ("distance_km", "ride_count", "duration_s", "energy_wh")


@dataclass(frozen=True)
class DaySummary:
    day: date
    distance_km: float | None = None
    ride_count: int | None = None
    duration_s: float | None = None
    energy_wh: float | None = None
    received_at: str | None = None
    revision: int | None = None
    availability: tuple[tuple[str, str], ...] = ()
    distance_basis: str = "server_daily_chart"
    rides_complete: bool = False
    adjacent_received_at: str | None = None
    adjacent_revision: int | None = None

    def reason(self, metric: DayMetric) -> str:
        return dict(self.availability).get(metric, "available")


def day_summary(store: TravelStatisticsStore, sn: str, day: date, now: datetime) -> DaySummary:
    """Missing dates/rows are gaps; only a proven empty window means zero."""
    record = store.month(sn, day.strftime("%Y%m"))
    zone = ZoneInfo(BUSINESS_TIMEZONE)
    business_today = now.astimezone(zone).date()
    if record is None:
        return DaySummary(
            day, availability=tuple((field, "missing_month") for field in DAY_METRICS)
        )
    sampled = timestamp(record.received_at)
    if (
        day > business_today
        or sampled > now
        or day > sampled.astimezone(zone).date()
        or (day < business_today and day >= sampled.astimezone(zone).date())
        or (day == business_today and (now - sampled).total_seconds() > 3 * DETAIL_INTERVAL)
    ):
        return DaySummary(
            day,
            received_at=record.received_at,
            revision=record.revision,
            availability=tuple((field, "day_not_observed") for field in DAY_METRICS),
        )
    distance = None
    if record.chart_status == "valid" and len(record.daily_distance_km) >= day.day:
        distance = record.daily_distance_km[day.day - 1]
    reasons: dict[str, str] = {
        "distance_km": "available" if distance is not None else "missing_daily_chart"
    }
    rows, complete = store.month_rides(sn, record.month)
    previous_record = None
    adjacent_missing = False
    if day.day == 1:
        # The chosen end-date policy needs the adjacent month at rollover;
        # don't assume upstream month filtering uses an end date too.
        previous_rows, previous_complete = store.month_rides(sn, previous_month(record.month))
        previous_record = store.month(sn, previous_month(record.month))
        previous_observed = bool(
            previous_complete
            and previous_record
            and timestamp(previous_record.received_at) <= now
            and timestamp(previous_record.received_at).astimezone(zone).date() >= day
        )
        adjacent_missing = complete and not previous_observed
        complete = complete and previous_observed
        unique = {row.ride_id: row for row in (*previous_rows, *rows)}
        rows = tuple(unique.values())
    selected = []
    for row in rows:
        if row.ended_at is None or "ended_at" not in row.current_fields:
            complete = False
            continue
        ended = timestamp(row.ended_at)
        if ended > sampled:
            complete = False
            continue
        if ended.astimezone(zone).date() == day:
            selected.append(row)
    count = len(selected) if complete else None
    totals: dict[str, float | None] = {}
    for field in ("distance_m", "duration_s", "energy_wh"):
        values = [getattr(row, field) for row in selected]
        valid = (
            complete
            and all(field in row.current_fields for row in selected)
            and all(value is not None for value in values)
        )
        if field == "duration_s":
            valid = valid and all(
                row.started_at is not None
                and row.ended_at is not None
                and "started_at" in row.current_fields
                and row.duration_s is not None
                and abs(
                    (timestamp(row.ended_at) - timestamp(row.started_at)).total_seconds()
                    - row.duration_s
                )
                <= 1
                for row in selected
            )
        total = sum(value for value in values if value is not None) if valid else None
        totals[field] = total if total is None or math.isfinite(total) else None
    basis = "server_daily_chart"
    if distance is None and totals["distance_m"] is not None:
        distance = totals["distance_m"] / 1000
        reasons["distance_km"] = "available"
        basis = "returned_unique_rides"
    for field, value in (
        ("ride_count", count),
        ("duration_s", totals["duration_s"]),
        ("energy_wh", totals["energy_wh"]),
    ):
        reasons[field] = (
            "available"
            if value is not None
            else (
                "adjacent_month_not_observed"
                if adjacent_missing
                else "incomplete_month_list"
                if not complete
                else "missing_metric"
            )
        )
    return DaySummary(
        day,
        distance,
        count,
        totals["duration_s"],
        totals["energy_wh"],
        record.received_at,
        record.revision,
        tuple(reasons.items()),
        basis,
        complete,
        previous_record.received_at if previous_record else None,
        previous_record.revision if previous_record else None,
    )
