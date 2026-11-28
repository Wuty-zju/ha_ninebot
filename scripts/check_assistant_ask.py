"""Actual Action/AI Task/LLM path with a synthetic provider; no model or cloud."""

import asyncio
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

INSTALLED = json.loads(
    (Path(__file__).resolve().parents[1] / "custom_components/ninebot/manifest.json").read_text()
)["version"]


async def check(directory: str) -> None:
    from homeassistant.components import ai_task
    from homeassistant.components.ai_task.const import DATA_COMPONENT, DATA_PREFERENCES
    from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
    from homeassistant.core import Context, HomeAssistant, ServiceCall
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import llm
    from homeassistant.helpers.entity_component import EntityComponent
    from homeassistant.util import dt as dt_util
    from probatio import to_openapi

    from custom_components.ninebot.assistant import async_sync_task_policy
    from custom_components.ninebot.assistant_access import (
        READ_STATISTICS,
        READ_STATUS,
        READ_SYNC,
        READ_TRIPS,
        TASK_REQUEST,
        AssistantAccess,
        VehicleAccess,
        with_assistant_access,
    )
    from custom_components.ninebot.assistant_api import NinebotTool, async_register
    from custom_components.ninebot.assistant_ask import EvidenceTurn, failed, turn_api
    from custom_components.ninebot.assistant_catalog import QUERY_SPECS
    from custom_components.ninebot.models import (
        Freshness,
        VehicleProfile,
        VehicleSnapshot,
        VehicleStatus,
    )
    from scripts.check_assistant_followups import check as check_followups
    from scripts.check_assistant_followups import check_period_metrics

    hass = HomeAssistant(directory)
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    entity_id = "ai_task.synthetic_ninebot"
    principal = SimpleNamespace(
        id="synthetic-admin",
        name="Synthetic administrator",
        is_admin=True,
        is_active=True,
        permissions=SimpleNamespace(
            access_all_entities=lambda policy: True,
            check_entity=lambda target, policy: target == entity_id,
        ),
    )

    async def user(user_id):
        return principal if user_id == principal.id else None

    hass.auth = SimpleNamespace(async_get_user=user)
    now = dt_util.utcnow()
    entry = ConfigEntry(
        version=3,
        minor_version=1,
        domain="ninebot",
        title="Synthetic",
        entry_id="synthetic-native-query",
        data={},
        options={"enable_assistant": True, "assistant_vehicles": ["synthetic-sn"]},
        source="user",
        unique_id="synthetic-native-query",
        state=ConfigEntryState.LOADED,
        discovery_keys=MappingProxyType({}),
        subentries_data=[],
        created_at=now,
        modified_at=now,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    stamp = Freshness(attempted_at=now, succeeded_at=now)
    snapshot = VehicleSnapshot(
        VehicleProfile("synthetic-sn", "Vehicle A", "Synthetic"),
        status=VehicleStatus(battery=42, latitude=30.12345, longitude=120.12345),
        status_freshness=stamp,
        battery_freshness=stamp,
        profile_freshness=stamp,
    )
    co = SimpleNamespace(
        _generation=0,
        _ownership={"synthetic-sn": 0},
        _stopping=False,
        data={"synthetic-sn": snapshot},
        statistics=SimpleNamespace(archive_ready=True),
        local_vehicle_available=lambda sn: sn in co.data,
        fresh=lambda sn, group: True,
    )
    entry.runtime_data = SimpleNamespace(
        coordinator=co, assistant_policy_options=None, assistant_policy_revision=0
    )
    hass.data[dr.DATA_REGISTRY] = dr.DeviceRegistry(hass)
    await dr.async_load(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("ninebot", "synthetic-sn")}, name="Vehicle A"
    )
    await er.async_load(hass)
    component = EntityComponent(logging.getLogger(__name__), "ai_task", hass)
    hass.data[DATA_COMPONENT] = component
    preferences = ai_task.AITaskPreferences(hass)
    await preferences.async_load()
    hass.data[DATA_PREFERENCES] = preferences
    current = {}
    provider_calls = []
    all_reads = frozenset({READ_STATUS, READ_STATISTICS, READ_TRIPS, READ_SYNC})
    all_queries = {f"ninebot__{spec.key}" for spec in QUERY_SPECS}

    def configure(reads):
        hass.config_entries.async_update_entry(
            entry,
            options=with_assistant_access(
                entry.options,
                AssistantAccess(
                    enabled=True,
                    vehicles={"synthetic-sn": VehicleAccess(reads=reads, tasks=TASK_REQUEST)},
                ),
            ),
        )
        async_sync_task_policy(hass, entry)

    class SyntheticTask(ai_task.AITaskEntity):
        _attr_supported_features = ai_task.AITaskEntityFeature.GENERATE_DATA
        _attr_name = "Synthetic task"

        async def _async_generate_data(self, task, chat_log):
            try:
                return await self._async_generate_test_data(task, chat_log)
            except Exception as error:
                current["provider_exception"] = error
                raise

        async def _async_generate_test_data(self, task, chat_log):
            if "stat_args" in current:
                await chat_log.llm_api.async_call_tool(
                    llm.ToolInput(
                        tool_name="ninebot__get_statistics", tool_args=current["stat_args"]
                    )
                )
                return ai_task.GenDataTaskResult(
                    conversation_id=chat_log.conversation_id, data="unverified model text"
                )
            provider_calls.append(current["context"])
            assert task.llm_api is not None and chat_log.llm_api is not None
            assert chat_log.llm_api.llm_context.context is current["context"]
            assert {tool.name for tool in chat_log.llm_api.tools} == current["visible_queries"]
            assert "request_detail_sync_approval" not in chat_log.llm_api.api_prompt
            for tool in chat_log.llm_api.tools:
                schema = to_openapi(
                    tool.parameters,
                    custom_serializer=chat_log.llm_api.custom_serializer,
                    openapi_version="3.1.0",
                )
                assert "vehicle_ref" not in schema.get("properties", {})
            public_tool = NinebotTool(
                next(spec for spec in QUERY_SPECS if spec.key == "get_vehicle_status")
            )
            public_schema = to_openapi(
                public_tool.parameters,
                custom_serializer=chat_log.llm_api.custom_serializer,
                openapi_version="3.1.0",
            )
            assert "vehicle_ref" in public_schema["required"]
            value = await chat_log.llm_api.async_call_tool(
                llm.ToolInput(tool_name="ninebot__get_vehicle_status", tool_args={})
            )
            assert not value.error and value.data["data"]["battery_soc_percent"] == 42
            value.data["data"]["battery_soc_percent"] = 9999  # Provider-side formatting.
            if current.get("revoke"):
                principal.is_active = False
            if current.get("regrant"):
                configure(frozenset({READ_STATISTICS}))
                configure(all_reads)
            if current.get("provider_failure"):
                raise HomeAssistantError("private provider detail: never-return-this")
            return ai_task.GenDataTaskResult(
                conversation_id=chat_log.conversation_id, data="Invented battery value: 9999"
            )

    entity = SyntheticTask()
    entity.entity_id = entity_id
    remove = None
    try:
        await component.async_add_entities([entity])
        assert component.get_entity(entity_id) is entity
        remove = async_register(hass)
        cases = (
            (False, False, False, False),
            (True, False, False, False),
            (True, True, False, False),
            (False, True, False, False),
            (False, False, True, False),
            (False, False, False, True),
        )
        for provider_failure, revoke, narrowed, regrant in cases:
            principal.is_active = True
            configure(frozenset({READ_STATUS}) if narrowed else all_reads)
            context = Context(user_id=principal.id)
            current.update(
                context=context,
                revoke=revoke,
                regrant=regrant,
                provider_failure=provider_failure,
                provider_exception=None,
                visible_queries={
                    "ninebot__get_vehicles",
                    "ninebot__search_help",
                    "ninebot__get_vehicle_status",
                }
                if narrowed
                else all_queries,
            )

            def observed_failure(code):
                if code == "model_task_failed":
                    raise AssertionError("Synthetic native task failed") from current.get(
                        "provider_exception"
                    )
                return failed(code)

            with (
                patch(
                    "custom_components.ninebot.assistant_queries.async_get_integration",
                    new=AsyncMock(return_value=SimpleNamespace(version=INSTALLED)),
                ),
                patch(
                    "custom_components.ninebot.assistant_ask.failed", side_effect=observed_failure
                ),
            ):
                response = await hass.services.async_call(
                    "ninebot",
                    "ask",
                    {
                        "question": "Battery?",
                        "device_id": device.id,
                        "ai_task_entity_id": entity_id,
                    },
                    context=context,
                    blocking=True,
                    return_response=True,
                )
            assert response is not None
            encoded = json.dumps(response)
            assert "Invented battery value: 9999" not in encoded
            assert "never-return-this" not in encoded
            if revoke or regrant:
                assert response["outcome"] == "denied" and response["results"] == []
            else:
                assert response["outcome"] == ("partial" if provider_failure else "evidence"), (
                    response
                )
                if provider_failure:
                    assert response["stop_reason"] == "model_task_failed"
                assert response["results"][0]["result"]["data"]["battery_soc_percent"] == 42
                assert response["results"][0]["arguments"] == {"vehicle_ref": device.id}
                assert not any(
                    secret in encoded for secret in ("synthetic-sn", "30.12345", "120.12345")
                )
        assert len(provider_calls) == len(cases)
        configure(all_reads)
        other_sn = "second-synthetic-sn"
        co.data[other_sn] = VehicleSnapshot(VehicleProfile(other_sn, "Vehicle A", "Synthetic"))
        other = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("ninebot", other_sn)}, name="Vehicle A"
        )
        hass.config_entries.async_update_entry(
            entry,
            options=with_assistant_access(
                entry.options,
                AssistantAccess(
                    True,
                    {
                        "synthetic-sn": VehicleAccess(frozenset({READ_STATISTICS})),
                        other_sn: VehicleAccess(frozenset({READ_STATUS})),
                    },
                ),
            ),
        )
        principal.is_active = True
        context = Context(user_id=principal.id)
        for target, expected in (
            (None, set()),
            (device.id, {"ninebot__get_statistics", "ninebot__explain_statistics"}),
            (other.id, {"ninebot__get_vehicle_status"}),
        ):
            call = ServiceCall(
                hass, "ninebot", "ask", {"device_id": target} if target else {}, context=context
            )
            turn = EvidenceTurn(hass, call)
            await turn.async_prepare()
            instance = await turn_api(turn).async_get_api_instance(
                llm.LLMContext(
                    platform="ai_task",
                    context=context,
                    language="en",
                    assistant="conversation",
                    device_id=None,
                )
            )
            assert turn.business_date in instance.api_prompt
            assert {tool.name for tool in instance.tools} == expected | {
                "ninebot__get_vehicles",
                "ninebot__search_help",
            }
            reply = turn.response()
            assert reply["outcome"] == "needs_input"
            if target is None:
                choices = reply["next_step"]["choices"]
                assert {item["device_id"] for item in choices} == {device.id, other.id}
                assert all(item["label"] == "Vehicle A" for item in choices)
            else:
                assert reply["selection"] == {
                    "device_id": target,
                    "allowed_reads": ["statistics" if target == device.id else "status"],
                    "business_date": turn.business_date,
                    "business_timezone": "Asia/Shanghai",
                }
        configure(frozenset())
        response = await hass.services.async_call(
            "ninebot",
            "ask",
            {"question": "Battery?", "device_id": device.id, "ai_task_entity_id": entity_id},
            context=Context(user_id=principal.id),
            blocking=True,
            return_response=True,
        )
        assert response["outcome"] == "denied" and len(provider_calls) == len(cases)
        response = await hass.services.async_call(
            "ninebot",
            "ask",
            {"question": "Battery?", "device_id": device.id, "ai_task_entity_id": entity_id},
            blocking=True,
            return_response=True,
        )
        assert response["outcome"] == "denied" and len(provider_calls) == len(cases)

        async def followup(question, args, selection):
            current["stat_args"] = args
            return await hass.services.async_call(
                "ninebot",
                "ask",
                {"question": question, "ai_task_entity_id": entity_id, **selection},
                context=Context(user_id=principal.id),
                blocking=True,
                return_response=True,
            )

        await check_followups(hass, entry, co, device, other, followup)
        await check_period_metrics(hass, entry, device, other, followup)
        print("Native query: same-name vehicles, day/month/energy follow-ups and fresh grants OK")
    finally:
        if remove is not None:
            remove()
        await entity.async_remove(force_remove=True)
        await hass.async_stop(force=True)


async def main() -> None:
    with TemporaryDirectory(prefix="ninebot-native-query-") as directory:
        await check(directory)


if __name__ == "__main__":
    asyncio.run(main())
