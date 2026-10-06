"""Explicit cross-month queries with bounded continuations and truthful scope."""

import math
import re
from dataclasses import replace
from datetime import timedelta
from functools import partial
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util

from .compat import validation as vol
from .exceptions import NinebotError
from .history import MAX_HISTORY_IDS, HistoryState, HistorySummary
from .models import TravelMonth
from .parsing import previous_month
from .services import (
    assert_query_scope,
    bounded_integer,
    iso,
    month_data,
    month_response,
    query_month,
    resolve_vehicle,
    ride_response,
    validation_error,
)
from .travel_statistics import energy_statistics, ride_signature


def cursor_value(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise vol.Invalid("Invalid history cursor")
    return value


HISTORY_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): str,
        vol.Required("start_month"): query_month,
        vol.Required("end_month"): query_month,
        vol.Optional("cursor"): cursor_value,
        vol.Optional("max_months_per_call", default=3): partial(bounded_integer, maximum=6),
        vol.Optional("limit", default=100): partial(bounded_integer, maximum=100),
        vol.Optional("include_daily_chart", default=True): bool,
    }
)


def merge_month(state: HistoryState, travel: TravelMonth) -> HistoryState:
    """CPU-only normalization merge; never substitute sampled rides for totals."""
    summary = travel.summary
    assert summary is not None
    seen = dict(state.seen)
    pending = list(state.pending)
    warnings = set(state.warnings)
    stopped = None
    indexed_totals = state.indexed_totals
    identity_complete = state.indexed_identity_complete
    for ride in travel.rides:
        if ride.ride_id is None:
            identity_complete = False
            warnings.add("ride_identity_incomplete")
            continue
        fingerprint = ride_signature(ride)
        if ride.ride_id in seen:
            if seen[ride.ride_id] != fingerprint:
                identity_complete = False
                warnings.add("conflicting_ride_id")
            continue
        if len(seen) >= MAX_HISTORY_IDS:
            stopped = "index_budget"
            warnings.add("history_index_truncated")
            break
        seen[ride.ride_id] = fingerprint
        pending.append(ride)
        distance_sum, energy_sum = (
            a + b if a is not None and b is not None and math.isfinite(a + b) else None
            for a, b in zip(indexed_totals, (ride.distance_m, ride.energy_raw), strict=True)
        )
        indexed_totals = distance_sum, energy_sum
    incoming = (summary.mileage_km, summary.energy_wh, summary.ride_count, summary.duration_s)
    totals = tuple(
        a + b if a is not None and b is not None and math.isfinite(a + b) else None
        for a, b in zip(state.totals, incoming, strict=True)
    )
    incomplete = state.incomplete_months + (
        (travel.month,) if summary.list_complete is False else ()
    )
    unknown = state.unknown_months + ((travel.month,) if summary.list_complete is None else ())
    next_month = (
        None if stopped or travel.month == state.start_month else previous_month(travel.month)
    )
    return replace(
        state,
        next_month=next_month,
        scanned_months=(*state.scanned_months, travel.month),
        incomplete_months=incomplete,
        unknown_months=unknown,
        totals=totals,
        seen=seen,
        pending=tuple(pending),
        warnings=tuple(sorted(warnings)),
        stopped_reason=stopped,
        indexed_totals=indexed_totals,
        indexed_identity_complete=identity_complete,
    )


async def async_history_query(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    entry, sn = resolve_vehicle(hass, call.data["device_id"])
    co = entry.runtime_data.coordinator
    store = co.history
    start, end = call.data["start_month"], call.data["end_month"]
    month_count = (int(end[:4]) - int(start[:4])) * 12 + int(end[4:]) - int(start[4:]) + 1
    if not 1 <= month_count <= 360:
        raise validation_error("history_range")
    if cursor := call.data.get("cursor"):
        state = store.get(cursor, dt_util.utcnow())
        if (
            state is None
            or state.entry_id != entry.entry_id
            or state.vehicle != sn
            or (state.start_month, state.end_month) != (start, end)
        ):
            raise validation_error("history_cursor")
    else:
        state = HistoryState(entry.entry_id, sn, start, end, end)
    months = []
    error = None
    retry_after = None
    # Drain previously indexed pages before making further requests. A small
    # limit cannot silently drop rides from a large month response.
    if not state.pending and state.stopped_reason is None:
        for _ in range(call.data["max_months_per_call"]):
            month = state.next_month
            if month is None:
                break
            store.expire(dt_util.utcnow())
            retry_at = store.retry_at.get((sn, month))
            if retry_at:
                retry_after = max(1, int((retry_at - dt_util.utcnow()).total_seconds()))
                error = {"month": month, "kind": "retry_cooldown"}
                break
            try:
                record = await co.async_query_month(sn, month)
                travel = await hass.async_add_executor_job(month_data, record)
            except NinebotError as err:
                store.retry_at[(sn, month)] = dt_util.utcnow() + timedelta(seconds=60)
                retry_after = 60
                error = {"month": month, "kind": err.kind.value}
                break
            state = await hass.async_add_executor_job(merge_month, state, travel)
            response = {
                "month": month,
                "received_at": iso(record.received_at),
                **month_response(travel),
            }
            if not call.data["include_daily_chart"]:
                response.pop("daily_mileage")
            months.append(response)
            if state.stopped_reason:
                break
    limit = call.data["limit"]
    rides, pending = state.pending[:limit], state.pending[limit:]
    state = replace(state, pending=pending)
    assert_query_scope(hass, call.data["device_id"], entry.entry_id, sn, False)
    needs_more = bool(pending) or (state.next_month is not None and state.stopped_reason is None)
    next_cursor = store.put(state, dt_util.utcnow()) if needs_more else None
    if needs_more and next_cursor is None:
        state = replace(
            state,
            stopped_reason="continuation_budget",
            warnings=(*state.warnings, "history_continuation_truncated"),
        )
    store.summary[sn] = HistorySummary.from_state(state)
    co.async_update_listeners()
    return {
        "schema_version": 1,
        "source": "ninecli",
        "range": {"start_month": start, "end_month": end},
        "months": months,
        "rides": [ride_response(ride) for ride in rides],
        "next_cursor": next_cursor,
        "pagination": {
            "returned": len(rides),
            "indexed_pending": len(pending),
            "has_more": bool(next_cursor),
        },
        "coverage": {
            "scanned_months": list(state.scanned_months),
            "range_scan_complete": state.scan_complete,
            "all_rides_complete": state.rides_complete,
            "indexed_unique_count": len(state.seen),
            "incomplete_months": list(state.incomplete_months),
            "unknown_months": list(state.unknown_months),
        },
        "scanned_months_totals": {
            "mileage_km": state.totals[0] if state.scanned_months else None,
            "energy_wh": state.totals[1] if state.scanned_months else None,
            "reported_ride_count": state.totals[2] if state.scanned_months else None,
            "duration_s": state.totals[3] if state.scanned_months else None,
            "basis": "server_month_summary",
        },
        "statistics": {
            "server_scanned_months": {
                **energy_statistics(
                    state.totals[0] if state.scanned_months else None,
                    state.totals[1] if state.scanned_months else None,
                    basis="server_scanned_month_summaries",
                ),
                "scanned_month_count": len(state.scanned_months),
                "range_scan_complete": state.scan_complete,
            },
            "indexed_rides": {
                **energy_statistics(
                    state.indexed_totals[0] / 1000
                    if state.scanned_months
                    and state.indexed_identity_complete
                    and state.indexed_totals[0] is not None
                    else None,
                    state.indexed_totals[1]
                    if state.scanned_months and state.indexed_identity_complete
                    else None,
                    basis="indexed_unique_rides",
                ),
                "unique_ride_count": len(state.seen),
                "identity_complete": state.indexed_identity_complete,
                "all_rides_complete": state.rides_complete,
                "reported_ride_count": state.totals[2] if state.scanned_months else None,
                "coverage_fraction": (
                    len(state.seen) / state.totals[2]
                    if state.indexed_identity_complete
                    and state.totals[2] is not None
                    and state.totals[2] > 0
                    and len(state.seen) <= state.totals[2]
                    else None
                ),
            },
        },
        "warnings": list(state.warnings),
        "stopped_reason": state.stopped_reason,
        "error": error,
        "retry_after_s": retry_after,
        "storage": "runtime_only",
    }
