"""Six bounded local queries shared by native tools; no model or cloud transport."""

import asyncio
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .assistant import CONF_ASSISTANT
from .assistant_policy import AssistantError, AssistantScope, async_authorize, async_require_admin
from .assistant_views import (
    AssistantView,
    detail_view,
    mapping,
    ride_view,
    safe_label,
    statistics_view,
    status_view,
    sync_view,
    trips_view,
)
from .compat import validation as vol
from .const import BUSINESS_TIMEZONE, DOMAIN
from .history_actions import cursor_value
from .recorded_actions import business_date
from .services import bounded_integer, query_month, ride_identifier

MAX_RESPONSE_BYTES = 16 * 1024
MAX_RIDE_BYTES = 2 * 1024
MAX_VEHICLES = 20


def vehicle_reference(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.isascii():
        raise vol.Invalid("Use a vehicle_ref from get_vehicles")
    if not value.isprintable():
        raise vol.Invalid("Invalid vehicle reference")
    return value


@dataclass(frozen=True)
class ToolSpec:
    key: str
    title: str
    description: str
    parameters: Any
    idempotent: bool = True


VEHICLE = {vol.Required("vehicle_ref"): vehicle_reference}
SPECS = (
    ToolSpec(
        "get_vehicles",
        "List Ninebot vehicles",
        "List vehicles explicitly allowed for AI. Ask the user to select when ambiguous.",
        vol.Schema({}),
    ),
    ToolSpec(
        "get_vehicle_status",
        "Read cached vehicle status",
        "Read local status with observation times. Does not refresh the cloud or return GPS.",
        vol.Schema(VEHICLE),
    ),
    ToolSpec(
        "get_statistics",
        "Read local trip statistics",
        "Read an explicit business period. Missing or partial metrics are not zero.",
        vol.Schema(
            {
                **VEHICLE,
                vol.Required("period"): vol.In(("today", "yesterday", "this_month", "last_month")),
            }
        ),
    ),
    ToolSpec(
        "get_recorded_trips",
        "List recorded trips",
        "Read one local date page, at most 31 days. A page is not a period total. "
        "Use returned ride_id and query_month for details; cursors may be consumed.",
        vol.Schema(
            {
                **VEHICLE,
                vol.Required("start_date"): business_date,
                vol.Required("end_date"): business_date,
                vol.Optional("limit", default=10): partial(bounded_integer, maximum=20),
                vol.Optional("cursor"): cursor_value,
            }
        ),
        idempotent=False,
    ),
    ToolSpec(
        "get_trip_detail",
        "Read a saved trip detail",
        "Read locally saved scalar detail using one ride_id/query_month from the trip list. "
        "Never fetches missing cloud details or returns tracks or sample arrays.",
        vol.Schema(
            {
                **VEHICLE,
                vol.Required("ride_id"): ride_identifier,
                vol.Required("query_month"): query_month,
            }
        ),
    ),
    ToolSpec(
        "get_sync_health",
        "Read history synchronization health",
        "Read current local history coverage and scheduling. Does not start or resume work.",
        vol.Schema(VEHICLE),
    ),
)
TOOL_SPECS = {spec.key: spec for spec in SPECS}


def parameter_schema(value: object) -> dict[str, Any] | None:
    """Publish constraints for our validators; execution still validates the Schema."""
    if value is vehicle_reference:
        return {"type": "string", "minLength": 1, "maxLength": 128}
    if value is business_date:
        return {"type": "string", "format": "date"}
    if value is query_month:
        return {"type": "string", "pattern": "^20[0-9]{2}(0[1-9]|1[0-2])$"}
    if value is ride_identifier:
        return {"type": "string", "minLength": 1, "maxLength": 256}
    if value is cursor_value:
        return {"type": "string", "pattern": "^[0-9a-f]{32}$"}
    if isinstance(value, partial) and value.func is bounded_integer:
        return {"type": "integer", "minimum": 1, "maximum": value.keywords["maximum"]}
    return None


def period_dates(period: str, now: datetime) -> tuple[date, date]:
    today = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date()
    if period == "today":
        return today, today
    if period == "yesterday":
        return today - timedelta(days=1), today - timedelta(days=1)
    if period == "this_month":
        return today.replace(day=1), today
    if period == "last_month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    raise AssistantError("invalid_arguments")


async def _action(
    hass: HomeAssistant, scope: AssistantScope, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    response = await hass.services.async_call(
        DOMAIN,
        name,
        {"device_id": scope.vehicle_ref, **arguments},
        blocking=True,
        return_response=True,
        context=scope.context,
    )
    if not isinstance(response, dict):
        raise AssistantError("local_data_missing")
    return response


async def _vehicles(
    hass: HomeAssistant, context: Context | None
) -> tuple[AssistantView, list[AssistantScope]]:
    scopes: list[AssistantScope] = []
    rows: list[dict[str, Any]] = []
    registry = dr.async_get(hass)
    candidates = {
        device.id: device
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.options.get(CONF_ASSISTANT) is True
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
    }
    for device in sorted(candidates.values(), key=lambda row: row.id):
        if not any(domain == DOMAIN for domain, _ in device.identifiers):
            continue
        try:
            scope = await async_authorize(hass, context, device.id)
        except AssistantError:
            continue
        scopes.append(scope)
        snapshot = scope.runtime.coordinator.data[scope.sn]
        rows.append(
            {
                "vehicle_ref": device.id,
                "label": safe_label(
                    device.name_by_user or snapshot.profile.name,
                    scope.sn,
                    str(scope.entry.data.get("account", "")),
                ),
                "capabilities": {
                    "cached_status": snapshot.status_freshness.succeeded_at is not None,
                    "local_history": scope.runtime.coordinator.statistics.archive_ready,
                },
            }
        )
        if len(rows) > MAX_VEHICLES:
            break
    if not scopes:
        raise AssistantError("not_authorized")
    return AssistantView(
        data={"vehicles": rows[:MAX_VEHICLES], "more_available": len(rows) > MAX_VEHICLES},
        evidence={"source_kind": "runtime_snapshot"},
        warnings=["narrow_the_account_ai_selection"] if len(rows) > MAX_VEHICLES else [],
    ), scopes


async def _vehicle_query(
    hass: HomeAssistant, scope: AssistantScope, key: str, args: dict[str, Any], now: datetime
) -> tuple[AssistantView, dict[str, Any]]:
    query: dict[str, Any] = {}
    if key == "get_vehicle_status":
        co = scope.runtime.coordinator
        return status_view(
            co.data[scope.sn],
            {group: co.fresh(scope.sn, group) for group in ("profile", "status", "battery")},
        ), query
    if key == "get_statistics":
        start, end = period_dates(args["period"], now)
        daily = args["period"] in {"today", "yesterday"}
        query = {
            "period": args["period"],
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        response = await _action(
            hass,
            scope,
            "get_statistics",
            {
                "start_month": start.strftime("%Y%m"),
                "end_month": end.strftime("%Y%m"),
                "include_daily": daily,
                "refresh": False,
            },
        )
        rows = response.get("days" if daily else "months", [])
        if not isinstance(rows, list):
            raise AssistantError("unsupported_capability")
        target_key, target = (
            ("date", start.isoformat()) if daily else ("month", start.strftime("%Y%m"))
        )
        row = next(
            (row for row in rows if isinstance(row, dict) and row.get(target_key) == target), {}
        )
        return statistics_view(row, daily=daily, now=now), query
    if key == "get_recorded_trips":
        if (
            not 1
            <= (date.fromisoformat(args["end_date"]) - date.fromisoformat(args["start_date"])).days
            + 1
            <= 31
        ):
            raise AssistantError("invalid_arguments")
        data: dict[str, Any] = {
            name: args[name]
            for name in ("start_date", "end_date", "limit", "cursor")
            if name in args
        }
        response = await _action(hass, scope, "get_recorded_trips", {**data, "sort": "desc"})
        rides = response.get("rides")
        if not isinstance(rides, list) or any(not isinstance(row, dict) for row in rides):
            raise AssistantError("unsupported_capability")
        if len(rides) > args["limit"]:
            raise AssistantError("response_too_large")
        if response.get("next_cursor") is not None:
            cursor_value(response["next_cursor"])
        for ride in rides:
            if len(json.dumps(ride_view(ride), ensure_ascii=False).encode()) > MAX_RIDE_BYTES:
                raise AssistantError("response_too_large")
        return trips_view(response, rides), {**data, "sort": "desc"}
    if key == "get_trip_detail":
        response = await _action(
            hass,
            scope,
            "get_trip_detail",
            {
                "ride_id": args["ride_id"],
                "query_month": args["query_month"],
                "local_only": True,
                "include_track": False,
                "refresh": False,
            },
        )
        ride = mapping(response.get("ride"))
        if (
            response.get("source_mode") != "ride_archive"
            or ride.get("ride_id") != args["ride_id"]
            or ride.get("query_month") != args["query_month"]
        ):
            raise AssistantError("local_data_missing")
        return detail_view(response), {
            "ride_id": args["ride_id"],
            "query_month": args["query_month"],
        }
    if key == "get_sync_health":
        response = await _action(hass, scope, "sync_history", {"operation": "sync_status"})
        return sync_view(response), query
    raise AssistantError("unsupported_capability")


async def async_query(
    hass: HomeAssistant, key: str, arguments: dict[str, Any], context: Context | None
) -> tuple[dict[str, Any], bool]:
    """Authenticate, validate, read, reauthorize and bound every native tool call."""
    request_id, now = uuid4().hex, dt_util.utcnow()
    try:
        async with asyncio.timeout(10):
            await async_require_admin(hass, context)
            if key not in TOOL_SPECS:
                raise AssistantError("invalid_arguments")
            args = TOOL_SPECS[key].parameters(arguments)
            query: dict[str, Any]
            if key == "get_vehicles":
                view, scopes = await _vehicles(hass, context)
                query = {}
            else:
                scope = await async_authorize(hass, context, args["vehicle_ref"])
                scopes = [scope]
                view, query = await _vehicle_query(hass, scope, key, args, now)
            for scope in scopes:
                await scope.async_validate(hass)
            # A later vehicle's await may revoke an earlier one. No further
            # await occurs between the final complete scope check and return.
            for scope in scopes:
                scope.assert_current(hass)
            response = {
                "schema_version": 1,
                "request_id": request_id,
                **({"vehicle_ref": args["vehicle_ref"]} if key != "get_vehicles" else {}),
                "query": {
                    "as_of": now.isoformat(),
                    "business_timezone": BUSINESS_TIMEZONE,
                    **query,
                },
                "data": view.data,
                "quality": view.quality,
                "evidence": {
                    "adapter_contract": "ninebot-assistant-v1",
                    "access_mode": "local_only",
                    **view.evidence,
                },
                "warnings": view.warnings,
            }
            if (
                len(json.dumps(response, ensure_ascii=False, allow_nan=False).encode())
                > MAX_RESPONSE_BYTES
            ):
                raise AssistantError("response_too_large")
            return response, False
    except Unauthorized:
        code = "not_authorized"
    except vol.Invalid:
        code = "invalid_arguments"
    except AssistantError as err:
        code = err.code
    except TimeoutError:
        code = "timeout"
    except HomeAssistantError as err:
        code = {
            "query_device": "not_authorized",
            "query_coordinates": "not_authorized",
            "history_cursor": "retry_later",
            "auth": "auth_required",
            "busy": "retry_later",
            "query_unavailable": "retry_later",
            "query_detail_not_recorded": "local_data_missing",
            "query_ride": "local_data_missing",
            "statistics_unavailable": "local_data_missing",
            "calendar_unavailable": "local_data_missing",
        }.get(err.translation_key or "", "unsupported_capability")
    return {"schema_version": 1, "request_id": request_id, "error": {"code": code}}, True
