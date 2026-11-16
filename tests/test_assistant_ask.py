"""Local evidence orchestration boundaries; providers are tested by native canaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import Context, ServiceCall

from custom_components.ninebot import assistant_ask as ask
from custom_components.ninebot.assistant_policy import AssistantError


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
    result.scopes = {"selected": SimpleNamespace(async_validate=AsyncMock())}
    return result


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


async def test_multi_vehicle_requires_selection_before_read(turn):
    turn.selected = None
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


async def test_wrong_vehicle_and_context_never_dispatch(turn):
    base = SimpleNamespace(async_call=AsyncMock())
    with patch.object(ask.EvidenceTurn, "async_validate", new=AsyncMock()):
        for args, context in (({"vehicle_ref": "other"}, turn.call.context), ({}, Context())):
            with pytest.raises(AssistantError):
                await turn.async_call(
                    base, SimpleNamespace(tool_args=args), SimpleNamespace(context=context)
                )
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
