"""Optional native model task with bounded, authorized local evidence responses."""

import asyncio
import copy
import inspect
import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any, NoReturn
from uuid import uuid4

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import llm
from homeassistant.helpers.importlib import async_import_module
from homeassistant.util import dt as dt_util

from .assistant import current_access
from .assistant_catalog import (
    QUERY_SPECS,
    ToolSpec,
    business_month,
    changed_statistic,
    statistic_change,
    vehicle_reference,
)
from .assistant_evidence import MAX_RECEIPTS, render_evidence_answer
from .assistant_policy import AssistantError, AssistantScope, async_authorize, async_require_admin
from .assistant_queries import (
    MAX_VEHICLES,
    async_other_vehicle_choices,
    async_vehicle_scopes,
    async_visible_keys,
    period_dates,
    vehicle_list_view,
)
from .assistant_views import METRIC_UNITS
from .compat import validation as vol
from .const import BUSINESS_TIMEZONE, DOMAIN
from .recorded_actions import business_date

ACTION = "ask"
MAX_TOOL_CALLS = MAX_RECEIPTS
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


def monthly_context(value: dict[str, str]) -> dict[str, str]:
    if value["through_date"][:7] != value["month"]:
        raise vol.Invalid("The end date must belong to the selected month")
    return value


QUERY_SELECTION = {
    vol.Required("device_id"): vehicle_reference,
    vol.Required("metric"): vol.In(tuple(METRIC_UNITS)),
}
QUERY_CONTEXT = vol.Any(
    vol.Schema({**QUERY_SELECTION, vol.Required("date"): business_date}),
    vol.All(
        vol.Schema(
            {
                **QUERY_SELECTION,
                vol.Required("month"): business_month,
                vol.Required("through_date"): business_date,
            }
        ),
        monthly_context,
    ),
)


SCHEMA = vol.Schema(
    {
        vol.Required("question"): question,
        vol.Required("ai_task_entity_id"): vol.Match(r"^ai_task\.[a-z0-9_]+$"),
        vol.Optional("device_id"): vol.All(str, vol.Length(min=1, max=128)),
        vol.Optional("query_context"): QUERY_CONTEXT,
    }
)


class TurnStopped(Exception):
    """A fixed local stop; no provider or user text is exposed."""


class EvidenceTurn:
    def __init__(self, hass: HomeAssistant, call: ServiceCall) -> None:
        self.hass, self.call = hass, call
        self.started_at = dt_util.utcnow()
        self.business_date = period_dates("today", self.started_at)[0].isoformat()
        self.scopes: dict[str, AssistantScope] = {}
        self.previous: dict[str, str] | None = call.data.get("query_context")
        self.selected: str | None = call.data.get("device_id") or (
            self.previous["device_id"] if self.previous else None
        )
        self.other_choices: list[AssistantScope] | None = None
        self.selected_reads: list[str] = []
        self.results: list[dict[str, Any]] = []
        self.calls = 0
        self.stop: str | None = None
        self.denied = False

    def deny(self) -> NoReturn:
        self.denied = True
        raise AssistantError("not_authorized")

    async def async_validate(self) -> None:
        if self.denied:
            self.deny()
        try:
            await async_require_admin(self.hass, self.call.context)
            for scope in tuple(self.scopes.values()):
                await scope.async_validate(self.hass)
            # A later permission await may revoke an already checked vehicle.
            for scope in self.scopes.values():
                scope.assert_current(self.hass)
        except (AssistantError, Unauthorized):
            self.denied = True
            raise

    async def async_prepare(self) -> None:
        if self.selected is not None:
            scope = await async_authorize(
                self.hass,
                self.call.context,
                self.selected,
                operation="get_statistics" if self.previous else "get_vehicles",
            )
            scopes = [scope]
        else:
            await async_require_admin(self.hass, self.call.context)
            scopes = await async_vehicle_scopes(
                self.hass, self.call.context, limit=MAX_VEHICLES + 1, operation="get_vehicles"
            )
        self.scopes = {scope.vehicle_ref: scope for scope in scopes}
        if len(scopes) == 1:
            scope = scopes[0]
            self.selected = scope.vehicle_ref
            scope.assert_current(self.hass)
            self.selected_reads = sorted(current_access(scope.entry).vehicles[scope.sn].reads)

    async def async_call(
        self,
        tool: Any,
        tool_input: llm.ToolInput,
        context: llm.LLMContext,
        *,
        bound_vehicle: str | None = None,
    ) -> Any:
        if context.context is not self.call.context:
            self.deny()
        await self.async_validate()
        if self.calls >= (1 if self.previous else MAX_TOOL_CALLS):
            self.stop = self.stop or "tool_budget"
            raise TurnStopped
        self.calls += 1  # Reserve before awaiting a possibly parallel native tool call.
        expected = self.selected
        if bound_vehicle is not None and self.previous is not None:
            bound_vehicle, tool_input = await self._async_followup(bound_vehicle, tool_input)
            expected = bound_vehicle
        if bound_vehicle is not None:
            if "vehicle_ref" in tool_input.tool_args:
                self.deny()
            tool_input = replace(
                tool_input, tool_args={**tool_input.tool_args, "vehicle_ref": bound_vehicle}
            )
        if "vehicle_ref" in tool_input.tool_args:
            if self.selected is None:
                self.stop = "vehicle_selection_required"
                raise TurnStopped
            if tool_input.tool_args["vehicle_ref"] != expected:
                self.deny()
        result = await tool.async_call(self.hass, tool_input, context)
        await self.async_validate()
        payload = copy.deepcopy(result.data)
        if tool_input.tool_name == "ninebot__get_vehicles" and not result.error:
            rows = payload["data"]["vehicles"]
            allowed = {self.selected} if self.selected is not None else self.scopes.keys()
            payload["data"]["vehicles"] = [row for row in rows if row["vehicle_ref"] in allowed]
        if result.error and payload.get("error", {}).get("code") == "not_authorized":
            self.deny()
        self.results.append(
            {
                "tool": tool_input.tool_name,
                "arguments": dict(tool_input.tool_args),
                "error": result.error,
                "result": payload,
            }
        )
        # Only the bounded safe result, never the original Action/raw payload, reaches the model.
        from .assistant_api import native_result

        return native_result(copy.deepcopy(payload), error=result.error)

    async def _async_followup(
        self, anchor: str, tool_input: llm.ToolInput
    ) -> tuple[str, llm.ToolInput]:
        """Resolve a statistic from the current grants and the preceding selection."""
        assert self.previous is not None
        supplied = tool_input.tool_args
        if tool_input.tool_name != "ninebot__get_statistics" or supplied.keys() - {"change"}:
            self.deny()
        previous = self.previous
        args = {
            "period": previous.get("date") or previous["month"],
            "metric": previous["metric"],
            **({"through_date": previous["through_date"]} if "month" in previous else {}),
        }
        if "change" in supplied:
            try:
                args = changed_statistic({**args, "change": supplied["change"]})
            except vol.Invalid as err:
                self.stop = "invalid_arguments"
                raise TurnStopped from err
        if args.pop("other_vehicle", False):
            if self.call.data.get("device_id"):
                self.deny()
            choices = await async_other_vehicle_choices(self.hass, self.call.context, anchor)
            self.scopes.update((scope.vehicle_ref, scope) for scope in choices)
            await self.async_validate()
            if len(choices) != 1:
                self.other_choices = choices
                self.stop = "vehicle_selection_required" if choices else "no_other_vehicle"
                raise TurnStopped
            anchor = choices[0].vehicle_ref
            scope = await async_authorize(
                self.hass, self.call.context, anchor, operation="get_statistics"
            )
            choices[0].assert_current(self.hass)
            self.scopes[anchor] = scope
        return anchor, replace(tool_input, tool_args=args)

    def response(self) -> dict[str, Any]:
        if self.denied:
            return failed("not_authorized")
        has_evidence = any(
            not row["error"] and row["tool"] != "ninebot__get_vehicles" for row in self.results
        )
        if (
            self.stop is not None
            and self.stop in {"model_task_failed", "invalid_arguments"}
            and not has_evidence
        ):
            return failed(self.stop)
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
        if self.other_choices == []:
            response["next_step"] = {"type": "no_other_vehicle"}
        elif self.other_choices is not None or (
            self.selected is None
            and len(self.scopes) > 1
            and (self.stop == "vehicle_selection_required" or response["outcome"] == "needs_input")
        ):
            options = (
                self.other_choices if self.other_choices is not None else list(self.scopes.values())
            )
            roster = vehicle_list_view(self.hass, options).data
            response["next_step"] = {
                "type": "select_vehicle",
                "parameter": "device_id",
                "choices": [
                    {"device_id": row["vehicle_ref"], "label": row["label"]}
                    for row in roster["vehicles"]
                ],
                "more_available": roster["more_available"],
            }
        elif response["outcome"] == "needs_input" and self.selected_reads:
            response["selection"] = {
                "device_id": self.selected,
                "allowed_reads": self.selected_reads,
                "business_date": self.business_date,
                "business_timezone": BUSINESS_TIMEZONE,
            }
        contexts = [
            row["result"].get("query_context")
            for row in self.results
            if row["tool"] in {"ninebot__get_statistics", "ninebot__explain_statistics"}
        ]
        if contexts and contexts[0] and all(item == contexts[0] for item in contexts):
            response["query_context"] = contexts[0]
        if (
            len(json.dumps(response, ensure_ascii=False, allow_nan=False).encode())
            > MAX_RESPONSE_BYTES
        ):
            return failed("response_too_large")
        if (answer := render_evidence_answer(self.results)) is not None:
            response["answer"] = answer
            if (
                len(json.dumps(response, ensure_ascii=False, allow_nan=False).encode())
                > MAX_RESPONSE_BYTES
            ):
                del response["answer"]  # Prefer complete tool results over a truncated display.
        return response


def failed(code: str) -> dict[str, Any]:
    denied = code == "not_authorized"
    result: dict[str, Any] = {
        "schema_version": 1,
        "answer_mode": "verified_tool_results",
        "outcome": "denied" if denied else "failed",
        "error": {"code": code},
        "results": [],
    }
    if denied:
        result["answer"] = render_evidence_answer([], denied=True)
    return result


def turn_api(turn: EvidenceTurn) -> llm.API:
    """A fresh native collection per task; no registry entry or model provider."""
    from .assistant_api import PROMPT, NinebotTool, tool_serializer

    class EvidenceTool(NinebotTool):
        def __init__(self, spec: ToolSpec) -> None:
            super().__init__(
                spec,
                selected_vehicle=None
                if turn.previous and not turn.call.data.get("device_id")
                else turn.selected,
                period_now=turn.started_at,
            )
            parameters = {
                key: value
                for key, value in spec.parameters.schema.items()
                if getattr(key, "schema", key) != "vehicle_ref"
            }
            self.bound_vehicle = (
                turn.selected if len(parameters) != len(spec.parameters.schema) else None
            )
            if self.bound_vehicle is not None:
                parameters = {
                    key: value
                    for key, value in parameters.items()
                    if getattr(key, "schema", key)
                    not in {"other_vehicle", "change", "through_date"}
                }
                self.parameters = vol.Schema(parameters)
                self.description = "Uses this Action's selected vehicle. " + spec.description
                if turn.previous and spec.key == "get_statistics":
                    allow_other = not turn.call.data.get("device_id")
                    self.parameters = vol.Schema(
                        {vol.Optional("change"): statistic_change(allow_other=allow_other)}
                    )
                    example = (
                        '{"vehicle":"other","period":"last_month","metric":"energy_wh"}'
                        if allow_other
                        else '{"vehicle":"previous","period":"last_month","metric":"energy_wh"}'
                    )
                    self.description = (
                        "Change only what the question asks. For a single change, pass a "
                        "string: last_month, yesterday, energy_wh or other_vehicle if offered. "
                        "For multiple changes pass one object, for example "
                        f"{example}. "
                        "For an object, choose the previous or other vehicle explicitly. "
                        "Omit unchanged period and metric. Omit change to reread."
                    )

        async def async_call(
            self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
        ) -> Any:
            return await turn.async_call(
                super(), tool_input, llm_context, bound_vehicle=self.bound_vehicle
            )

    class EvidenceAPI(llm.API):
        requires_admin = True

        async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
            if llm_context.context is not turn.call.context:
                raise AssistantError("not_authorized")
            visible = await async_visible_keys(
                turn.hass, turn.call.context, vehicle_ref=turn.selected, queries_only=True
            )
            await turn.async_validate()
            if turn.previous:
                visible = visible.intersection({"get_statistics"})
            elif turn.selected is None:
                visible = visible.intersection({"get_vehicles", "search_help"})
            tools: list[llm.Tool] = [
                EvidenceTool(spec) for spec in QUERY_SPECS if spec.key in visible
            ]
            if not tools:
                turn.deny()
            if turn.previous:
                scope_prompt = (
                    "Read a fresh statistic for this follow-up. The previous vehicle is the "
                    "reference point. For multiple changes, select vehicle=other when the user "
                    "asks about another vehicle, or vehicle=previous for the same vehicle. "
                    "Combine the vehicle, requested month or day, and requested measure in ONE "
                    "change object. Omit unchanged fields. Energy use is energy_wh. "
                    "For a single change its short string also works. Use only the new receipt."
                )
            elif turn.selected is not None:
                scope_prompt = (
                    PROMPT + " This Action has already selected a vehicle. Its vehicle tools are "
                    "bound to that selection; call them directly without vehicle_ref. If the "
                    "question requires another vehicle, ask the user to select it in the Action."
                )
            else:
                scope_prompt = (
                    PROMPT + " Multiple vehicles are available. For a vehicle question, list the "
                    "choices and ask the user to select the HA device. For general help, "
                    "search the guide directly."
                )
            return llm.APIInstance(
                api=self,
                api_prompt=scope_prompt
                + f" Use the Action's starting business date, {turn.business_date} "
                f"({BUSINESS_TIMEZONE}), for relative periods. When the scope excludes a "
                "requested read, report that limit without supplying a date or count.",
                llm_context=llm_context,
                tools=tools,
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
                selection: dict[str, Any] = {
                    "vehicle_selected": turn.selected is not None
                    and (not turn.previous or bool(call.data.get("device_id"))),
                    "business_timezone": BUSINESS_TIMEZONE,
                    "business_date": turn.business_date,
                }
                if turn.selected_reads:
                    selection["allowed_reads"] = turn.selected_reads
                if turn.previous:
                    selection["previous_vehicle"] = (
                        "explicitly_selected" if call.data.get("device_id") else "from_last_receipt"
                    )
                    if "date" in turn.previous:
                        selection["previous_date"] = turn.previous["date"]
                    else:
                        selection["previous_month"] = turn.previous["month"]
                        selection["previous_through_date"] = turn.previous["through_date"]
                    selection["previous_metric"] = turn.previous["metric"]
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
                    turn.stop = turn.stop or "model_task_failed"
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
