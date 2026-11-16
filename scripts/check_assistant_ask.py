"""Actual Action/AI Task/LLM path with a synthetic provider; no model or cloud."""

import asyncio
import json
import logging
import sys
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
    from homeassistant.components.mcp_server.server import _format_tool
    from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
    from homeassistant.core import Context, HomeAssistant
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import llm
    from homeassistant.helpers.entity_component import EntityComponent
    from homeassistant.util import dt as dt_util

    from custom_components.ninebot.assistant_api import NinebotTool, async_register
    from custom_components.ninebot.assistant_ask import failed
    from custom_components.ninebot.assistant_queries import SPECS
    from custom_components.ninebot.models import (
        Freshness,
        VehicleProfile,
        VehicleSnapshot,
        VehicleStatus,
    )

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
    entry.runtime_data = SimpleNamespace(coordinator=co)
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

    class SyntheticTask(ai_task.AITaskEntity):
        _attr_supported_features = ai_task.AITaskEntityFeature.GENERATE_DATA
        _attr_name = "Synthetic task"

        async def _async_generate_data(self, task, chat_log):
            provider_calls.append(current["context"])
            assert task.llm_api is not None and chat_log.llm_api is not None
            assert chat_log.llm_api.llm_context.context is current["context"]
            assert len(chat_log.llm_api.tools) == 8
            assert all(tool.name.startswith("ninebot__") for tool in chat_log.llm_api.tools)
            for tool in chat_log.llm_api.tools:
                schema = _format_tool(tool, chat_log.llm_api.custom_serializer).inputSchema
                assert "vehicle_ref" not in schema.get("properties", {})
            public_tool = NinebotTool(
                next(spec for spec in SPECS if spec.key == "get_vehicle_status")
            )
            public_schema = _format_tool(
                public_tool, chat_log.llm_api.custom_serializer
            ).inputSchema
            assert "vehicle_ref" in public_schema["required"]
            value = await chat_log.llm_api.async_call_tool(
                llm.ToolInput(tool_name="ninebot__get_vehicle_status", tool_args={})
            )
            assert not value.error and value.data["data"]["battery_soc_percent"] == 42
            value.data["data"]["battery_soc_percent"] = 9999  # Provider-side formatting.
            if current.get("revoke"):
                principal.is_active = False
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
        for provider_failure, revoke in (
            (False, False),
            (True, False),
            (True, True),
            (False, True),
        ):
            principal.is_active = True
            context = Context(user_id=principal.id)
            current.update(context=context, revoke=revoke, provider_failure=provider_failure)

            def observed_failure(code):
                if code == "model_task_failed":
                    raise AssertionError("Synthetic native task failed") from sys.exception()
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
            assert response is not None and "9999" not in json.dumps(response)
            assert "never-return-this" not in json.dumps(response)
            if revoke:
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
                    secret in json.dumps(response)
                    for secret in ("synthetic-sn", "30.12345", "120.12345")
                )
        assert len(provider_calls) == 4
        response = await hass.services.async_call(
            "ninebot",
            "ask",
            {"question": "Battery?", "device_id": device.id, "ai_task_entity_id": entity_id},
            blocking=True,
            return_response=True,
        )
        assert response["outcome"] == "denied" and len(provider_calls) == 4
        print("Native query: Context, eight tools, partial evidence and withdrawal passed")
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
