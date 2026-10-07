"""Bounded period responses from the existing ledger, never another history scan."""

import math
from calendar import monthrange
from copy import copy
from datetime import date, datetime
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .compat import validation as vol
from .const import BUSINESS_TIMEZONE, DOMAIN
from .exceptions import NinebotError
from .parsing import previous_month
from .period_statistics import DAY_METRICS, day_summary
from .services import assert_query_scope, month_data, query_month, resolve_vehicle, validation_error
from .statistics_store import TravelStatisticsStore, timestamp
from .travel_statistics import energy_statistics

MAX_STATISTICS_MONTHS = 6
STATISTICS_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("start_month"): query_month,
        vol.Required("end_month"): query_month,
        vol.Optional("include_daily", default=True): cv.boolean,
        vol.Optional("refresh", default=False): cv.boolean,
    }
)
METRICS = ("distance_km", "energy_wh", "ride_count", "duration_s")


def months_between(start: str, end: str) -> tuple[str, ...]:
    count = (int(end[:4]) - int(start[:4])) * 12 + int(end[4:]) - int(start[4:]) + 1
    if not 1 <= count <= MAX_STATISTICS_MONTHS:
        raise validation_error("statistics_range")
    months = [end]
    while months[-1] != start:
        months.append(previous_month(months[-1]))
    return tuple(reversed(months))


def finite_sum(values: list[float | int | None]) -> float | int | None:
    if any(value is None for value in values):
        return None
    try:
        total = sum(value for value in values if value is not None)
        return total if math.isfinite(total) else None
    except OverflowError:
        return None


def statistics_response(
    store: TravelStatisticsStore,
    sn: str,
    months: tuple[str, ...],
    now: datetime,
    include_daily: bool,
) -> dict[str, Any]:
    """Atomic immutable input view; scope/basis and each metric stay explicit."""
    today = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date()
    month_rows: list[dict[str, Any]] = []
    day_rows: list[dict[str, Any]] = []
    for month in months:
        year, month_number = int(month[:4]), int(month[4:])
        last_day = date(year, month_number, monthrange(year, month_number)[1])
        record = store.month(sn, month)
        valid = record is not None and timestamp(record.received_at) <= now
        observed_after_end = bool(
            valid
            and record
            and timestamp(record.received_at).astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date()
            > last_day
        )
        values = {field: getattr(record, field) if valid else None for field in METRICS}
        month_rows.append(
            {
                "month": month,
                "basis": "server_month_summary",
                **values,
                "energy_intensity_wh_per_km": energy_statistics(
                    values["distance_km"], values["energy_wh"], basis="server_month_summary"
                )["energy_intensity_wh_per_km"],
                "received_at": record.received_at if record else None,
                "revision": record.revision if record else None,
                "backend_version": record.backend_version if record else None,
                "period_closed": last_day < today,
                "observed_after_period_end": observed_after_end,
                "list_complete": record.list_complete if valid and record else None,
                "availability": {
                    field: "available"
                    if value is not None
                    else ("not_reported" if valid else "missing_month")
                    for field, value in values.items()
                },
            }
        )
        if include_daily:
            for day_number in range(1, last_day.day + 1):
                day = date(year, month_number, day_number)
                if day > today:
                    break  # Future chart padding is not a known zero.
                summary = day_summary(store, sn, day, now)
                day_rows.append(
                    {
                        "date": day.isoformat(),
                        **{str(field): getattr(summary, field) for field in DAY_METRICS},
                        "distance_basis": summary.distance_basis,
                        "ride_basis": "returned_unique_rides",
                        "received_at": summary.received_at,
                        "revision": summary.revision,
                        "adjacent_received_at": summary.adjacent_received_at,
                        "adjacent_revision": summary.adjacent_revision,
                        "ride_window_complete": summary.rides_complete,
                        "availability": {field: summary.reason(field) for field in DAY_METRICS},
                        "period_closed": day < today,
                    }
                )
    totals = {field: finite_sum([row[field] for row in month_rows]) for field in METRICS}
    return {
        "schema_version": 1,
        "source": "ninecli",
        "source_mode": "bounded_statistics_ledger",
        "business_timezone": BUSINESS_TIMEZONE,
        "date_assignment": "whole_ride_end_business_date",
        "scope": {"start_month": months[0], "end_month": months[-1], "month_count": len(months)},
        "units": {"distance_km": "km", "energy_wh": "Wh", "duration_s": "s", "ride_count": None},
        "summary": {
            "basis": "server_month_summary",
            **totals,
            "energy_intensity_wh_per_km": energy_statistics(
                totals["distance_km"], totals["energy_wh"], basis="server_month_summary"
            )["energy_intensity_wh_per_km"],
            "complete_by_metric": {field: totals[field] is not None for field in METRICS},
            "observed_after_all_period_ends": all(
                row["observed_after_period_end"] for row in month_rows
            ),
            "missing_months": [row["month"] for row in month_rows if row["received_at"] is None],
        },
        "months": month_rows,
        "days": day_rows,
        "warnings": [
            "upstream_pagination_unverified",
            "no_hourly_distribution",
            "server_daily_distance_and_ride_end_date_can_differ",
        ],
    }


async def async_statistics_query(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    entry, sn = resolve_vehicle(hass, call.data["device_id"])
    co = entry.runtime_data.coordinator
    months = months_between(call.data["start_month"], call.data["end_month"])
    if not co.statistics.available:
        raise validation_error("statistics_unavailable")
    if call.data["refresh"]:
        try:
            for month in months:
                record = await co.async_query_month(sn, month)
                await co.statistics.async_record(
                    sn, month_data(record), record.received_at, record.backend_version
                )
        except NinebotError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=err.kind.value
            ) from err
    # The executor never sees dictionaries changing midway through its read.
    view = copy(co.statistics)
    view.months = {key: dict(values) for key, values in co.statistics.months.items()}
    view.rides = {key: dict(values) for key, values in co.statistics.rides.items()}
    response = await hass.async_add_executor_job(
        partial(statistics_response, view, sn, months, dt_util.utcnow(), call.data["include_daily"])
    )
    assert_query_scope(hass, call.data["device_id"], entry.entry_id, sn, False)
    if call.data["refresh"]:
        # Stored day projections may change; the current travel snapshot and its
        # successful-sample clock remain intact, so this cannot replay an event.
        co.async_update_listeners()
    response["query"] = {
        "refresh_requested": call.data["refresh"],
        "max_month_queries": len(months) if call.data["refresh"] else 0,
    }
    return response
