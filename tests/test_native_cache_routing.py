"""Pinned native REST routes against loopback-only synthetic upstreams."""

import asyncio
import json
import time

import aiohttp
import pytest
from aiohttp import web

from custom_components.ninebot.client import NinecliClient
from custom_components.ninebot.exceptions import ErrorKind, NinebotError

pytestmark = pytest.mark.usefixtures("socket_enabled")


async def test_native_discovery_business_failures_are_not_successful_unbinding(
    tmp_path, monkeypatch
):
    paths = []

    async def upstream(request):
        paths.append(request.path)
        return web.json_response({"code": 503, "message": "synthetic-failure"})

    application = web.Application()
    application.router.add_route("*", "/{path:.*}", upstream)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    (tmp_path / "tokens.json").write_text(
        json.dumps(
            {
                "uuid": "synthetic-uuid",
                "access_token": "synthetic-access",
                "refresh_token": "synthetic-refresh",
                "business_uid": "123",
                "accessTokenValidity": "4102444800000",
                "saved_at": 4100000000,
            }
        )
    )
    old = b'{"vehicles":[{"wnumber":"synthetic-old","business_line":"ebike"}]}'
    (tmp_path / "vehicles.json").write_bytes(old)
    execute = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        flags = tuple(
            item
            for flag in (
                "--passport-base",
                "--biz-host",
                "--ebike-host",
                "--motor-host",
                "--travel-host",
            )
            for item in (flag, origin)
        )
        return await execute(*args[:5], *flags, *args[5:], **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            client = NinecliClient(tmp_path, session, timeout=5)
            try:
                with pytest.raises(NinebotError) as error:
                    await client.async_list_vehicles()
                assert error.value.kind is ErrorKind.SERVICE
                assert paths == ["/vehicle/binding/my-vehicle"] * 2
                assert (tmp_path / "vehicles.json").read_bytes() == old
                assert client._process is None
            finally:
                await client.async_close()
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize(
    "action,path",
    [
        ("battery", "/v6/vehicle/battery-info"),
        ("bell", "/devices/control/bell"),
        ("buck", "/devices/control/open_buck"),
        ("engine/start", "/devices/control/engine_start"),
        ("engine/stop", "/devices/control/engine_stop"),
    ],
)
async def test_native_routes_and_endpoint_specific_cache_requirements(
    tmp_path, monkeypatch, cached, action, path
):
    paths = []

    async def upstream(request):
        paths.append(request.path)
        return web.json_response({"code": 503, "message": "synthetic-upstream"})

    application = web.Application()
    application.router.add_route("*", "/{path:.*}", upstream)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    origin = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    (tmp_path / "tokens.json").write_text(
        json.dumps(
            {
                "uuid": "synthetic-uuid",
                "access_token": "synthetic-access",
                "refresh_token": "synthetic-refresh",
                "accessTokenValidity": "4102444800000",
                "business_uid": "123",
                "saved_at": int(time.time()),
            }
        )
    )
    if cached:
        (tmp_path / "vehicles.json").write_text(
            json.dumps(
                {
                    "vehicles": [
                        {
                            "wnumber": "synthetic-vehicle",
                            "vehicle_name": "Synthetic model",
                            "device_name": "Synthetic vehicle",
                            "business_line": "ebike",
                        }
                    ]
                }
            )
        )
    execute = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        # Every upstream origin is explicitly loopback in this test. Production
        # has no host override facility. No real control is sent here.
        assert "serve" in args
        prefix, suffix = args[:5], args[5:]
        flags = tuple(
            item
            for flag in (
                "--passport-base",
                "--biz-host",
                "--ebike-host",
                "--motor-host",
                "--travel-host",
            )
            for item in (flag, origin)
        )
        return await execute(*prefix, *flags, *suffix, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            client = NinecliClient(tmp_path, session, timeout=5)
            try:
                with pytest.raises(NinebotError) as error:
                    if action == "battery":
                        await client.async_get_battery("synthetic-vehicle")
                    else:
                        await client.async_control("synthetic-vehicle", action)
                requires_cache = action == "battery" and not cached
                assert error.value.kind is (
                    ErrorKind.PROTOCOL if requires_cache else ErrorKind.SERVICE
                )
                # One bounded retry for a read-side 5xx, never for controls or
                # a missing native routing cache (a protocol error).
                expected_attempts = 2 if action == "battery" else 1
                assert paths == ([] if requires_cache else [path] * expected_attempts)
            finally:
                await client.async_close()
            assert client._process is None
    finally:
        await runner.cleanup()
