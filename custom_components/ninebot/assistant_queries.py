"""Bounded local queries and public help shared by native tools; no model or cloud transport."""

import asyncio
import json
import re
from collections.abc import Iterable, Iterator
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .assistant import current_access, tasks_ready
from .assistant_catalog import BY_KEY, QUERY_SPECS, TASK_SPECS
from .assistant_errors import TOOL_ERRORS, TOOL_TIMEOUT, checked_result, tool_failure
from .assistant_knowledge import KnowledgeUnavailable, search_help
from .assistant_limits import ReadLimitExceeded, read_slot
from .assistant_policy import (
    AssistantError,
    AssistantScope,
    async_authorize,
    async_require_admin,
    operation_allowed,
)
from .assistant_views import (
    STATUS_READINGS,
    AssistantView,
    detail_view,
    mapping,
    safe_label,
    statistics_period_complete,
    statistics_view,
    status_view,
    sync_view,
    trips_view,
)
from .backup import backup_active
from .compat import validation as vol
from .const import BUSINESS_TIMEZONE, CONF_ACCOUNT, CONF_BUSINESS_UID, CONF_SESSION_KEY, DOMAIN
from .history_actions import cursor_value
from .history_budget import configured_budget, request_interval
from .identity import device_sn
from .recorded_actions import business_date

MAX_RIDE_BYTES = 2 * 1024
MAX_VEHICLES = 20
TOOL_SPECS = {spec.key: spec for spec in QUERY_SPECS}


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
    if re.fullmatch(r"20[0-9]{2}-(0[1-9]|1[0-2])", period):
        start = date.fromisoformat(f"{period}-01")
        if start > today:
            raise AssistantError("invalid_arguments")
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, min(today, next_month - timedelta(days=1))
    try:
        selected = date.fromisoformat(business_date(period))
    except vol.Invalid as err:
        raise AssistantError("invalid_arguments") from err
    if selected > today:
        raise AssistantError("invalid_arguments")
    return selected, selected


async def _action(
    hass: HomeAssistant,
    scope: AssistantScope,
    name: str,
    arguments: dict[str, Any],
    *,
    retry_busy: bool = False,
) -> tuple[dict[str, Any], int]:
    for attempt in (1, 2):
        try:
            response = await hass.services.async_call(
                DOMAIN,
                name,
                {"device_id": scope.vehicle_ref, **arguments},
                blocking=True,
                return_response=True,
                context=scope.context,
            )
            break
        except HomeAssistantError as err:
            if (
                attempt != 1
                or not retry_busy
                or err.translation_domain != DOMAIN
                or err.translation_key != "busy"
            ):
                raise
            await scope.async_validate(hass)
    if not isinstance(response, dict):
        raise AssistantError("local_data_missing")
    return response, attempt


def _vehicle_refs(hass: HomeAssistant, operation: str | None = None) -> Iterator[str]:
    registry = dr.async_get(hass)
    for entry in hass.config_entries.async_entries(DOMAIN):
        access = current_access(entry)
        if not access.enabled or not access.vehicles:
            continue
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            if (sn := device_sn(entry, device)) is not None and operation_allowed(
                access, sn, operation
            ):
                yield device.id


async def async_vehicle_scopes(
    hass: HomeAssistant, context: Context | None, *, limit: int, operation: str = "get_vehicles"
) -> list[AssistantScope]:
    """Use stable ordering for paged discovery; help needs only one witness."""
    candidates: Iterable[str] = _vehicle_refs(hass, operation)
    if limit > 1:
        candidates = sorted(set(candidates))
    scopes: list[AssistantScope] = []
    for device_id in candidates:
        try:
            scope = await async_authorize(hass, context, device_id, operation=operation)
        except AssistantError:
            continue
        scopes.append(scope)
        if len(scopes) == limit:
            break
    if not scopes:
        raise AssistantError("not_authorized")
    return scopes


async def async_visible_keys(
    hass: HomeAssistant,
    context: Context | None,
    *,
    vehicle_ref: str | None = None,
    queries_only: bool = False,
) -> frozenset[str]:
    """Advertise the union of authorized operations; calls always recheck their target."""
    await async_require_admin(hass, context)
    include_tasks = not queries_only and tasks_ready(hass) and not backup_active(hass)
    specs = (*QUERY_SPECS, *TASK_SPECS) if include_tasks else QUERY_SPECS
    witnesses: dict[str, AssistantScope] = {}
    targets = (vehicle_ref,) if vehicle_ref is not None else _vehicle_refs(hass)
    for target in targets:
        try:
            scope = await async_authorize(hass, context, target)
        except AssistantError:
            continue
        access = current_access(scope.entry)
        for spec in specs:
            if spec.key not in witnesses and operation_allowed(access, scope.sn, spec.key):
                witnesses[spec.key] = scope
        if len(witnesses) == len(specs):
            break
    await async_require_admin(hass, context)
    task_access = include_tasks and tasks_ready(hass) and not backup_active(hass)
    visible: set[str] = set()
    for key, scope in witnesses.items():
        try:
            scope.assert_current(hass)
        except AssistantError:
            continue
        if not BY_KEY[key].task or task_access:
            visible.add(key)
    return frozenset(visible)


def vehicle_list_view(hass: HomeAssistant, scopes: list[AssistantScope]) -> AssistantView:
    registry = dr.async_get(hass)
    rows: list[dict[str, Any]] = []
    for scope in scopes[:MAX_VEHICLES]:
        device = registry.async_get(scope.vehicle_ref)
        if device is None:
            raise AssistantError("not_authorized")
        scope.assert_current(hass)
        snapshot = scope.runtime.coordinator.data[scope.sn]
        private_ids = [scope.sn]
        private_ids.extend(
            value
            for key in (CONF_ACCOUNT, CONF_BUSINESS_UID, CONF_SESSION_KEY)
            if isinstance(value := scope.entry.data.get(key), str)
        )
        grant = current_access(scope.entry).vehicles[scope.sn]
        capabilities = {}
        if "status" in grant.reads:
            capabilities["cached_status"] = snapshot.status_freshness.succeeded_at is not None
        if grant.reads.intersection(("statistics", "trips", "sync")):
            capabilities["local_history"] = scope.runtime.coordinator.statistics.archive_ready
        rows.append(
            {
                "vehicle_ref": device.id,
                "label": safe_label(
                    device.name_by_user or snapshot.profile.name,
                    *private_ids,
                ),
                "allowed_reads": sorted(grant.reads),
                "capabilities": capabilities,
            }
        )
    more = len(scopes) > MAX_VEHICLES
    return AssistantView(
        data={"vehicles": rows, "more_available": more},
        evidence={
            "source_kind": "runtime_snapshot",
            "capabilities_basis": "local_cache_and_archive_readiness",
        },
        warnings=["narrow_the_account_ai_selection"] if more else [],
    )


async def _vehicle_query(
    hass: HomeAssistant,
    scope: AssistantScope,
    key: str,
    args: dict[str, Any],
    now: datetime,
    period_now: datetime | None = None,
) -> tuple[AssistantView, dict[str, Any]]:
    query: dict[str, Any] = {}
    if key == "get_vehicle_status":
        co = scope.runtime.coordinator
        failure = getattr(co, "query_failure", None)
        metric = args.get("metric")
        groups = (
            ("profile", "status", "battery") if metric is None else (STATUS_READINGS[metric][2],)
        )
        return status_view(
            co.data[scope.sn],
            {group: co.fresh(scope.sn, group) for group in groups},
            {group: failure(scope.sn, group) for group in groups} if callable(failure) else None,
            metric=metric,
        ), {"metric": metric} if metric is not None else query
    if key == "get_statistics":
        start, end = period_dates(args["period"], period_now or now)
        daily = args["period"] not in {"this_month", "last_month"} and len(args["period"]) != 7
        query = {
            "period": args["period"],
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        response, attempts = await _action(
            hass,
            scope,
            "get_statistics",
            {
                "start_month": start.strftime("%Y%m"),
                "end_month": end.strftime("%Y%m"),
                "include_daily": daily,
                "refresh": False,
            },
            retry_busy=True,
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
        if not daily and period_dates(start.strftime("%Y-%m"), dt_util.utcnow())[1] != end:
            # The summary for this particular month now covers a longer window.
            raise AssistantError("retry_later")
        metric = args.get("metric")
        return statistics_view(row, daily=daily, now=now, metric=metric), {
            **query,
            **({"metric": metric} if metric is not None else {}),
            "local_action_attempts": attempts,
        }
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
        response, attempts = await _action(
            hass, scope, "get_recorded_trips", {**data, "sort": "desc"}, retry_busy=False
        )
        rides = response.get("rides")
        if not isinstance(rides, list) or any(not isinstance(row, dict) for row in rides):
            raise AssistantError("unsupported_capability")
        if len(rides) > args["limit"]:
            raise AssistantError("response_too_large")
        if response.get("next_cursor") is not None:
            cursor_value(response["next_cursor"])
        view = trips_view(response, rides)
        for ride in view.data["rides"]:
            if len(json.dumps(ride, ensure_ascii=False).encode()) > MAX_RIDE_BYTES:
                raise AssistantError("response_too_large")
        return view, {**data, "sort": "desc", "local_action_attempts": attempts}
    if key == "get_trip_detail":
        response, attempts = await _action(
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
            retry_busy=True,
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
            "local_action_attempts": attempts,
        }
    if key == "get_sync_health":
        response, attempts = await _action(
            hass, scope, "sync_history", {"operation": "sync_status"}, retry_busy=True
        )
        view = sync_view(response)
        budget = configured_budget(scope.entry.options)
        view.data["request_policy"] = {
            "configured_requests_per_hour": budget,
            "minimum_interval_s": request_interval(budget),
            "basis": "paced_account_requests",
        }
        return view, {"local_action_attempts": attempts}
    raise AssistantError("unsupported_capability")


async def _explain_statistics(
    hass: HomeAssistant, scope: AssistantScope, args: dict[str, Any], now: datetime
) -> tuple[AssistantView, dict[str, Any]]:
    start, end = period_dates(args["period"], now)
    monthly = args["period"] in {"this_month", "last_month"} or len(args["period"]) == 7
    period = start.strftime("%Y-%m") if monthly else start.isoformat()
    metric = args["metric"]
    may_read_sync = operation_allowed(current_access(scope.entry), scope.sn, "get_sync_health")
    steps: list[dict[str, Any]] = []
    help_data: dict[str, Any] = {}
    metric_verified = False
    for key, arguments in (
        ("get_statistics", {"vehicle_ref": scope.vehicle_ref, "period": period, "metric": metric}),
        ("get_sync_health", {"vehicle_ref": scope.vehicle_ref}),
        (
            "search_help",
            {
                "query": "unknown missing zero",
                "language": args["language"],
                "include_procedures": True,
            },
        ),
    ):
        if key == "get_sync_health" and not may_read_sync:
            continue
        await scope.async_validate(hass)
        result, error = await async_query(
            hass,
            key,
            arguments,
            scope.context,
            _period_now=now,
            _selected_vehicle=scope.vehicle_ref,
        )
        await scope.async_validate(hass)
        if error and result["error"]["code"] == "not_authorized":
            raise AssistantError("not_authorized")
        steps.append({"tool": f"ninebot__{key}", "error": error, "result": result})
        if error:
            if "retry_after_seconds" in result["error"]:
                break
        elif key == "get_statistics":
            quality = result["quality"]
            metric_verified = (
                quality["metrics"][metric] == {"status": "available", "reason": "available"}
                and quality["received_at"] is not None
                and statistics_period_complete(quality, result["evidence"]["source_kind"])
            )
            if metric_verified:
                break
        elif key == "search_help":
            help_data = result["data"]
    if all(step["error"] for step in steps):
        failure = steps[0]["result"]["error"]
        if "retry_after_seconds" in failure:
            raise ReadLimitExceeded(failure["retry_after_seconds"])
        raise AssistantError(failure["code"])
    statistics = steps[0]
    metric_quality = (
        statistics["result"]["quality"]["metrics"][args["metric"]]
        if not statistics["error"]
        else {"status": "unknown", "reason": "statistics_query_failed"}
    )
    help_complete = any(
        match["source_id"] == "statistics_missing" for match in help_data.get("matches", [])
    ) and any(procedure["id"] == "SK05" for procedure in help_data.get("procedures", []))
    warnings = []
    if not metric_verified:
        warnings.append("observed_conditions_do_not_establish_root_cause")
        if not may_read_sync:
            warnings.append("sync_health_outside_grant")
    return AssistantView(
        data={"metric": metric, "steps": steps},
        quality={
            "partial": not metric_verified
            and (not may_read_sync or not help_complete or any(step["error"] for step in steps)),
            "metric": metric_quality,
        },
        evidence={
            "source_kind": "local_statistics_check",
            "workflow": "statistics-check-v2",
            "outcome": "metric_verified" if metric_verified else "coverage_review",
        },
        warnings=warnings,
    ), {
        "period": args["period"],
        "resolved_period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }


async def _response(
    hass: HomeAssistant,
    scopes: list[AssistantScope],
    args: dict[str, Any],
    view: AssistantView,
    query: dict[str, Any],
    request_id: str,
    now: datetime,
) -> tuple[dict[str, Any], bool]:
    for scope in scopes:
        await scope.async_validate(hass)
    # No await between the complete scope fence and the returned projection.
    for scope in scopes:
        scope.assert_current(hass)
    response = {
        "schema_version": 1,
        "request_id": request_id,
        **({"vehicle_ref": args["vehicle_ref"]} if "vehicle_ref" in args else {}),
        "query": {"as_of": now.isoformat(), "business_timezone": BUSINESS_TIMEZONE, **query},
        "data": view.data,
        "quality": view.quality,
        "evidence": {
            "adapter_contract": "ninebot-assistant-v1",
            "access_mode": "local_only",
            **view.evidence,
        },
        "warnings": view.warnings,
    }
    return checked_result(response)


SERVICE_ERRORS = {
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
}


async def async_query(
    hass: HomeAssistant,
    key: str,
    arguments: dict[str, Any],
    context: Context | None,
    *,
    _period_now: datetime | None = None,
    _selected_vehicle: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Authenticate, validate, read, reauthorize and bound every native tool call."""
    request_id, now = uuid4().hex, dt_util.utcnow()
    try:
        async with asyncio.timeout(TOOL_TIMEOUT):
            user_id = await async_require_admin(hass, context)
            if (
                _selected_vehicle is not None
                and arguments.get("vehicle_ref", _selected_vehicle) != _selected_vehicle
            ):
                raise AssistantError("not_authorized")
            query: dict[str, Any]
            if key == "explain_statistics":
                with read_slot(hass, user_id):
                    args = TOOL_SPECS[key].parameters(arguments)
                    scope = await async_authorize(hass, context, args["vehicle_ref"], operation=key)
                # Child reads acquire their own slots; never nest active admissions.
                view, query = await _explain_statistics(hass, scope, args, _period_now or now)
                return await _response(hass, [scope], args, view, query, request_id, now)
            with read_slot(hass, user_id):
                if key not in TOOL_SPECS:
                    raise AssistantError("invalid_arguments")
                args = TOOL_SPECS[key].parameters(arguments)
                if key in {"get_vehicles", "search_help"}:
                    if _selected_vehicle is not None:
                        scopes = [
                            await async_authorize(hass, context, _selected_vehicle, operation=key)
                        ]
                    else:
                        scopes = await async_vehicle_scopes(
                            hass,
                            context,
                            operation=key,
                            limit=1 if key == "search_help" else MAX_VEHICLES + 1,
                        )
                    query = {}
                    if key == "get_vehicles":
                        view = vehicle_list_view(hass, scopes)
                    else:
                        integration = await async_get_integration(hass, DOMAIN)
                        try:
                            help_data = await hass.async_add_executor_job(
                                partial(
                                    search_help,
                                    args["query"],
                                    args["language"],
                                    str(integration.version),
                                    include_procedures=args["include_procedures"],
                                )
                            )
                        except KnowledgeUnavailable as err:
                            raise AssistantError("unsupported_capability") from err
                        view = AssistantView(
                            data=help_data,
                            evidence={"source_kind": "bundled_public_help"},
                            warnings=["documentation_is_not_vehicle_evidence"],
                        )
                else:
                    scope = await async_authorize(hass, context, args["vehicle_ref"], operation=key)
                    scopes = [scope]
                    view, query = await _vehicle_query(hass, scope, key, args, now, _period_now)
                return await _response(hass, scopes, args, view, query, request_id, now)
    except TOOL_ERRORS as err:
        return tool_failure(request_id, err, service_errors=SERVICE_ERRORS)
