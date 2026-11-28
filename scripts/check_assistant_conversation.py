"""Replay provider-independent native Conversation turns with scripted deltas.

Core owns ChatLog, API caching and tool execution. Cancellation here interrupts
tool I/O, independently of provider-generation cancellation. The existing MCP
canary supplies invented accounts and cached data. No model is called, so these
checks establish host contracts rather than model answer quality.
"""

import asyncio
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch


async def check(fixture: SimpleNamespace) -> None:
    from homeassistant.components.conversation import ChatLog, ConversationInput, ConverseError
    from homeassistant.components.conversation.chat_log import (
        SystemContent,
        ToolResultContent,
        UserContent,
    )
    from homeassistant.core import Context, SupportsResponse
    from homeassistant.helpers import llm
    from homeassistant.helpers.json import json_dumps
    from homeassistant.util import dt as dt_util

    from custom_components.ninebot import assistant_policy, assistant_queries
    from custom_components.ninebot.archive_statistics import ArchiveStatisticsView
    from custom_components.ninebot.assistant_api import PROMPT
    from custom_components.ninebot.assistant_catalog import QUERY_SPECS
    from custom_components.ninebot.assistant_limits import DATA_KEY
    from custom_components.ninebot.statistics_actions import months_between, statistics_response
    from scripts.check_assistant_followups import PERIODS

    hass = fixture.hass
    log = ChatLog(hass, "synthetic-conversation", content=[SystemContent("")])
    current_context: Context | None = None
    authorized_contexts: list[Context] = []
    action_contexts: list[Context] = []
    sequence = 0

    async def turn(
        text: str,
        *,
        prompt: str = "Answer the user's question using the selected tools.",
        extra_system_prompt: str | None = None,
        selected_api: str | list[str] = "ninebot",
    ) -> None:
        nonlocal current_context
        current_context = Context(user_id="synthetic-admin")
        user_input = ConversationInput(
            text=text,
            context=current_context,
            conversation_id=log.conversation_id,
            device_id=None,
            satellite_id=None,
            language="en",
            agent_id="conversation.synthetic",
            extra_system_prompt=extra_system_prompt,
        )
        log.async_add_user_content(UserContent(text))
        await log.async_provide_llm_data(
            user_input.as_llm_context("synthetic_provider"),
            selected_api,
            prompt,
            user_input.extra_system_prompt,
        )
        assert log.llm_api is not None
        assert log.llm_api.llm_context.context is current_context

    async def authorize(actual_hass, context, target, **kwargs):
        assert actual_hass is hass and context is current_context
        authorized_contexts.append(context)
        # Use the real policy; only the canary's MCP-specific Context assertion
        # is replaced while the native Conversation path is under test.
        return await assistant_policy.async_authorize(actual_hass, context, target, **kwargs)

    async def stream_call(name: str, arguments: dict) -> ToolResultContent:
        nonlocal sequence
        sequence += 1
        call = llm.ToolInput(id=f"synthetic-call-{sequence}", tool_name=name, tool_args=arguments)

        async def deltas():
            yield {"role": "assistant"}
            yield {"tool_calls": [call]}

        contents = [
            item
            async for item in log.async_add_delta_content_stream("conversation.synthetic", deltas())
        ]
        results = [item for item in contents if isinstance(item, ToolResultContent)]
        assert len(results) == 1
        result = results[0]
        assert result.tool_call_id == call.id and result.tool_name == name
        assert isinstance(result.result, llm.ToolResult)
        return result

    async def statistics(call):
        assert call.context is current_context
        assert call.data["device_id"] == fixture.vehicle_ref
        assert call.data["refresh"] is False
        action_contexts.append(call.context)
        return statistics_response(
            ArchiveStatisticsView(),
            "synthetic-vehicle",
            months_between(call.data["start_month"], call.data["end_month"]),
            dt_util.utcnow(),
            call.data["include_daily"],
        )

    with patch.object(assistant_queries, "async_authorize", side_effect=authorize):
        await turn("Which vehicle can I ask about?")
        assert PROMPT in log.content[0].content
        instance = log.llm_api
        queries = {f"ninebot__{spec.key}" for spec in QUERY_SPECS}
        assert {tool.name for tool in instance.tools} == queries
        roster = await stream_call("ninebot__get_vehicles", {})
        assert not roster.result.error
        assert len(roster.result.data["data"]["vehicles"]) == 2

        before = len(authorized_contexts)

        async def clarification():
            yield {"role": "assistant", "content": "Which vehicle do you mean?"}

        clarification_messages = [
            item
            async for item in log.async_add_delta_content_stream(
                "conversation.synthetic", clarification()
            )
        ]
        assert len(clarification_messages) == 1
        assert len(authorized_contexts) == before

        previous_context = current_context
        await turn("Use Vehicle A. What is its battery level?")
        assert log.llm_api is instance and current_context is not previous_context
        status = await stream_call(
            "ninebot__get_vehicle_status", {"vehicle_ref": fixture.vehicle_ref}
        )
        assert not status.result.error
        assert status.result.data["data"]["battery_soc_percent"] == 42
        assert status.result.data["data"]["charging"] is False
        assert authorized_contexts[-1] is current_context

        cached_prompt, previous_context = log.content[0].content, current_context
        await turn("Keep this conversation.", prompt="Changed configuration prompt.")
        assert log.llm_api is instance and log.content[0].content == cached_prompt
        assert current_context is not previous_context

        selected_ride = (
            "Current view selection: ride_id=synthetic-visible-ride; query_month=202611."
        )
        await turn("Show the selected trip.", extra_system_prompt=selected_ride)
        assert log.llm_api is not instance
        assert selected_ride in log.content[0].content and PROMPT in log.content[0].content
        instance = log.llm_api

        snapshot = fixture.coordinator.data["synthetic-vehicle"]
        fixture.coordinator.data["synthetic-vehicle"] = replace(
            snapshot, status=replace(snapshot.status, battery=0)
        )
        try:
            with patch.object(fixture.coordinator, "fresh", return_value=False):
                stale = await stream_call(
                    "ninebot__get_vehicle_status", {"vehicle_ref": fixture.vehicle_ref}
                )
            assert not stale.result.error
            assert stale.result.data["data"]["battery_soc_percent"] == 0
            assert stale.result.data["quality"]["groups"]["status"]["status"] == "stale"
            assert stale.result.data["quality"]["groups"]["status"]["received_at"]
        finally:
            fixture.coordinator.data["synthetic-vehicle"] = snapshot

        invalid = await stream_call(
            "ninebot__get_vehicle_status",
            {"vehicle_ref": fixture.vehicle_ref, "refresh": True},
        )
        assert invalid.result.error
        assert invalid.result.data["error"]["code"] == "invalid_arguments"

        hass.services.async_register(
            "ninebot", "get_statistics", statistics, supports_response=SupportsResponse.ONLY
        )
        await turn("How many trips yesterday for Vehicle A?")
        missing = await stream_call(
            "ninebot__get_statistics", {"vehicle_ref": fixture.vehicle_ref, "period": "yesterday"}
        )
        assert not missing.result.error
        assert missing.result.data["data"]["ride_count"] is None
        assert action_contexts[-1] is current_context

        # The host keeps the chat log. Every call in the next turn uses a new HA Context.
        async def other_statistics(call):
            assert call.context is current_context
            assert call.data["device_id"] == fixture.other_ref
            assert call.data["refresh"] is False
            action_contexts.append(call.context)
            if call.data["include_daily"]:
                return {"days": [{"date": "2026-10-01", "ride_count": 2}]}
            month = call.data["start_month"]
            return {"months": [{"month": month, "energy_wh": PERIODS["last_month"]["energy_wh"]}]}

        hass.services.async_register(
            "ninebot", "get_statistics", other_statistics, supports_response=SupportsResponse.ONLY
        )
        followup = {
            "vehicle_ref": fixture.vehicle_ref,
            "metric": "ride_count",
            "period": "2026-10-01",
            "other_vehicle": True,
        }
        await turn("另一辆呢？")
        other = await stream_call("ninebot__get_statistics", followup)
        assert not other.result.error
        assert other.result.data["query_context"] == {
            "device_id": fixture.other_ref,
            "date": "2026-10-01",
            "metric": "ride_count",
        }
        compound = {
            "vehicle_ref": fixture.vehicle_ref,
            "period": "2026-11-05",
            "metric": "ride_count",
            "change": {"vehicle": "other", "period": "last_month", "metric": "energy_wh"},
        }
        before = len(action_contexts)
        with patch.object(dt_util, "utcnow", return_value=datetime(2026, 11, 5, tzinfo=UTC)):
            await turn("那另一辆车上个月耗电多少？")
            combined = await stream_call("ninebot__get_statistics", compound)
        assert not combined.result.error and len(action_contexts) == before + 1
        assert combined.result.data["data"] == {
            "energy_wh": PERIODS["last_month"]["energy_wh"],
            "units": {"energy_wh": "Wh"},
        }
        assert combined.result.data["query_context"] == {
            "device_id": fixture.other_ref,
            "metric": "energy_wh",
            "month": "2026-10",
            "through_date": "2026-10-31",
        }
        month_args = {
            "vehicle_ref": fixture.other_ref,
            "metric": "ride_count",
            "period": "2026-10",
            "change": "energy_wh",
        }
        count = len(action_contexts)
        moved = await stream_call(
            "ninebot__get_statistics", {**month_args, "through_date": "2026-10-01"}
        )
        assert moved.result.error and moved.result.data["error"]["code"] == "period_changed"
        assert len(action_contexts) == count
        called = len(action_contexts)
        fixture.entry.options["assistant_vehicles"].remove(fixture.other_sn)
        try:
            await turn("再看下另一辆呢？")
            absent = await stream_call("ninebot__get_statistics", compound)
            assert absent.result.error and "data" not in absent.result.data
            assert absent.result.data["error"]["code"] == "no_other_vehicle"
            assert len(action_contexts) == called
        finally:
            fixture.entry.options["assistant_vehicles"].append(fixture.other_sn)

        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def blocked_statistics(call):
            assert call.context is current_context
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        hass.services.async_register(
            "ninebot", "get_statistics", blocked_statistics, supports_response=SupportsResponse.ONLY
        )
        pending = asyncio.create_task(
            stream_call(
                "ninebot__get_statistics",
                {"vehicle_ref": fixture.vehicle_ref, "period": "yesterday"},
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending
            await asyncio.wait_for(cancelled.wait(), timeout=2)
            assert hass.data[DATA_KEY].active == 0
        finally:
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending

        async def revoke_in_read(call):
            response = await statistics(call)
            fixture.entry.options["enable_assistant"] = False
            return response

        hass.services.async_register(
            "ninebot", "get_statistics", revoke_in_read, supports_response=SupportsResponse.ONLY
        )
        try:
            denied = await stream_call(
                "ninebot__get_statistics",
                {"vehicle_ref": fixture.vehicle_ref, "period": "yesterday"},
            )
            assert denied.result.error
            assert denied.result.data["error"]["code"] == "not_authorized"
            assert "data" not in denied.result.data
        finally:
            fixture.entry.options["enable_assistant"] = True

        user = await hass.auth.async_get_user("synthetic-admin")
        user.is_active = False
        try:
            await turn("Read the battery again.")
            assert log.llm_api is instance
            inactive = await stream_call(
                "ninebot__get_vehicle_status", {"vehicle_ref": fixture.vehicle_ref}
            )
            assert inactive.result.error
            assert inactive.result.data["error"]["code"] == "not_authorized"
        finally:
            user.is_active = True

        first, last = fixture.companion_ids
        for stale in ([first, "ninebot", "ninebot_tasks", last], ["ninebot_tasks"]):
            try:
                await turn("Prepare the saved tool selection.", selected_api=stale)
            except ConverseError as err:
                assert str(err.__cause__) == "API ninebot_tasks not found"
            else:
                raise AssertionError("Conversation unexpectedly accepted the retired API ID")
        await turn("Prepare the revised selection.", selected_api=[first, "ninebot", last])
        assert {tool.name for tool in log.llm_api.tools} == queries | {
            f"{first}__probe",
            f"{last}__probe",
        }

    serialized = json_dumps([item.as_dict() for item in log.content])
    assert not any(
        value in serialized
        for value in (
            "private-fixture-account",
            "synthetic-vehicle",
            '"latitude"',
            '"longitude"',
            "30.12345",
            "120.12345",
        )
    )
    assert hass.data[DATA_KEY].active == 0
    print(
        "Native Conversation: eight tools, clarification, turn Context/view updates, quality, "
        "Action response, tool-I/O cancellation, cached-API revocation and saved selection OK"
    )
