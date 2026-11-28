"""Replay the installed HA MCP HTTP handler with synthetic authorization and data.

The ephemeral aiohttp test listener runs in isolated validation environments. HA's request handler,
MCP dispatcher, native API and Ninebot consent checks are real; bearer lookup,
config entries, device registry and vehicle snapshots are synthetic. This does
not exercise production HA, OAuth, a model provider or the Ninebot cloud.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import ExitStack, nullcontext
from datetime import datetime, timedelta
from functools import partial
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

FIXTURE_INTEGRATION_VERSION = "2.1.0"
COMPANION_IDS = ("fixture_a", "fixture_b")


async def main() -> None:
    from scripts.assistant_reader import native_mcp, read_tools
    from scripts.check_assistant_conversation import check as check_conversation

    async def sdk_handshake(fixture):
        async with native_mcp(fixture.mcp_url, "fixture-admin") as backend:
            tools = await backend.tools()
            assert len(read_tools(tools, help_enabled=True)) == 3
            assert "Ninebot" in await backend.prompt()
            roster = await backend.call("ninebot__get_vehicles", {})
            assert not roster.get("error") and len(roster["data"]["vehicles"]) == 2
        print("Official MCP SDK: initialize, tools, prompt, authenticated read and close OK")
        with ExitStack() as cleanup:
            for api_id in COMPANION_IDS:
                cleanup.callback(register_companion_api(fixture.hass, api_id))
            await check_conversation(fixture)
            await check_cutover(fixture)

    with TemporaryDirectory(prefix="ninebot-mcp-") as directory:
        await check(directory, scenario=sdk_handshake)


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
    from homeassistant.components.mcp_server.server import META_DEVICE_ID
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.core import HomeAssistant, SupportsResponse
    from homeassistant.helpers.http import KEY_AUTHENTICATED
    from homeassistant.util import dt as dt_util

    from custom_components.ninebot import assistant_queries, assistant_task_queries
    from custom_components.ninebot.assistant_api import (
        PROMPT,
        NinebotAPI,
        async_register,
    )
    from custom_components.ninebot.assistant_catalog import QUERY_SPECS
    from custom_components.ninebot.models import (
        Freshness,
        VehicleProfile,
        VehicleSnapshot,
        VehicleStatus,
    )

    hass = HomeAssistant(config_dir)
    users = {
        "fixture-admin": SimpleNamespace(id="synthetic-admin", is_admin=True, is_active=True),
        "fixture-task-admin": SimpleNamespace(
            id="synthetic-task-admin", is_admin=True, is_active=True
        ),
        "fixture-member": SimpleNamespace(id="synthetic-member", is_admin=False, is_active=True),
    }
    for user in users.values():
        user.name = "Synthetic user"
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
        runtime_data=SimpleNamespace(
            coordinator=co, assistant_task_blocks=0, assistant_policy_revision=0
        ),
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
    other_sn, other_ref = "synthetic-vehicle-b", "synthetic-device-b"
    if scenario is not None:
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

    http_requests = 0

    @web.middleware
    async def synthetic_auth(request, handler):
        nonlocal http_requests
        http_requests += 1
        user = users.get(request.headers.get("Authorization", "").removeprefix("Bearer "))
        if user is None:
            raise web.HTTPUnauthorized()
        request["hass_user"] = user
        request[KEY_AUTHENTICATED] = True
        return await handler(request)

    app = web.Application(middlewares=[synthetic_auth])
    app[KEY_HASS] = hass
    native_http.ModelContextProtocolStreamableView().register(hass, app, app.router)
    native_http.ModelContextProtocolStreamableApiView().register(hass, app, app.router)
    contexts = []
    original_create = native_http.create_mcp_server
    original_authorize = assistant_queries.async_authorize
    original_instance = NinebotAPI.async_get_api_instance
    metadata_devices = []

    async def capture_api_context(api, context):
        instance = await original_instance(api, context)
        if context.platform == "mcp_server":
            metadata_devices.append(context.device_id)
        return instance

    async def capture_context(request, context, *args):
        contexts.append(context)
        return await original_create(request, context, *args)

    async def verify_context(actual_hass, context, target, **kwargs):
        assert actual_hass is hass and context is contexts[-1]
        assert context.user_id in {"synthetic-admin", "synthetic-task-admin"}
        return await original_authorize(actual_hass, context, target, **kwargs)

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
        patch.object(assistant_task_queries, "async_authorize", side_effect=verify_context),
        patch.object(
            assistant_queries,
            "async_get_integration",
            new=AsyncMock(return_value=SimpleNamespace(version=FIXTURE_INTEGRATION_VERSION)),
        ),
        patch.object(NinebotAPI, "async_get_api_instance", new=capture_api_context),
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

            async def rpc(method, params=None, token="fixture-admin", *, path="/api/mcp"):
                nonlocal sequence
                sequence += 1
                message = {"jsonrpc": "2.0", "id": sequence, "method": method}
                if params is not None:
                    message["params"] = params
                response = await client.post(
                    path,
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
                f"ninebot__{spec.key}" for spec in QUERY_SPECS
            }
            assert all(tool["annotations"]["readOnlyHint"] for tool in body["result"]["tools"])
            for token, expected in (("missing", 401), ("fixture-member", 403)):
                status, _ = await rpc("prompts/get", {"name": "Ninebot"}, token=token)
                assert status == expected
            status, prompts = await rpc("prompts/list")
            assert status == 200 and [row["name"] for row in prompts["result"]["prompts"]] == [
                "Ninebot"
            ]
            status, prompt = await rpc("prompts/get", {"name": "Ninebot"})
            assert status == 200 and prompt["result"]["messages"] == [
                {"role": "assistant", "content": {"type": "text", "text": PROMPT}}
            ]
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

            async def fixture_sync_status(call):
                assert call.context is contexts[-1]
                assert dict(call.data) == {"device_id": vehicle_ref, "operation": "sync_status"}
                return {
                    "sync_state": "tracks_limited",
                    "archive_counts": {
                        "total": 12,
                        "saved_details": 9,
                        "simplified": 2,
                        "details_pending_classification": 1,
                    },
                    "details": {"discovered": 8, "saved": 8},
                }

            hass.services.async_register(
                "ninebot",
                "sync_history",
                fixture_sync_status,
                supports_response=SupportsResponse.ONLY,
            )
            try:
                status, body = await rpc("tools/call", {**call, "name": "ninebot__get_sync_health"})
                assert status == 200 and not body["result"].get("isError", False)
                sync = json.loads(body["result"]["content"][0]["text"])
                assert sync["data"]["archive_counts"]["total"] == 12
                assert sync["data"]["details"]["discovered"] == 8
                assert sync["evidence"]["archive_counts_scope"] == "all_saved_vehicle_trips"
                assert sync["evidence"]["details_scope"] == "discovered_history_plan"
            finally:
                hass.services.async_remove("ninebot", "sync_history")
            # Client-origin device metadata must neither select nor authorize a vehicle.
            hidden_sn, hidden_ref = "synthetic-unexposed-vehicle", "synthetic-unexposed-device"
            co.data[hidden_sn] = VehicleSnapshot(
                VehicleProfile(hidden_sn, "Unexposed fixture", "Fixture"),
                status=VehicleStatus(battery=99),
                status_freshness=stamp,
            )
            co._ownership[hidden_sn] = 0
            devices[hidden_ref] = SimpleNamespace(
                id=hidden_ref,
                config_entry_id=entry_id,
                identifiers={("ninebot", hidden_sn)},
                disabled_by=None,
                parent_device_id=None,
                name_by_user=None,
            )
            try:
                for source_device in (vehicle_ref, hidden_ref):
                    _, missing_target = await rpc(
                        "tools/call",
                        {
                            "name": "ninebot__get_vehicle_status",
                            "arguments": {},
                            "_meta": {META_DEVICE_ID: source_device},
                        },
                    )
                    assert metadata_devices[-1] == source_device
                    assert missing_target["result"]["isError"]
                    _, forbidden = await rpc(
                        "tools/call",
                        {
                            **call,
                            "arguments": {"vehicle_ref": hidden_ref},
                            "_meta": {META_DEVICE_ID: source_device},
                        },
                    )
                    assert metadata_devices[-1] == source_device
                    assert forbidden["result"]["isError"]
                    error = json.loads(forbidden["result"]["content"][0]["text"])
                    assert error["error"]["code"] == "not_authorized" and "data" not in error
                    assert hidden_sn not in json.dumps(forbidden)
                _, explicit_target = await rpc(
                    "tools/call", {**call, "_meta": {META_DEVICE_ID: hidden_ref}}
                )
                assert metadata_devices[-1] == hidden_ref
                assert not explicit_target["result"].get("isError", False)
                value = json.loads(explicit_target["result"]["content"][0]["text"])
                assert value["data"]["battery_soc_percent"] == 42
            finally:
                co.data.pop(hidden_sn)
                co._ownership.pop(hidden_sn)
                devices.pop(hidden_ref)
            if scenario is not None:
                try:
                    await scenario(
                        SimpleNamespace(
                            hass=hass,
                            entry=entry,
                            coordinator=co,
                            rpc=rpc,
                            mcp_url=str(client.make_url("/api/mcp/ninebot")),
                            vehicle_ref=vehicle_ref,
                            vehicle_sn=sn,
                            other_ref=other_ref,
                            other_sn=other_sn,
                            mcp_options=mcp_entry.data,
                            companion_ids=COMPANION_IDS,
                            request_count=lambda: http_requests,
                        )
                    )
                finally:
                    entry.options["enable_assistant"] = True
            users["fixture-admin"].is_active = False
            status, inactive = await rpc("tools/call", call)
            assert status in (401, 403) or (
                status == 200 and ("error" in inactive or inactive["result"].get("isError"))
            )
            users["fixture-admin"].is_active = True
            entry.options["enable_assistant"] = False
            _, denied = await rpc("tools/call", call)
            assert "error" in denied or denied["result"]["isError"]
            assert "battery_soc_percent" not in json.dumps(denied)
            _, unknown = await rpc("tools/call", {"name": "ninebot__engine_start", "arguments": {}})
            assert "error" in unknown or unknown["result"].get("isError")
            unregister()
            _, removed = await rpc("tools/list")
            assert "error" in removed
            _, removed_prompt = await rpc("prompts/get", {"name": "Ninebot"})
            assert "error" in removed_prompt
    print(
        "Native MCP HTTP: read tools and prompts, original Context, "
        "untrusted device metadata, consent revocation/removal OK"
    )


def register_companion_api(hass, api_id):
    """Register one synthetic stand-in for an unrelated saved host selection."""
    from homeassistant.helpers import llm

    from custom_components.ninebot.assistant_api import native_result
    from custom_components.ninebot.compat import validation

    class CompanionTool(llm.Tool):
        name = f"{api_id}__probe"
        integration = "fixture"
        parameters = validation.Schema({})
        annotations = llm.ToolAnnotations(read_only=True)

        async def async_call(self, actual_hass, tool_input, llm_context):
            return native_result({"synthetic": True})

    class CompanionAPI(llm.API):
        async def async_get_api_instance(self, context):
            return llm.APIInstance(
                api=self, api_prompt=api_id, llm_context=context, tools=[CompanionTool()]
            )

    return llm.async_register_api(hass, CompanionAPI(hass=hass, id=api_id, name=api_id))


async def check_cutover(fixture) -> None:
    from homeassistant.components import websocket_api

    from custom_components.ninebot.archive_runtime import ArchiveStatistics
    from custom_components.ninebot.assistant import TASK_POLICY_EPOCH
    from custom_components.ninebot.assistant_access import (
        READ_STATISTICS,
        READ_STATUS,
        READ_SYNC,
        READ_TRIPS,
        TASK_OFF,
        TASK_PREVIEW,
        TASK_REQUEST,
        AssistantAccess,
        VehicleAccess,
        with_assistant_access,
    )
    from custom_components.ninebot.assistant_catalog import QUERY_SPECS, REQUEST_TOOL, SPECS
    from custom_components.ninebot.assistant_review import REGISTERED, async_register

    hass, entry, co = fixture.hass, fixture.entry, fixture.coordinator
    private_path = "/api/mcp/ninebot"
    rpc = partial(fixture.rpc, token="fixture-task-admin")
    mcp = fixture.mcp_options
    saved_options, saved_data, saved_mcp = entry.options, entry.data, dict(mcp)
    queries = {f"ninebot__{spec.key}" for spec in QUERY_SPECS}
    complete = {f"ninebot__{spec.key}" for spec in SPECS}
    request_name = f"ninebot__{REQUEST_TOOL}"
    preview_tools = complete - {request_name}
    companions = {f"{api_id}__probe" for api_id in COMPANION_IDS}
    full_reads = frozenset({READ_STATUS, READ_STATISTICS, READ_TRIPS, READ_SYNC})

    def grant(level):
        entry.options = with_assistant_access(
            saved_options,
            AssistantAccess(
                enabled=True,
                vehicles={
                    fixture.vehicle_sn: VehicleAccess(reads=full_reads, tasks=level),
                    fixture.other_sn: VehicleAccess(
                        reads=frozenset({READ_STATISTICS}), tasks=TASK_OFF
                    ),
                },
            ),
        )

    async def tools(path="/api/mcp"):
        status, body = await rpc("tools/list", path=path)
        assert status == 200 and "result" in body, (status, body)
        return {tool["name"]: tool for tool in body["result"]["tools"]}

    async def payload(name, args, *, path=private_path, expected_error=None):
        status, body = await rpc("tools/call", {"name": name, "arguments": args}, path=path)
        assert status == 200 and "result" in body, (status, body)
        result = body["result"]
        assert result.get("isError", False) == (expected_error is not None), result
        data = json.loads(result["content"][0]["text"])
        if expected_error is not None:
            assert data["error"]["code"] == expected_error and "data" not in data, data
        return data

    saved_statistics = co.statistics
    statistics = ArchiveStatistics(hass, entry.entry_id, "synthetic-task-archive")
    await statistics.archive.async_open()
    statistics.archive_ready = True
    co.statistics = statistics
    hass.data.setdefault(websocket_api.DOMAIN, {})
    async_register(hass)
    try:
        entry.data = {**saved_data, TASK_POLICY_EPOCH: "0" * 32}
        entry.options = {**saved_options, "enable_assistant_tasks": True}
        assert set(await tools(private_path)) == queries  # Legacy confirmation pending.
        grant(TASK_REQUEST)
        hass.data[REGISTERED] = False
        assert set(await tools(private_path)) == queries  # Human review unavailable.
        hass.data[REGISTERED] = True

        args = {
            "vehicle_ref": fixture.vehicle_ref,
            "start_date": "2026-11-06",
            "end_date": "2026-11-06",
        }
        grant(TASK_PREVIEW)
        assert (
            set(await tools(private_path)) == preview_tools
            and len(preview_tools) == len(queries) + 4
        )
        _, prompt = await rpc("prompts/get", {"name": "Ninebot"}, path=private_path)
        text = prompt["result"]["messages"][0]["content"]["text"]
        assert "task_vehicles" in text and REQUEST_TOOL not in text
        preview = await payload("ninebot__preview_detail_sync", args)
        assert preview["data"]["task_created"] is False
        assert preview["evidence"]["contract"] == "ninebot-tasks-v1"

        grant(TASK_REQUEST)
        formatted = await tools(private_path)
        assert set(formatted) == complete and len(formatted) == len(queries) + 5
        assert formatted[request_name]["annotations"]["readOnlyHint"] is False
        assert formatted[request_name]["annotations"]["idempotentHint"] is False
        assert set(await tools()) == preview_tools
        mutation = {"name": request_name, "arguments": args}
        with patch.object(
            statistics.archive,
            "async_detail_preview",
            wraps=statistics.archive.async_detail_preview,
        ) as local_read:
            _, blocked = await rpc("tools/call", mutation)
            assert "error" in blocked or blocked["result"]["isError"]
            local_read.assert_not_awaited()
            await payload(
                request_name,
                {**args, "vehicle_ref": fixture.other_ref},
                expected_error="not_authorized",
            )
            await payload(
                request_name, {**args, "confirmed": True}, expected_error="invalid_arguments"
            )
            local_read.assert_not_awaited()
            request = await payload(request_name, args)
            assert request["data"]["task_recorded"] is False  # Empty local archive.
            assert request["evidence"]["cloud_calls"] == 0
            local_read.assert_awaited_once()
            mcp["read_only"] = False
            assert set(await tools()) == complete
            assert (await payload(request_name, args, path="/api/mcp"))["data"][
                "task_recorded"
            ] is False
            assert local_read.await_count == 2

        roster = await payload("ninebot__task_vehicles", {})
        assert [row["vehicle_ref"] for row in roster["data"]["vehicles"]] == [fixture.vehicle_ref]
        await payload(
            "ninebot__get_vehicle_status",
            {"vehicle_ref": fixture.other_ref},
            expected_error="not_authorized",
        )

        status, _ = await rpc("tools/list", path="/api/mcp/ninebot_tasks")
        assert status == 404
        mcp["llm_hass_api"] = [COMPANION_IDS[0], "ninebot", "ninebot_tasks", COMPANION_IDS[1]]
        _, stale = await rpc("tools/list")
        assert "API ninebot_tasks not found" in stale["error"]["message"], stale
        mcp["all_llm_apis"] = True
        assert set(await tools()) == complete | companions
        mcp["all_llm_apis"] = False
        mcp["llm_hass_api"].remove("ninebot_tasks")
        merged = await tools()
        assert set(merged) == complete | companions
        for name, tool in formatted.items():
            assert merged[name]["inputSchema"] == tool["inputSchema"]
        mcp["llm_hass_api"] = ["ninebot_tasks"]
        _, old_only = await rpc("tools/list")
        assert "API ninebot_tasks not found" in old_only["error"]["message"], old_only
    finally:
        entry.options, entry.data, co.statistics = saved_options, saved_data, saved_statistics
        mcp.update(saved_mcp)
        await statistics.archive.async_close()
    print(
        "Native MCP cutover: scoped tools, review readiness, cross-vehicle denial "
        "and saved API selection OK"
    )


if __name__ == "__main__":
    asyncio.run(main())
