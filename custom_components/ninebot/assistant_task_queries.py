"""Local task tools: preview, apply and inspect, with no approval or cloud execution."""

import asyncio
import json
import re
from typing import Any
from uuid import uuid4

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.util import dt as dt_util

from .assistant import DATA_KEY, _enabled
from .assistant_errors import error_data
from .assistant_limits import ReadLimitExceeded, read_slot
from .assistant_plans import async_detail_proposal
from .assistant_policy import AssistantError, AssistantScope, async_authorize, async_require_admin
from .assistant_queries import MAX_RESPONSE_BYTES, VEHICLE, ToolSpec, _vehicles
from .assistant_review import REGISTERED
from .assistant_task_store import ApprovalStore, TaskStoreUnavailable
from .assistant_task_views import ticket_view
from .assistant_tasks import assert_task_owner, async_request_details, task_owner
from .compat import validation as vol
from .const import BUSINESS_TIMEZONE
from .recorded_actions import business_date
from .ride_archive import ArchiveError


def task_reference(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{32}", value) is None:
        raise vol.Invalid("Use a task ID returned by the task tools")
    return value


DATES = {
    **VEHICLE,
    vol.Required("start_date"): business_date,
    vol.Required("end_date"): business_date,
}
SPECS = (
    ToolSpec(
        "task_vehicles",
        "List task-enabled Ninebot vehicles",
        "List vehicles allowed for task applications. Ask which vehicle when ambiguous; "
        "never select the first. Does not query the cloud.",
        vol.Schema({}),
    ),
    ToolSpec(
        "preview_detail_sync",
        "Preview missing local details",
        "Preview at most 31 business days and twenty locally mapped missing details. "
        "Unobserved history is not zero. Does not create a task or query the cloud.",
        vol.Schema(DATES),
    ),
    ToolSpec(
        "request_detail_sync_approval",
        "Request human review of missing details",
        "Use only when the user asks to apply for detail synchronization. Writes a bounded "
        "local application from the selected vehicle/dates. Human review is separate. "
        "Never approves, queues, completes or queries the cloud. Do not ask for a token.",
        vol.Schema(DATES),
        idempotent=False,
    ),
    ToolSpec(
        "get_tasks",
        "List your vehicle tasks",
        "Read the original user's local task metadata. Approved does not mean queued "
        "or completed. No confirmation secrets, ride identities or cloud requests.",
        vol.Schema(VEHICLE),
    ),
    ToolSpec(
        "get_task",
        "Read one local task",
        "Read one task ID from your task list for the selected vehicle. Missing is not "
        "completed. No approval, cloud requests or private ride data.",
        vol.Schema({**VEHICLE, vol.Required("task_id"): task_reference}),
    ),
)
SPEC_BY_KEY = {spec.key: spec for spec in SPECS}
REQUEST_TOOL = "request_detail_sync_approval"


def available(hass: HomeAssistant) -> bool:
    state = hass.data.get(DATA_KEY)
    return (
        _enabled(hass, tasks=True)
        and hass.data.get(REGISTERED) is True
        and (state is None or (state.review_error is None and state.task_error is None))
    )


async def async_task_query(
    hass: HomeAssistant, key: str, arguments: dict[str, Any], context: Context | None
) -> tuple[dict[str, Any], bool]:
    request_id, retry_after = uuid4().hex, None
    now = dt_util.utcnow()
    try:
        async with asyncio.timeout(10):
            user_id = await async_require_admin(hass, context)
            if not available(hass):
                raise AssistantError("not_authorized")
            if key not in SPEC_BY_KEY:
                raise AssistantError("invalid_arguments")
            args = SPEC_BY_KEY[key].parameters(arguments)
            with read_slot(hass, user_id):
                owners: list[tuple[AssistantScope, ApprovalStore]] = []
                if key == "task_vehicles":
                    view, scopes = await _vehicles(hass, context, tasks=True)
                    data = view.data
                else:
                    scope = await async_authorize(hass, context, args["vehicle_ref"])
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
                            data = {
                                "tasks": [
                                    ticket_view(row)
                                    for row in await store.async_list(context, scope.vehicle_ref)
                                ]
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
                    },
                }
                if (
                    len(json.dumps(response, ensure_ascii=False, allow_nan=False).encode())
                    > MAX_RESPONSE_BYTES
                ):
                    raise AssistantError("response_too_large")
                return response, False
    except ReadLimitExceeded as err:
        code, retry_after = "retry_later", err.retry_after
    except Unauthorized:
        code = "not_authorized"
    except vol.Invalid:
        code = "invalid_arguments"
    except AssistantError as err:
        code = err.code
    except TimeoutError:
        code = "timeout"
    except (TaskStoreUnavailable, ArchiveError):
        code = "retry_later"
    except HomeAssistantError:
        code = "unsupported_capability"
    return {
        "schema_version": 1,
        "request_id": request_id,
        "error": error_data(code, retry_after),
    }, True
