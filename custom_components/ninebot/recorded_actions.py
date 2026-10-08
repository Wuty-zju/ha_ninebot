"""Read-only local date pages; opening a browser never schedules cloud queries."""

import re
from datetime import date, datetime, time, timedelta
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util

from .archive_runtime import ArchiveStatistics
from .archive_timeline import MAX_TIMELINE_DAYS, SELECTION_BASIS
from .compat import validation as vol
from .const import BUSINESS_TIMEZONE
from .history_actions import cursor_value
from .ride_archive import ArchiveError, ArchiveFailure
from .services import (
    assert_query_scope,
    async_check_history_access,
    bounded_integer,
    iso,
    resolve_vehicle,
    ride_response,
    validation_error,
)


def business_date(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", value) is None:
        raise vol.Invalid("Use YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as err:
        raise vol.Invalid("Invalid date") from err
    return value


RECORDED_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): str,
        vol.Required("start_date"): business_date,
        vol.Required("end_date"): business_date,
        vol.Optional("limit", default=20): partial(bounded_integer, maximum=100),
        vol.Optional("cursor"): cursor_value,
    }
)


async def async_recorded_query(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    entry, sn = resolve_vehicle(hass, call.data["device_id"], require_live=False)
    co = entry.runtime_data.coordinator
    generation, ownership = co._generation, co._ownership.get(sn, 0)
    await async_check_history_access(hass, call, entry.entry_id)
    statistics = co.statistics
    if not isinstance(statistics, ArchiveStatistics) or not statistics.archive_ready:
        raise validation_error("calendar_unavailable")
    start_date, end_date = (
        date.fromisoformat(call.data[key]) for key in ("start_date", "end_date")
    )
    days = (end_date - start_date).days + 1
    if not 1 <= days <= MAX_TIMELINE_DAYS:
        raise validation_error("recorded_range")
    zone = ZoneInfo(BUSINESS_TIMEZONE)
    start = datetime.combine(start_date, time(), zone)
    end = datetime.combine(end_date + timedelta(days=1), time(), zone)
    now = dt_util.utcnow()
    cursor = None
    if token := call.data.get("cursor"):
        cursor = co.recorded_cursors.get(token, now)
        if cursor is None:
            raise validation_error("history_cursor")
    try:
        page = await statistics.archive.async_recorded_page(
            sn, start, end, now, limit=call.data["limit"], cursor=cursor
        )
    except ValueError:
        raise validation_error("recorded_range") from None
    except ArchiveError as err:
        if err.kind is ArchiveFailure.CURSOR:
            raise validation_error("history_cursor") from None
        statistics.record_error(err)
        raise validation_error(
            "recorded_range" if err.kind is ArchiveFailure.BUDGET else "calendar_unavailable"
        ) from None
    await async_check_history_access(hass, call, entry.entry_id)
    assert_query_scope(hass, call.data["device_id"], entry.entry_id, sn, False, require_live=False)
    if generation != co._generation or ownership != co._ownership.get(sn, 0):
        raise validation_error("query_unavailable")
    months = []
    month = start_date.replace(day=1)
    while month <= end_date:
        months.append(month.strftime("%Y%m"))
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    stored = {row.month for row in page.months}
    missing = [month for month in months if month not in stored]
    partial_months = [row.month for row in page.months if row.list_complete is False]
    unknown_months = [row.month for row in page.months if row.list_complete is None]
    next_cursor = co.recorded_cursors.put(page.next_cursor, now) if page.next_cursor else None
    return {
        "schema_version": 1,
        "source_mode": "ride_archive",
        "revision": page.revision,
        "range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "time_zone": BUSINESS_TIMEZONE,
            "as_of": cursor.as_of if cursor else iso(now),
        },
        "rides": [
            {**ride_response(row.ride), "received_at": iso(row.received_at)} for row in page.records
        ],
        "months": [
            {
                "month": row.month,
                "received_at": iso(row.received_at),
                "observed_list_complete": row.list_complete,
                "reported_count": row.reported_count,
                "returned_count": row.returned_count,
            }
            for row in page.months
        ],
        "next_cursor": next_cursor,
        "pagination": {"returned": len(page.records), "has_more": next_cursor is not None},
        "coverage": {
            "selection_basis": SELECTION_BASIS,
            "requested_months": months,
            "stored_months": sorted(stored),
            "missing_months": missing,
            "partial_months": partial_months,
            "unknown_months": unknown_months,
            "all_observed_lists_complete": not (missing or partial_months or unknown_months),
            "upstream_history_complete": "unverified",
            "future_and_ongoing_excluded": True,
            "requires_valid_start_end": True,
            "skipped_conflicting_times_this_page": page.skipped_conflicting_times,
        },
    }
