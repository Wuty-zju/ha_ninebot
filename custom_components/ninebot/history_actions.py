"""Explicit cross-month queries with bounded continuations and truthful scope."""

import hashlib
import json
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
    for ride in travel.rides:
        if ride.ride_id is None:
            warnings.add("ride_identity_incomplete")
            continue
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    iso(ride.started_at),
                    iso(ride.ended_at),
                    ride.distance_m,
                    ride.duration_s,
                    ride.energy_raw,
                    ride.server_max_speed_m_s,
                ]
            ).encode()
        ).hexdigest()
        if ride.ride_id in seen:
            if seen[ride.ride_id] != fingerprint:
                warnings.add("conflicting_ride_id")
            continue
        if len(seen) >= MAX_HISTORY_IDS:
            stopped = "index_budget"
            warnings.add("history_index_truncated")
            break
        seen[ride.ride_id] = fingerprint
        pending.append(ride)
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
        "warnings": list(state.warnings),
        "stopped_reason": state.stopped_reason,
        "error": error,
        "retry_after_s": retry_after,
        "storage": "runtime_only",
    }
