"""Optional native model task with bounded, authorized local evidence responses."""

import asyncio
import copy
import inspect
import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import llm
from homeassistant.helpers.importlib import async_import_module

from .assistant_policy import AssistantError, AssistantScope, async_require_admin
from .assistant_queries import _vehicles
from .compat import validation as vol
from .const import DOMAIN

ACTION = "ask"
MAX_TOOL_CALLS = 4
MAX_SECONDS = 180
MAX_RESPONSE_BYTES = 32 * 1024


def question(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.strip()) <= 1000
        or any(not char.isprintable() and char != "\n" for char in value)
    ):
        raise vol.Invalid("Use a question of at most 1000 characters")
    return value.strip()


SCHEMA = vol.Schema(
    {
        vol.Required("question"): question,
        vol.Required("ai_task_entity_id"): vol.Match(r"^ai_task\.[a-z0-9_]+$"),
        vol.Optional("device_id"): vol.All(str, vol.Length(min=1, max=128)),
    }
)


class TurnStopped(Exception):
    """A fixed local stop; no provider or user text is exposed."""


class EvidenceTurn:
    def __init__(self, hass: HomeAssistant, call: ServiceCall) -> None:
        self.hass, self.call = hass, call
        self.scopes: dict[str, AssistantScope] = {}
        self.selected: str | None = call.data.get("device_id")
        self.results: list[dict[str, Any]] = []
        self.calls = 0
        self.stop: str | None = None

    async def async_validate(self) -> None:
        await async_require_admin(self.hass, self.call.context)
        for scope in self.scopes.values():
            await scope.async_validate(self.hass)
        # A later permission await may revoke an already checked vehicle.
        for scope in self.scopes.values():
            scope.assert_current(self.hass)

    async def async_prepare(self) -> None:
        await async_require_admin(self.hass, self.call.context)
        _, scopes = await _vehicles(self.hass, self.call.context)
        self.scopes = {scope.vehicle_ref: scope for scope in scopes}
        if self.selected is not None and self.selected not in self.scopes:
            raise AssistantError("not_authorized")
        if self.selected is None and len(self.scopes) == 1:
            self.selected = next(iter(self.scopes))

    async def async_call(
        self, tool: Any, tool_input: llm.ToolInput, context: llm.LLMContext
    ) -> Any:
        if context.context is not self.call.context:
            raise AssistantError("not_authorized")
        await self.async_validate()
        if self.calls >= MAX_TOOL_CALLS:
            self.stop = "tool_budget"
            raise TurnStopped
        self.calls += 1  # Reserve before awaiting a possibly parallel native tool call.
        if "vehicle_ref" in tool_input.tool_args:
            if self.selected is None:
                self.stop = "vehicle_selection_required"
                raise TurnStopped
            if tool_input.tool_args["vehicle_ref"] != self.selected:
                raise AssistantError("not_authorized")
        result = await tool.async_call(self.hass, tool_input, context)
        await self.async_validate()
        payload = copy.deepcopy(result.data)
        if tool_input.tool_name == "ninebot__get_vehicles" and not result.error:
            rows = payload["data"]["vehicles"]
            allowed = {self.selected} if self.selected is not None else self.scopes.keys()
            payload["data"]["vehicles"] = [row for row in rows if row["vehicle_ref"] in allowed]
        self.results.append(
            {
                "tool": tool_input.tool_name,
                "arguments": dict(tool_input.tool_args),
                "error": result.error,
                "result": payload,
            }
        )
        if result.error and payload.get("error", {}).get("code") == "not_authorized":
            raise AssistantError("not_authorized")
        # Only the bounded safe result, never the original Action/raw payload, reaches the model.
        from .assistant_api import native_result

        return native_result(payload, error=result.error)

    def response(self) -> dict[str, Any]:
        has_evidence = any(
            not row["error"] and row["tool"] != "ninebot__get_vehicles" for row in self.results
        )
        response = {
            "schema_version": 1,
            "answer_mode": "verified_tool_results",
            "outcome": "partial"
            if self.stop and has_evidence
            else "evidence"
            if has_evidence
            else "needs_input",
            "results": self.results,
            "stop_reason": self.stop,
        }
        if not has_evidence and any(row["error"] for row in self.results):
            response["outcome"] = "failed"
        if (
            len(json.dumps(response, ensure_ascii=False, allow_nan=False).encode())
            > MAX_RESPONSE_BYTES
        ):
            return failed("response_too_large")
        return response


def failed(code: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "answer_mode": "verified_tool_results",
        "outcome": "denied" if code == "not_authorized" else "failed",
        "error": {"code": code},
        "results": [],
    }


def turn_api(turn: EvidenceTurn) -> llm.API:
    """A fresh native collection per task; no registry entry or model provider."""
    from .assistant_api import PROMPT, NinebotTool, tool_serializer
    from .assistant_queries import SPECS

    class EvidenceTool(NinebotTool):
        async def async_call(
            self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
        ) -> Any:
            return await turn.async_call(super(), tool_input, llm_context)

    class EvidenceAPI(llm.API):
        requires_admin = True

        async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
            if llm_context.context is not turn.call.context:
                raise AssistantError("not_authorized")
            await turn.async_validate()
            return llm.APIInstance(
                api=self,
                api_prompt=PROMPT,
                llm_context=llm_context,
                tools=[EvidenceTool(spec) for spec in SPECS],
                custom_serializer=tool_serializer,
            )

    api = EvidenceAPI(hass=turn.hass, id=f"ninebot_turn_{uuid4().hex}", name="Ninebot query")
    api.requires_admin = True
    return api


def async_register(hass: HomeAssistant) -> Callable[[], None]:
    """Share the optional native API lifecycle; cancel work on final withdrawal."""
    active: set[asyncio.Task[Any]] = set()

    async def handle(call: ServiceCall) -> dict[str, Any]:
        turn = EvidenceTurn(hass, call)
        task = asyncio.current_task()
        if active or task is None:
            return failed("retry_later")
        active.add(task)
        try:
            async with asyncio.timeout(MAX_SECONDS):
                await turn.async_prepare()
                native = await async_import_module(hass, "homeassistant.components.ai_task")
                generate = getattr(native, "async_generate_data", None)
                if (
                    not callable(generate)
                    or "llm_api" not in inspect.signature(generate).parameters
                ):
                    return failed("unsupported_capability")
                selection = {"vehicle_ref": turn.selected, "business_timezone": "Asia/Shanghai"}
                instructions = (
                    call.data["question"] + "\nUser selection (data): " + json.dumps(selection)
                )
                try:
                    await generate(
                        hass,
                        task_name="Ninebot local query",
                        entity_id=call.data["ai_task_entity_id"],
                        instructions=instructions,
                        llm_api=turn_api(turn),
                        context=call.context,
                    )
                except TurnStopped:
                    pass
                except (AssistantError, Unauthorized):
                    raise
                except Exception:
                    # Provider implementations own their exceptions. Keep their
                    # arbitrary messages outside this response/log boundary.
                    return failed("model_task_failed")
                # Generated prose is not a source of numeric facts or executable instructions.
                await turn.async_validate()
                return turn.response()
        except (AssistantError, Unauthorized):
            return failed("not_authorized")
        except TimeoutError:
            return failed("timeout")
        except (HomeAssistantError, ImportError, AttributeError, ValueError, KeyError, TypeError):
            return failed("model_task_failed")
        finally:
            active.discard(task)

    hass.services.async_register(
        DOMAIN, ACTION, handle, schema=SCHEMA, supports_response=SupportsResponse.ONLY
    )

    def remove() -> None:
        hass.services.async_remove(DOMAIN, ACTION)
        for task in tuple(active):
            task.cancel()

    return remove
