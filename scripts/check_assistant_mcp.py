"""Replay the installed HA MCP HTTP handler with synthetic authorization and data.

The ephemeral aiohttp test listener runs only in CI. HA's request handler,
MCP dispatcher, native API and Ninebot consent checks are real; bearer lookup,
config entries, device registry and vehicle snapshots are synthetic. This does
not exercise production HA, OAuth, a model provider or the Ninebot cloud.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import ExitStack, nullcontext
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


async def main() -> None:
    with TemporaryDirectory(prefix="ninebot-mcp-") as directory:
        await check(directory)


async def check(
    config_dir: str,
    scenario: Callable[..., Awaitable[None]] | None = None,
    *,
    reference_time: datetime | None = None,
) -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from homeassistant.components.http import KEY_HASS
    from homeassistant.components.mcp_server import http as native_http
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import llm
    from homeassistant.helpers.http import KEY_AUTHENTICATED
    from homeassistant.util import dt as dt_util

    from custom_components.ninebot import assistant_queries
    from custom_components.ninebot.assistant_api import NinebotReadAPI, async_register
    from custom_components.ninebot.compat import validation
    from custom_components.ninebot.models import (
        Freshness,
        VehicleProfile,
        VehicleSnapshot,
        VehicleStatus,
    )

    hass = HomeAssistant(config_dir)
    users = {
        "fixture-admin": SimpleNamespace(id="synthetic-admin", is_admin=True, is_active=True),
        "fixture-member": SimpleNamespace(id="synthetic-member", is_admin=False, is_active=True),
    }
    for user in users.values():
        user.permissions = SimpleNamespace(access_all_entities=lambda _permission: True)
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(
            side_effect=lambda user_id: next(
                (user for user in users.values() if user.id == user_id), None
            )
        )
    )
    sn, vehicle_ref, entry_id = "synthetic-vehicle", "synthetic-device", "synthetic-account"
    now = reference_time or dt_util.utcnow()
    observed_at = now - timedelta(seconds=20) if reference_time is not None else now
    stamp = Freshness(attempted_at=observed_at, succeeded_at=observed_at)
    snapshot = VehicleSnapshot(
        VehicleProfile(sn, "Vehicle A", "Fixture"),
        status=VehicleStatus(
            battery=42, charging=False, locked=True, latitude=30.12345, longitude=120.12345
        ),
        profile_freshness=stamp,
        status_freshness=stamp,
        battery_freshness=stamp,
    )
    co = SimpleNamespace(
        _generation=0,
        _ownership={sn: 0},
        _stopping=False,
        data={sn: snapshot},
        statistics=SimpleNamespace(archive_ready=True),
        local_vehicle_available=lambda target: target in co.data,
        fresh=lambda target, group: target in co.data and group in {"status", "battery", "profile"},
    )
    entry = SimpleNamespace(
        domain="ninebot",
        entry_id=entry_id,
        state=ConfigEntryState.LOADED,
        options={"enable_assistant": True, "assistant_vehicles": [sn]},
        runtime_data=SimpleNamespace(coordinator=co),
        data={"account": "private-fixture-account"},
    )
    device = SimpleNamespace(
        id=vehicle_ref,
        config_entry_id=entry_id,
        identifiers={("ninebot", sn)},
        disabled_by=None,
        parent_device_id=None,
        name_by_user=None,
    )
    devices = {vehicle_ref: device}
    if scenario is not None:
        other_sn, other_ref = "synthetic-vehicle-b", "synthetic-device-b"
        co.data[other_sn] = VehicleSnapshot(VehicleProfile(other_sn, "Vehicle B", "Fixture"))
        co._ownership[other_sn] = 0
        entry.options["assistant_vehicles"].append(other_sn)
        devices[other_ref] = SimpleNamespace(
            id=other_ref,
            config_entry_id=entry_id,
            identifiers={("ninebot", other_sn)},
            disabled_by=None,
            parent_device_id=None,
            name_by_user=None,
        )
    registry = SimpleNamespace(async_get=devices.get)
    mcp_entry = SimpleNamespace(
        data={
            "all_llm_apis": False,
            "llm_hass_api": ["ninebot"],
            "require_admin": True,
            "read_only": True,
        }
    )
    hass.config_entries = SimpleNamespace(
        async_loaded_entries=lambda domain: [mcp_entry] if domain == "mcp_server" else [],
        async_entries=lambda domain: [entry] if domain == "ninebot" else [],
        async_get_entry=lambda target: entry if target == entry_id else None,
    )

    @web.middleware
    async def synthetic_auth(request, handler):
        user = users.get(request.headers.get("Authorization", "").removeprefix("Bearer "))
        if user is None:
            raise web.HTTPUnauthorized()
        request["hass_user"] = user
        request[KEY_AUTHENTICATED] = True
        return await handler(request)

    app = web.Application(middlewares=[synthetic_auth])
    app[KEY_HASS] = hass
    native_http.ModelContextProtocolStreamableView().register(hass, app, app.router)
    contexts = []
    original_create = native_http.create_mcp_server
    original_authorize = assistant_queries.async_authorize
    original_instance = NinebotReadAPI.async_get_api_instance
    writes = []

    class SyntheticWriteTool(llm.Tool):
        name = "ninebot__synthetic_write"
        integration = "ninebot"
        parameters = validation.Schema({})
        annotations = llm.ToolAnnotations(read_only=False)

        async def async_call(self, actual_hass, tool_input, llm_context):
            writes.append(True)
            raise AssertionError("Read-only MCP must remove synthetic write tools")

    async def add_write_probe(api, context):
        instance = await original_instance(api, context)
        instance.tools.append(SyntheticWriteTool())
        return instance

    async def capture_context(request, context, *args):
        contexts.append(context)
        return await original_create(request, context, *args)

    async def verify_context(actual_hass, context, target):
        assert actual_hass is hass and context is contexts[-1]
        assert context.user_id == "synthetic-admin"
        return await original_authorize(actual_hass, context, target)

    remove = async_register(hass)

    def unregister():
        nonlocal remove
        if remove is not None:
            callback, remove = remove, None
            callback()

    with (
        ExitStack() as cleanup,
        patch.object(dt_util, "utcnow", return_value=now)
        if reference_time is not None
        else nullcontext(),
        patch.object(native_http, "create_mcp_server", side_effect=capture_context),
        patch.object(assistant_queries, "async_authorize", side_effect=verify_context),
        patch.object(
            assistant_queries,
            "async_get_integration",
            new=AsyncMock(return_value=SimpleNamespace(version="2.1.0")),
        ),
        patch.object(NinebotReadAPI, "async_get_api_instance", new=add_write_probe),
        patch("custom_components.ninebot.services.dr.async_get", return_value=registry),
        patch(
            "custom_components.ninebot.assistant_queries.dr.async_entries_for_config_entry",
            side_effect=lambda _registry, owner: (
                list(devices.values()) if owner == entry_id else []
            ),
        ),
    ):
        cleanup.callback(unregister)
        async with TestClient(TestServer(app)) as client:
            sequence = 0

            async def rpc(method, params=None, token="fixture-admin"):
                nonlocal sequence
                sequence += 1
                message = {"jsonrpc": "2.0", "id": sequence, "method": method}
                if params is not None:
                    message["params"] = params
                response = await client.post(
                    "/api/mcp",
                    json=message,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {token}",
                    },
                )
                body = await response.text()
                return response.status, json.loads(body) if response.status == 200 else body

            status, _ = await rpc("tools/list", token="missing")
            assert status == 401
            status, _ = await rpc("tools/list", token="fixture-member")
            assert status == 403
            status, body = await rpc("tools/list")
            assert status == 200 and {tool["name"] for tool in body["result"]["tools"]} == {
                f"ninebot__{spec.key}" for spec in assistant_queries.SPECS
            }
            assert all(tool["annotations"]["readOnlyHint"] for tool in body["result"]["tools"])
            status, help_body = await rpc(
                "tools/call",
                {
                    "name": "ninebot__search_help",
                    "arguments": {"query": "not_authorized", "language": "en"},
                },
            )
            assert status == 200 and not help_body["result"].get("isError", False)
            help_data = json.loads(help_body["result"]["content"][0]["text"])
            assert help_data["data"]["matches"][0]["source_id"] == "assistant_access"
            assert "vehicle_ref" not in help_data
            call = {
                "name": "ninebot__get_vehicle_status",
                "arguments": {"vehicle_ref": vehicle_ref},
            }
            status, body = await rpc("tools/call", call)
            assert status == 200 and not body["result"].get("isError", False)
            result = json.loads(body["result"]["content"][0]["text"])
            assert result["data"]["battery_soc_percent"] == 42
            assert result["data"]["charging"] is False
            assert not any(
                value in json.dumps(body)
                for value in (
                    sn,
                    "private-fixture-account",
                    "latitude",
                    "longitude",
                    "30.12345",
                    "120.12345",
                )
            )
            if scenario is not None:
                try:
                    await scenario(
                        SimpleNamespace(
                            hass=hass,
                            entry=entry,
                            coordinator=co,
                            rpc=rpc,
                            vehicle_ref=vehicle_ref,
                            request_count=lambda: sequence,
                        )
                    )
                finally:
                    entry.options["enable_assistant"] = True
            _, filtered = await rpc(
                "tools/call", {"name": "ninebot__synthetic_write", "arguments": {}}
            )
            assert "error" in filtered or filtered["result"].get("isError")
            assert not writes
            users["fixture-admin"].is_active = False
            status, inactive = await rpc("tools/call", call)
            assert status in (401, 403) or (
                status == 200 and ("error" in inactive or inactive["result"].get("isError"))
            )
            users["fixture-admin"].is_active = True
            entry.options["enable_assistant"] = False
            _, denied = await rpc("tools/call", call)
            assert denied["result"]["isError"]
            assert (
                json.loads(denied["result"]["content"][0]["text"])["error"]["code"]
                == "not_authorized"
            )
            _, unknown = await rpc("tools/call", {"name": "ninebot__engine_start", "arguments": {}})
            assert "error" in unknown or unknown["result"].get("isError")
            unregister()
            _, removed = await rpc("tools/list")
            assert "error" in removed
    print("Native MCP HTTP: local read tools, original Context, consent revocation and removal OK")


if __name__ == "__main__":
    asyncio.run(main())
