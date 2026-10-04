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


@pytest.mark.parametrize("cached", [False, True])
async def test_native_battery_requires_cache_before_any_upstream_call(
    tmp_path, monkeypatch, cached
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
        # has no host override facility and sends no control requests here.
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
                    await client.async_get_battery("synthetic-vehicle")
                assert error.value.kind is (ErrorKind.SERVICE if cached else ErrorKind.PROTOCOL)
                assert paths == (["/v6/vehicle/battery-info"] if cached else [])
            finally:
                await client.async_close()
            assert client._process is None
    finally:
        await runner.cleanup()
