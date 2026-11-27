"""Local evidence orchestration boundaries; providers are tested by native canaries."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import Context, ServiceCall
from homeassistant.helpers import llm

from custom_components.ninebot import assistant_ask as ask
from custom_components.ninebot.assistant_knowledge import search_help
from custom_components.ninebot.assistant_policy import AssistantError
from custom_components.ninebot.assistant_views import statistics_view


@pytest.fixture
def turn(hass):
    context = Context(user_id="synthetic-user")
    call = ServiceCall(
        hass,
        "ninebot",
        "ask",
        {
            "question": "Yesterday?",
            "ai_task_entity_id": "ai_task.synthetic",
            "device_id": "selected",
        },
        context=context,
    )
    result = ask.EvidenceTurn(hass, call)
    result.scopes = {"selected": SimpleNamespace(async_validate=AsyncMock(), assert_current=Mock())}
    return result


async def test_later_scope_await_cannot_release_a_revoked_earlier_vehicle(hass, turn):
    current = True

    def assert_current(actual_hass):
        assert actual_hass is hass
        if not current:
            raise AssistantError("not_authorized")

    async def later_validation(actual_hass):
        nonlocal current
        assert actual_hass is hass
        current = False

    first = SimpleNamespace(async_validate=AsyncMock(), assert_current=assert_current)
    last = SimpleNamespace(
        async_validate=AsyncMock(side_effect=later_validation), assert_current=Mock()
    )
    turn.scopes = {"earlier": first, "later": last}
    with patch.object(ask, "async_require_admin", new=AsyncMock()):
        with pytest.raises(AssistantError, match="not_authorized"):
            await turn.async_validate()
    first.async_validate.assert_awaited_once_with(hass)
    last.async_validate.assert_awaited_once_with(hass)


@pytest.mark.skipif(
    not all(hasattr(llm, name) for name in ("ToolAnnotations", "ToolResult")),
    reason="Native tool API is unavailable",
)
async def test_native_action_tools_are_bound_to_the_request_and_selected_vehicle(hass, turn):
    api = ask.turn_api(turn)
    with pytest.raises(AssistantError, match="^not_authorized$"):
        await api.async_get_api_instance(SimpleNamespace(context=Context(user_id="other")))
    context = llm.LLMContext(
        platform="ai_task",
        context=turn.call.context,
        language="en",
        assistant="conversation",
        device_id=None,
    )
    with (
        patch.object(ask, "async_require_admin", new=AsyncMock()),
        patch.object(
            ask,
            "async_visible_keys",
            new=AsyncMock(return_value=frozenset({"get_vehicle_status", "get_tasks"})),
        ) as discover,
    ):
        instance = await api.async_get_api_instance(context)
        discover.assert_awaited_once_with(
            hass, turn.call.context, vehicle_ref="selected", queries_only=True
        )
        assert [tool.name for tool in instance.tools] == ["ninebot__get_vehicle_status"]
        status = instance.tools[0]
        assert "vehicle_ref" not in {key.schema for key in status.parameters.schema}
        with patch(
            "custom_components.ninebot.assistant_api.async_query",
            new=AsyncMock(return_value=({"data": {"vehicle_locked": False}}, False)),
        ) as read:
            result = await status.async_call(
                hass, llm.ToolInput(tool_name=status.name, tool_args={}), instance.llm_context
            )
        assert not result.error and result.data["data"]["vehicle_locked"] is False
        read.assert_awaited_once_with(
            hass,
            "get_vehicle_status",
            {"vehicle_ref": "selected"},
            turn.call.context,
            _selected_vehicle="selected",
        )
        assert turn.results[0]["arguments"] == {"vehicle_ref": "selected"}


@pytest.mark.parametrize("value", ["", "\t", "x" * 1001, 7, "bad\x00value"])
def test_question_is_bounded(value):
    with pytest.raises(ask.vol.Invalid):
        ask.SCHEMA({"question": value, "ai_task_entity_id": "ai_task.synthetic"})


async def test_original_context_and_post_read_withdrawal(hass, turn):
    context = SimpleNamespace(context=turn.call.context)
    tool_input = SimpleNamespace(
        tool_name="ninebot__get_statistics",
        tool_args={"vehicle_ref": "selected", "period": "yesterday"},
    )
    data = {"data": {"duration_s": 1200}, "request_id": "a" * 32}
    base = SimpleNamespace(
        async_call=AsyncMock(return_value=SimpleNamespace(data=data, error=False))
    )
    with patch.object(ask, "async_require_admin", new=AsyncMock()):
        turn.scopes["selected"].async_validate.side_effect = [
            None,
            AssistantError("not_authorized"),
        ]
        with pytest.raises(AssistantError):
            await turn.async_call(base, tool_input, context)
    base.async_call.assert_awaited_once_with(hass, tool_input, context)
    assert turn.results == []


async def test_provider_formatting_cannot_mutate_business_or_recorded_evidence(hass, turn):
    original = {"data": {"duration_s": 1200}, "evidence": {"source_kind": "account_archive"}}
    base = SimpleNamespace(
        async_call=AsyncMock(return_value=SimpleNamespace(data=original, error=False))
    )
    with (
        patch.object(ask.EvidenceTurn, "async_validate", new=AsyncMock()),
        patch(
            "custom_components.ninebot.assistant_api.native_result",
            side_effect=lambda data, *, error: SimpleNamespace(data=data, error=error),
        ),
    ):
        returned = await turn.async_call(
            base,
            SimpleNamespace(
                tool_name="ninebot__get_statistics", tool_args={"vehicle_ref": "selected"}
            ),
            SimpleNamespace(context=turn.call.context),
        )
    returned.data["data"]["duration_s"] = 9999
    returned.data["evidence"]["source_kind"] = "changed_by_provider"
    recorded = turn.response()["results"][0]["result"]
    assert recorded == original
    assert recorded is not original and returned.data is not recorded
    assert recorded["data"]["duration_s"] == original["data"]["duration_s"] == 1200


async def test_multi_vehicle_requires_selection_before_read(turn):
    turn.selected = None
    turn.scopes["second"] = turn.scopes["selected"]
    base = SimpleNamespace(async_call=AsyncMock())
    with patch.object(ask.EvidenceTurn, "async_validate", new=AsyncMock()):
        with pytest.raises(ask.TurnStopped):
            await turn.async_call(
                base,
                SimpleNamespace(tool_args={"vehicle_ref": "selected"}),
                SimpleNamespace(context=turn.call.context),
            )
    base.async_call.assert_not_awaited()
    assert turn.stop == "vehicle_selection_required"
    assert turn.response()["next_step"] == {"type": "select_vehicle", "parameter": "device_id"}


async def test_wrong_vehicle_and_context_never_dispatch(turn):
    base = SimpleNamespace(async_call=AsyncMock())
    with patch.object(ask.EvidenceTurn, "async_validate", new=AsyncMock()):
        for args, context in (({"vehicle_ref": "other"}, turn.call.context), ({}, Context())):
            with pytest.raises(AssistantError):
                await turn.async_call(
                    base, SimpleNamespace(tool_args=args), SimpleNamespace(context=context)
                )
    base.async_call.assert_not_awaited()


async def test_bound_selection_preserves_provider_input_and_records_resolved_target(hass, turn):
    original = llm.ToolInput(tool_name="ninebot__get_statistics", tool_args={"period": "yesterday"})
    context = SimpleNamespace(context=turn.call.context)
    base = SimpleNamespace(
        async_call=AsyncMock(return_value=SimpleNamespace(data={"data": {}}, error=False))
    )
    with (
        patch.object(ask.EvidenceTurn, "async_validate", new=AsyncMock()),
        patch(
            "custom_components.ninebot.assistant_api.native_result",
            side_effect=lambda data, *, error: SimpleNamespace(data=data, error=error),
        ),
    ):
        await turn.async_call(base, original, context, bound_vehicle="selected")
    actual = base.async_call.await_args.args[1]
    assert actual is not original and actual.id == original.id
    assert original.tool_args == {"period": "yesterday"}
    assert actual.tool_args == {"period": "yesterday", "vehicle_ref": "selected"}
    base.async_call.assert_awaited_once_with(hass, actual, context)
    assert turn.results[0]["arguments"] == actual.tool_args


@pytest.mark.parametrize("override", ["selected", "other"])
async def test_bound_selection_rejects_explicit_target_even_when_matching(turn, override):
    base = SimpleNamespace(async_call=AsyncMock())
    with patch.object(ask.EvidenceTurn, "async_validate", new=AsyncMock()):
        with pytest.raises(AssistantError, match="not_authorized"):
            await turn.async_call(
                base,
                llm.ToolInput(
                    tool_name="ninebot__get_statistics",
                    tool_args={"vehicle_ref": override, "period": "yesterday"},
                ),
                SimpleNamespace(context=turn.call.context),
                bound_vehicle="selected",
            )
    assert turn.calls == 1 and turn.results == []
    base.async_call.assert_not_awaited()


async def test_call_budget_reserves_before_parallel_work(turn):
    turn.calls = ask.MAX_TOOL_CALLS
    base = SimpleNamespace(async_call=AsyncMock())
    with patch.object(ask.EvidenceTurn, "async_validate", new=AsyncMock()):
        with pytest.raises(ask.TurnStopped):
            await turn.async_call(
                base, SimpleNamespace(tool_args={}), SimpleNamespace(context=turn.call.context)
            )
    base.async_call.assert_not_awaited()
    assert turn.stop == "tool_budget"


def test_discovery_is_not_an_answer_and_large_result_is_explicit(turn):
    turn.results = [{"tool": "ninebot__get_vehicles", "error": False, "result": {}}]
    assert turn.response()["outcome"] == "needs_input"
    turn.results = [{"tool": "ninebot__get_statistics", "error": True, "result": {}}]
    assert turn.response()["outcome"] == "failed"
    turn.results = [
        {
            "tool": "ninebot__get_statistics",
            "error": False,
            "result": {"data": "x" * ask.MAX_RESPONSE_BYTES},
        }
    ]
    assert turn.response() == ask.failed("response_too_large")


async def test_model_draft_is_not_a_fact_and_native_context_is_preserved(hass, turn):
    contexts = []

    async def generate(actual_hass, *, context, llm_api, **kwargs):
        assert actual_hass is hass
        contexts.append(context)
        return SimpleNamespace(data="2000 seconds, invented text is not evidence")

    native = SimpleNamespace(async_generate_data=generate)
    with (
        patch.object(ask.EvidenceTurn, "async_prepare", new=AsyncMock()),
        patch.object(ask.EvidenceTurn, "async_validate", new=AsyncMock()),
        patch.object(ask, "turn_api", return_value=object()),
        patch.object(ask, "async_import_module", new=AsyncMock(return_value=native)),
    ):
        remove = ask.async_register(hass)
        try:
            result = await hass.services.async_call(
                "ninebot",
                "ask",
                dict(turn.call.data),
                blocking=True,
                return_response=True,
                context=turn.call.context,
            )
        finally:
            remove()
    assert contexts == [turn.call.context]
    assert result["outcome"] == "needs_input" and result["results"] == []
    assert "2000" not in str(result)


@pytest.mark.parametrize("kind", ["no_results", "discovery", "failed_read", "evidence"])
async def test_provider_failure_preserves_only_revalidated_evidence(hass, turn, kind):
    rows = (
        []
        if kind == "no_results"
        else [
            {
                "tool": "ninebot__get_vehicles"
                if kind == "discovery"
                else "ninebot__get_statistics",
                "error": kind == "failed_read",
                "result": {"data": {"duration_s": 1200}},
            }
        ]
    )

    async def generate(actual_hass, *, context, llm_api, **kwargs):
        assert actual_hass is hass and context is turn.call.context
        llm_api.results = rows
        raise RuntimeError("private provider detail token=never-return-this")

    with (
        patch.object(ask.EvidenceTurn, "async_prepare", new=AsyncMock()),
        patch.object(ask.EvidenceTurn, "async_validate", new=AsyncMock()) as validate,
        patch.object(ask, "turn_api", side_effect=lambda actual_turn: actual_turn),
        patch.object(
            ask,
            "async_import_module",
            new=AsyncMock(return_value=SimpleNamespace(async_generate_data=generate)),
        ),
    ):
        remove = ask.async_register(hass)
        try:
            result = await hass.services.async_call(
                "ninebot",
                "ask",
                dict(turn.call.data),
                blocking=True,
                return_response=True,
                context=turn.call.context,
            )
        finally:
            remove()
    validate.assert_awaited_once()
    if kind == "evidence":
        assert result["outcome"] == "partial" and result["stop_reason"] == "model_task_failed"
        assert result["results"] == rows
    else:
        assert result == ask.failed("model_task_failed")
    assert "never-return-this" not in str(result)


async def test_unregister_cancels_generation_and_releases_admission(hass, turn):
    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def generate(actual_hass, *, llm_api, **kwargs):
        assert actual_hass is hass
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    with (
        patch.object(ask.EvidenceTurn, "async_prepare", new=AsyncMock()),
        patch.object(ask.EvidenceTurn, "async_validate", new=AsyncMock()),
        patch.object(ask, "turn_api", return_value=object()),
        patch.object(
            ask,
            "async_import_module",
            new=AsyncMock(return_value=SimpleNamespace(async_generate_data=generate)),
        ),
    ):
        remove = ask.async_register(hass)
        first = asyncio.create_task(
            hass.services.async_call(
                "ninebot",
                "ask",
                dict(turn.call.data),
                blocking=True,
                return_response=True,
                context=turn.call.context,
            )
        )
        await asyncio.wait_for(entered.wait(), 1)
        busy = await hass.services.async_call(
            "ninebot",
            "ask",
            dict(turn.call.data),
            blocking=True,
            return_response=True,
            context=turn.call.context,
        )
        assert busy == ask.failed("retry_later")
        remove()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert stopped.is_set()
    assert not hass.services.has_service("ninebot", "ask")


def _help_receipt(question, language="en"):
    data = search_help(question, language, "2.1.27")
    return {
        "schema_version": 1,
        "request_id": "a" * 32,
        "data": data,
        "evidence": {"source_kind": "bundled_public_help"},
    }


async def test_action_displays_full_catalog_citation_from_recorded_tool_result(hass, turn):
    receipt = _help_receipt("ready accepted confirmed control_policy")
    control = next(
        match for match in receipt["data"]["matches"] if match["source_id"] == "control_outcomes"
    )
    native_tool = SimpleNamespace(
        async_call=AsyncMock(return_value=SimpleNamespace(data=receipt, error=False)),
    )

    async def generate(actual_hass, *, context, llm_api, **kwargs):
        result = await llm_api.async_call(
            native_tool,
            llm.ToolInput(tool_name="ninebot__search_help", tool_args={"query": "control_policy"}),
            SimpleNamespace(context=context),
        )
        result.data["data"]["matches"][0]["text"] = "provider-modified receipt"
        return SimpleNamespace(data="invented root cause and [Source](https://github")

    with (
        patch.object(ask.EvidenceTurn, "async_prepare", new=AsyncMock()),
        patch.object(ask.EvidenceTurn, "async_validate", new=AsyncMock()),
        patch.object(ask, "turn_api", side_effect=lambda actual_turn: actual_turn),
        patch.object(
            ask,
            "async_import_module",
            new=AsyncMock(return_value=SimpleNamespace(async_generate_data=generate)),
        ),
        patch(
            "custom_components.ninebot.assistant_api.native_result",
            side_effect=lambda data, *, error: SimpleNamespace(data=data, error=error),
        ),
    ):
        remove = ask.async_register(hass)
        try:
            response = await hass.services.async_call(
                "ninebot",
                "ask",
                dict(turn.call.data),
                blocking=True,
                return_response=True,
                context=turn.call.context,
            )
        finally:
            remove()

    assert response["answer_mode"] == "verified_tool_results" and response["outcome"] == "evidence"
    assert response["results"][0]["result"] == receipt
    answer = response["answer"]
    assert answer["contract"] == "ninebot-evidence-answer-v1"
    assert answer["request_ids"] == [receipt["request_id"]]
    assert control["citation"] in answer["text"]
    assert "local eligibility" in answer["text"] and "Native DEBUG" in answer["text"]
    assert "provider-modified" not in str(response) and "invented root cause" not in str(response)


def test_help_display_preserves_local_language_and_checks_fixed_citation(turn):
    receipt = _help_receipt("主电池循环计数显示不支持", "zh-Hans")
    turn.results = [{"tool": "ninebot__search_help", "error": False, "result": receipt}]
    answer = turn.response()["answer"]["text"]
    assert "证据不足时原因保持未知" in answer
    assert "对比App的同一字段" in answer
    assert receipt["data"]["matches"][0]["citation"] in answer
    receipt["data"]["matches"][0]["citation"] = "[Source](https://github"
    assert "answer" not in turn.response()


def _partial_statistics():
    now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    view = statistics_view(
        {
            "duration_s": 0,
            "period_closed": True,
            "ride_window_complete": False,
            "completeness_basis": "incomplete",
            "availability": {"ride_count": "incomplete_month_list"},
            "received_at": "2026-11-14T07:05:00+00:00",
        },
        daily=True,
        now=now,
    )
    return {
        "schema_version": 1,
        "request_id": "b" * 32,
        "query": {
            "start_date": "2026-11-13",
            "end_date": "2026-11-13",
            "business_timezone": "Asia/Shanghai",
        },
        "data": view.data,
        "quality": view.quality,
        "evidence": view.evidence,
    }


def test_statistics_display_keeps_zero_distinct_from_unknown_and_cause_unverified(turn):
    receipt = _partial_statistics()
    turn.results = [{"tool": "ninebot__get_statistics", "error": False, "result": receipt}]
    answer = turn.response()["answer"]["text"]
    assert "2026-11-13 (Asia/Shanghai)" in answer
    assert "Ride count: unknown (missing, recorded reason `incomplete_month_list`)" in answer
    assert "Duration: 0 s" in answer
    assert "2026-11-14T07:05:00+00:00" in answer
    assert "`account_archive` with coverage basis `business_day_projection`" in answer
    assert "complete period total unverified" in answer
    assert "cause of any data gap remains unverified" in answer


def test_month_display_uses_the_closed_summary_and_its_own_observation_time(turn):
    now = datetime(2026, 11, 14, tzinfo=UTC)
    view = statistics_view(
        {
            "period_closed": True,
            "observed_after_period_end": True,
            "received_at": now.isoformat(),
            "distance_km": 20,
            "ride_count": 2,
            "duration_s": 3600,
            "energy_wh": 600,
            "energy_intensity_wh_per_km": 30,
        },
        daily=False,
        now=now,
    )
    receipt = {
        **_partial_statistics(),
        "data": view.data,
        "quality": view.quality,
        "evidence": view.evidence,
        "query": {
            "start_date": "2026-10-01",
            "end_date": "2026-10-31",
            "business_timezone": "Asia/Shanghai",
        },
    }
    turn.results = [{"tool": "ninebot__get_statistics", "error": False, "result": receipt}]
    text = turn.response()["answer"]["text"]
    assert "2026-10-01 through 2026-10-31" in text and "Ride count: 2" in text
    assert "`server_month_summary`" in text and now.isoformat() in text
    assert "complete period total unverified" not in text and "data gap" not in text

    for missing_observation in (None, "unknown", "2026-11-14T00:00:00"):
        receipt["quality"]["received_at"] = missing_observation
        text = turn.response()["answer"]["text"]
        assert "Observation time: unrecorded." in text and "Ride count: 2" in text


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"request_id": "not-a-receipt"},
        {
            "query": {
                "business_timezone": "Asia/Shanghai",
                "start_date": "2026-02-30",
                "end_date": "2026-03-01",
            }
        },
        {"quality": {"metrics": {"distance_km": {"status": "missing", "reason": "not_reported"}}}},
        {"evidence": {"source_kind": "unknown", "coverage_basis": "unverified"}},
        {"error": {"code": "timeout"}},
    ],
)
def test_malformed_statistic_does_not_displace_an_independent_help_receipt(turn, change):
    help_receipt = _help_receipt("login 502")
    malformed = {**_partial_statistics(), **change}
    turn.results = [
        {"tool": "ninebot__get_statistics", "error": False, "result": malformed},
        {"tool": "ninebot__search_help", "error": False, "result": help_receipt},
    ]
    answer = turn.response()["answer"]
    assert answer["request_ids"] == [help_receipt["request_id"]]
    assert help_receipt["data"]["matches"][0]["citation"] in answer["text"]
    assert "Local statistics" not in answer["text"]


def test_partial_workflow_reports_timed_out_step_and_omits_invalid_sync_state(turn):
    turn.results = [
        {
            "tool": "ninebot__explain_statistics",
            "error": False,
            "result": {
                "schema_version": 1,
                "request_id": "c" * 32,
                "data": {
                    "metric": "ride_count",
                    "steps": [
                        {
                            "tool": "ninebot__get_statistics",
                            "error": True,
                            "result": {"error": {"code": "timeout"}},
                        },
                        {
                            "tool": "ninebot__get_sync_health",
                            "error": False,
                            "result": {"data": {"state": "private raw state"}},
                        },
                    ],
                },
                "quality": {"partial": True},
                "evidence": {"source_kind": "local_statistics_check"},
            },
        }
    ]
    answer = turn.response()["answer"]
    assert answer["request_ids"] == ["c" * 32]
    assert "timed out" in answer["text"] and "incomplete evidence" in answer["text"]
    assert "private raw state" not in answer["text"] and "Ride count:" not in answer["text"]


def test_statistics_workflow_uses_selected_metric_and_preserves_help_citation(turn):
    stats = {"tool": "ninebot__get_statistics", "error": False, "result": _partial_statistics()}
    help_receipt = _help_receipt("unknown missing zero")
    sync_receipt = {
        "data": {"state": "waiting_budget"},
        "evidence": {
            "source_kind": "account_archive",
            "operation": "sync_status",
            "adapter_contract": "ninebot-assistant-v1",
            "access_mode": "local_only",
        },
    }
    receipt = {
        "schema_version": 1,
        "request_id": "c" * 32,
        "data": {
            "metric": "ride_count",
            "steps": [
                stats,
                {"tool": "ninebot__get_sync_health", "error": False, "result": sync_receipt},
                {"tool": "ninebot__search_help", "error": False, "result": help_receipt},
            ],
        },
        "quality": {"partial": True},
        "evidence": {"source_kind": "local_statistics_check"},
    }
    turn.results = [{"tool": "ninebot__explain_statistics", "error": False, "result": receipt}]
    answer = turn.response()["answer"]
    assert answer["request_ids"] == [receipt["request_id"]]
    assert "Ride count: unknown" in answer["text"] and "Duration: 0 s" not in answer["text"]
    assert "`waiting_budget`" in answer["text"] and "underlying cause unverified" in answer["text"]
    assert "incomplete evidence" in answer["text"]
    assert any(match["citation"] in answer["text"] for match in help_receipt["data"]["matches"])


def test_auth_required_displays_safe_error_without_an_upstream_diagnosis(turn):
    receipt = {
        "schema_version": 1,
        "request_id": "d" * 32,
        "error": {"code": "auth_required", "message": "private arbitrary error"},
    }
    turn.results = [{"tool": "ninebot__get_statistics", "error": True, "result": receipt}]
    response = turn.response()
    assert response["outcome"] == "failed"
    answer = response["answer"]
    assert answer["request_ids"] == [receipt["request_id"]]
    assert "reauthentication in Home Assistant" in answer["text"]
    assert "authentication failure is unverified" in answer["text"]
    assert "private arbitrary error" not in answer["text"]


async def test_native_access_denial_withdraws_earlier_evidence_even_if_provider_continues(turn):
    turn.results = [
        {"tool": "ninebot__get_statistics", "error": False, "result": _partial_statistics()}
    ]
    base = SimpleNamespace(
        async_call=AsyncMock(
            return_value=SimpleNamespace(data={"error": {"code": "not_authorized"}}, error=True)
        )
    )
    with (
        patch.object(ask, "async_require_admin", new=AsyncMock()),
        pytest.raises(AssistantError, match="not_authorized"),
    ):
        await turn.async_call(
            base,
            llm.ToolInput(tool_name="ninebot__get_statistics", tool_args={"period": "yesterday"}),
            SimpleNamespace(context=turn.call.context),
            bound_vehicle="selected",
        )
    response = turn.response()
    assert response["outcome"] == "denied" and response["results"] == []
    assert response["answer"]["request_ids"] == []
    assert "which check failed" in response["answer"]["text"]
    assert "ride_count" not in response["answer"]["text"]


def test_display_is_optional_when_repeating_evidence_would_exceed_budget(turn):
    receipt = _help_receipt("ready accepted confirmed control_policy")
    turn.results = [{"tool": "ninebot__search_help", "error": False, "result": receipt}]
    full = turn.response()
    del full["answer"]
    with patch.object(
        ask, "MAX_RESPONSE_BYTES", len(json.dumps(full, ensure_ascii=False).encode())
    ):
        assert turn.response() == full


def test_sign_in_502_help_displays_reviewed_guidance_and_canonical_citation(turn):
    receipt = _help_receipt("login 502")
    help_data = search_help("login 502", "en", "2.1.27", include_procedures=True)
    receipt["data"] = help_data
    assert help_data["procedures"][0]["id"] == "SK07"
    match = help_data["matches"][0]
    assert match["source_id"] == "authentication_support"
    turn.results = [{"tool": "ninebot__search_help", "error": False, "result": receipt}]
    answer = turn.response()["answer"]["text"]
    assert match["citation"] in answer
    assert "native integration DEBUG logging" in answer and "omit credentials" in answer
    assert "does not establish a cloud region or internal authentication cause" in answer
