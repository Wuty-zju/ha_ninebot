"""Local task tools: preview, apply and inspect, with no approval or cloud execution."""

import asyncio
from typing import Any
from uuid import uuid4

from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util

from .assistant import tasks_ready
from .assistant_catalog import REQUEST_TOOL, TASK_SPECS
from .assistant_errors import TOOL_ERRORS, TOOL_TIMEOUT, checked_result, tool_failure
from .assistant_limits import read_slot
from .assistant_plans import async_detail_proposal
from .assistant_policy import (
    REVIEW_TASKS,
    AssistantError,
    AssistantScope,
    async_authorize,
    async_require_admin,
)
from .assistant_queries import (
    MAX_VEHICLES,
    async_vehicle_scopes,
    vehicle_list_view,
)
from .assistant_task_store import ApprovalStore, TaskStoreUnavailable
from .assistant_task_views import ticket_view
from .assistant_tasks import assert_task_owner, async_request_details, task_owner
from .const import BUSINESS_TIMEZONE
from .ride_archive import ArchiveError

SPEC_BY_KEY = {spec.key: spec for spec in TASK_SPECS}


async def async_task_query(
    hass: HomeAssistant,
    key: str,
    arguments: dict[str, Any],
    context: Context | None,
    *,
    _review_list: bool = False,
) -> tuple[dict[str, Any], bool]:
    request_id = uuid4().hex
    now = dt_util.utcnow()
    try:
        async with asyncio.timeout(TOOL_TIMEOUT):
            user_id = await async_require_admin(hass, context)
            if not tasks_ready(hass):
                raise AssistantError("not_authorized")
            if key not in SPEC_BY_KEY:
                raise AssistantError("invalid_arguments")
            args = SPEC_BY_KEY[key].parameters(arguments)
            with read_slot(hass, user_id):
                owners: list[tuple[AssistantScope, ApprovalStore]] = []
                evidence: dict[str, Any] = {}
                warnings: list[str] = []
                if key == "task_vehicles":
                    scopes = await async_vehicle_scopes(
                        hass,
                        context,
                        limit=MAX_VEHICLES + 1,
                        operation=REVIEW_TASKS if _review_list else key,
                    )
                    view = vehicle_list_view(hass, scopes)
                    data, evidence, warnings = view.data, view.evidence, view.warnings
                else:
                    scope = await async_authorize(hass, context, args["vehicle_ref"], operation=key)
                    scope.assert_task_current(hass)
                    scopes = [scope]
                    if key == "preview_detail_sync":
                        data = (
                            await async_detail_proposal(
                                hass,
                                context,
                                scope.vehicle_ref,
                                args["start_date"],
                                args["end_date"],
                            )
                        ).model_data()
                    elif key == REQUEST_TOOL:
                        data = await async_request_details(
                            hass, context, scope.vehicle_ref, args["start_date"], args["end_date"]
                        )
                        if data["task_recorded"]:
                            current = scope.runtime.assistant_tasks
                            if current is None:
                                raise AssistantError("not_authorized")
                            owners.append((scope, current))
                    else:
                        store = task_owner(hass, scope)
                        owners.append((scope, store))
                        if key == "get_tasks":
                            rows = sorted(
                                await store.async_list(context, scope.vehicle_ref),
                                key=lambda row: (row.plan.created_at, row.task_id),
                                reverse=True,
                            )
                            start = (args["page"] - 1) * args["limit"]
                            data = {
                                "tasks": [
                                    ticket_view(row) for row in rows[start : start + args["limit"]]
                                ],
                                "page": args["page"],
                                "total": len(rows),
                                "has_more": start + args["limit"] < len(rows),
                            }
                        else:
                            ticket = await store.async_get(args["task_id"], context)
                            if ticket.plan.vehicle_ref != scope.vehicle_ref:
                                raise AssistantError("not_authorized")
                            data = {"task": ticket_view(ticket)}
                for scope in scopes:
                    await scope.async_validate(hass)
                for scope in scopes:
                    scope.assert_task_current(hass)
                for scope, store in owners:
                    assert_task_owner(hass, scope, store)
                response = {
                    "schema_version": 1,
                    "request_id": request_id,
                    "data": data,
                    "query": {"as_of": now.isoformat(), "business_timezone": BUSINESS_TIMEZONE},
                    "evidence": {
                        "contract": "ninebot-tasks-v1",
                        "access_mode": "local_only",
                        "cloud_calls": 0,
                        **evidence,
                    },
                }
                response["warnings"] = warnings
                return checked_result(response)
    except (TaskStoreUnavailable, ArchiveError):
        return tool_failure(request_id, "retry_later")
    except TOOL_ERRORS as err:
        return tool_failure(request_id, err)
