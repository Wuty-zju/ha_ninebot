"""Authenticated human task review over HA WebSocket, never a service or LLM tool."""

import asyncio
import json
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import Unauthorized

from .assistant_limits import ReadLimitExceeded, read_slot
from .assistant_policy import AssistantError, async_authorize
from .assistant_task_store import TaskStoreUnavailable
from .assistant_task_views import ticket_view
from .assistant_tasks import assert_task_owner, task_owner
from .compat import validation as vol
from .ride_archive import ArchiveError

COMMAND = "ninebot/assistant/review"
REGISTERED = "ninebot_assistant_review_registered"
MAX_REVIEW_BYTES = 32 * 1024
ERRORS = {
    "not_authorized": "Task review access denied.",
    "task_not_found": "This task is no longer available.",
    "approval_expired": "The confirmation expired. Request a new plan.",
    "approval_used": "This task is no longer awaiting approval.",
    "invalid_arguments": "The review arguments do not match the operation.",
    "response_too_large": "Narrow the requested task range.",
    "retry_later": "Task review is temporarily unavailable. Check its state before retrying.",
}


@websocket_api.websocket_command(
    {
        vol.Required("type"): COMMAND,
        vol.Required("vehicle_ref"): vol.All(str, vol.Length(min=1, max=128)),
        vol.Required("operation"): vol.In(("list", "prepare", "approve", "cancel")),
        vol.Optional("task_id"): vol.All(str, vol.Match(r"^[a-f0-9]{32}$")),
        # Use Core's redacted secret key for the short-lived confirmation nonce.
        vol.Optional("token"): vol.All(str, vol.Length(min=16, max=128)),
        vol.Optional("plan_hash"): vol.All(str, vol.Match(r"^[a-f0-9]{64}$")),
    }
)
@websocket_api.async_response
async def async_review(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, message: dict[str, Any]
) -> None:
    context = connection.context(message)
    try:
        if context.user_id != connection.user.id:
            raise AssistantError("not_authorized")
        scope = await async_authorize(hass, context, message["vehicle_ref"])
        if scope.runtime.assistant_task_blocks:
            raise AssistantError("retry_later")
        scope.assert_task_current(hass)
        operation = message["operation"]
        fields = set(message) - {"id", "type", "vehicle_ref", "operation"}
        expected = {
            "list": set(),
            "prepare": {"task_id"},
            "cancel": {"task_id"},
            "approve": {"task_id", "token", "plan_hash"},
        }
        if fields != expected[operation]:
            raise AssistantError("invalid_arguments")
        user_id = scope.context.user_id
        if user_id is None:
            raise AssistantError("not_authorized")
        with read_slot(hass, user_id):
            async with asyncio.timeout(10):
                store = task_owner(hass, scope)
                if operation == "list":
                    tickets = await store.async_list(context, scope.vehicle_ref)
                    response: dict[str, Any] = {"tasks": [ticket_view(row) for row in tickets]}
                else:
                    ticket = await store.async_get(message["task_id"], context)
                    if ticket.plan.vehicle_ref != scope.vehicle_ref:
                        raise AssistantError("not_authorized")
                    if operation == "prepare":
                        ticket, nonce = await store.async_prepare(ticket.task_id, context)
                        response = {
                            "task": {
                                **ticket_view(ticket),
                                "ride_ids": [identity for identity, _ in ticket.plan.scope.rides],
                            },
                            "token": nonce,
                        }
                    elif operation == "cancel":
                        from .assistant_execution import driver_for

                        ticket = await driver_for(hass, scope.entry).async_cancel(
                            ticket.task_id, context
                        )
                        response = {"task": ticket_view(ticket)}
                    else:
                        ticket = await store.async_approve(
                            ticket.task_id, context, message["token"], message["plan_hash"]
                        )
                        from .assistant_execution import driver_for

                        ticket = await driver_for(hass, scope.entry).async_accept(ticket, context)
                        response = {"task": ticket_view(ticket)}
                await scope.async_validate(hass)
                assert_task_owner(hass, scope, store)
                response["schema_version"] = 1
                if len(json.dumps(response, ensure_ascii=False).encode()) > MAX_REVIEW_BYTES:
                    raise AssistantError("response_too_large")
        connection.send_result(message["id"], response)
    except (AssistantError, Unauthorized) as err:
        code = (
            err.code
            if isinstance(err, AssistantError) and err.code in ERRORS
            else ("not_authorized" if isinstance(err, Unauthorized) else "retry_later")
        )
        connection.send_error(message["id"], code, ERRORS[code])
    except (ReadLimitExceeded, TimeoutError, TaskStoreUnavailable, ArchiveError):
        connection.send_error(message["id"], "retry_later", ERRORS["retry_later"])


@websocket_api.websocket_command({vol.Required("type"): "ninebot/assistant/vehicles"})
@websocket_api.async_response
async def async_review_vehicles(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, message: dict[str, Any]
) -> None:
    """Thin local discovery; reuse task consent, current Context, limits and projection."""
    from .assistant_task_queries import async_task_query

    context = connection.context(message)
    if context.user_id != connection.user.id:
        connection.send_error(message["id"], "not_authorized", ERRORS["not_authorized"])
        return
    result, failed = await async_task_query(hass, "task_vehicles", {}, context)
    if failed:
        error = result["error"]
        connection.send_error(message["id"], error["code"], error["message"])
    else:
        connection.send_result(message["id"], result)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register once on opt-in; each call rechecks current consent and identity."""
    if hass.data.get(REGISTERED):
        return
    websocket_api.async_register_command(hass, async_review)
    websocket_api.async_register_command(hass, async_review_vehicles)
    hass.data[REGISTERED] = True
