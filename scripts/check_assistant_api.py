"""Exercise installed native API registration and MCP formatting with synthetic tools.

This canary does not set up Ninebot accounts, contact a model, open an HTTP
listener or use the generated HA test plugin. HTTP transport is tested separately.
"""

import asyncio
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


async def main() -> None:
    with TemporaryDirectory(prefix="ninebot-native-") as directory:
        await check(directory)


async def check(config_dir: str) -> None:
    from homeassistant.components.mcp_server.server import _format_tool, async_get_api_instance
    from homeassistant.core import Context, HomeAssistant
    from homeassistant.exceptions import Unauthorized
    from homeassistant.helpers import llm

    from custom_components.ninebot.assistant_api import async_register, native_result
    from custom_components.ninebot.assistant_queries import SPECS
    from custom_components.ninebot.compat import validation

    context = llm.LLMContext(
        platform="mcp_server",
        context=Context(user_id="synthetic-admin"),
        language="en",
        assistant="conversation",
        device_id=None,
    )
    hass = HomeAssistant(config_dir)
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True, is_active=True))
    )
    unregister = async_register(hass)
    assert [api.id for api in llm.async_get_apis(hass)] == ["ninebot"]
    api = await llm.async_get_api(hass, "ninebot", context)
    assert api.api.requires_admin and api.llm_context is context
    assert [tool.name for tool in api.tools] == [f"ninebot__{spec.key}" for spec in SPECS]
    for tool in api.tools:
        formatted = _format_tool(tool, api.custom_serializer)
        assert formatted.annotations.read_only_hint and not formatted.annotations.open_world_hint
        assert set(formatted.input_schema["properties"]) == {
            marker.schema for marker in tool.parameters.schema
        }
        if tool.name == "ninebot__get_recorded_trips":
            properties = formatted.input_schema["properties"]
            assert properties["limit"]["minimum"] == 1 and properties["limit"]["maximum"] == 20
            assert properties["start_date"]["format"] == "date"
    invalid = await api.async_call_tool(
        llm.ToolInput(
            tool_name="ninebot__get_vehicle_status",
            tool_args={"vehicle_ref": "synthetic", "refresh": True},
        )
    )
    assert invalid.error and invalid.data["error"]["code"] == "invalid_arguments"
    hass.auth.async_get_user.return_value = SimpleNamespace(is_admin=False, is_active=True)
    try:
        await llm.async_get_api(hass, "ninebot", context)
    except Unauthorized:
        pass
    else:
        raise AssertionError("Native collection must require an administrator")
    hass.auth.async_get_user.return_value = SimpleNamespace(is_admin=True, is_active=True)

    class ProbeTool(llm.Tool):
        name = "ninebot__synthetic_probe"
        title = "Synthetic probe"
        integration = "ninebot"
        parameters = validation.Schema({})
        annotations = llm.ToolAnnotations(
            read_only=True, destructive=False, idempotent=True, open_world=False
        )

        async def async_call(self, actual_hass, tool_input, llm_context):
            assert actual_hass is hass and llm_context is context
            return native_result({"zero": 0, "missing": None})

    probe = ProbeTool()
    api.tools.append(probe)
    result = await api.async_call_tool(llm.ToolInput(tool_name=probe.name, tool_args={}))
    assert isinstance(result, llm.ToolResult) and result.data == {"zero": 0, "missing": None}
    formatted = _format_tool(probe, None)
    assert formatted.name == probe.name and formatted.annotations.read_only_hint
    with patch.object(llm, "async_get_api", AsyncMock(return_value=api)):
        readonly = await async_get_api_instance(hass, "ninebot", context, True)
    assert readonly.tools == api.tools
    unregister()
    assert llm.async_get_apis(hass) == []
    print("Native Assistant: registry, admin gate, original Context, ToolResult and MCP schema OK")


if __name__ == "__main__":
    asyncio.run(main())
