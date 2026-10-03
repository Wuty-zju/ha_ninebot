import asyncio
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web

from custom_components.ninebot.client import NinecliClient, response_data
from custom_components.ninebot.const import MAX_RESPONSE_BYTES
from custom_components.ninebot.exceptions import ErrorKind, NinebotError

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.mark.parametrize(
    "status,raw,kind",
    [
        (401, {"ok": False, "error": {"code": "unauthorized"}}, ErrorKind.AUTH),
        (
            502,
            {
                "ok": False,
                "error": {"code": "upstream_error", "upstream_code": 503, "message": "secret"},
            },
            ErrorKind.SERVICE,
        ),
        (200, {}, ErrorKind.PROTOCOL),
        (200, {"ok": True}, ErrorKind.PROTOCOL),
        (200, {"ok": False, "error": {}}, ErrorKind.SERVICE),
        (400, {"ok": False, "error": {"code": "bad_request"}}, ErrorKind.PROTOCOL),
        (200, {"ok": "true", "data": {}}, ErrorKind.PROTOCOL),
    ],
)
def test_response_error_categories(status, raw, kind):
    with pytest.raises(NinebotError) as error:
        response_data(status, raw)
    assert error.value.kind == kind
    assert "secret" not in str(error.value)
    assert response_data(200, {"ok": True, "data": []}) == []


@pytest.fixture
async def proxy():
    requests = []
    wait = asyncio.Event()
    started = asyncio.Event()

    async def handler(request):
        requests.append(
            (request.method, request.path, await request.json() if request.can_read_body else None)
        )
        started.set()
        if request.path.endswith("/slow/status"):
            await wait.wait()
        if request.path.endswith("/bad/status"):
            return web.Response(body=b"{bad")
        if request.path.endswith("/large/status"):
            return web.Response(body=b"x" * (MAX_RESPONSE_BYTES + 1))
        return web.json_response({"ok": True, "data": []})

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", requests, wait, started
    wait.set()
    await runner.cleanup()


async def test_password_body_and_path_encoding(tmp_path, proxy):
    url, requests, _, _ = proxy
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        client._base = url
        client._start = AsyncMock()
        await client.async_login("fake-account", "fake-password")
        await client.async_get_status("synthetic")
        await client.async_control("synthetic", "bell")
        assert requests[0] == (
            "POST",
            "/auth/login",
            {"account": "fake-account", "password": "fake-password"},
        )
        assert requests[1][1] == "/vehicles/synthetic/status"
        with pytest.raises(ValueError):
            await client.async_control("synthetic", "arbitrary")
        await client.async_close()
        with pytest.raises(NinebotError) as error:
            await client.async_list_vehicles()
        assert error.value.kind == ErrorKind.CLOSED


@pytest.mark.parametrize("vehicle", ["bad", "large"])
async def test_malformed_or_excess_output_stops_child(tmp_path, proxy, vehicle):
    url, _, _, _ = proxy
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        client._base = url
        client._start = AsyncMock()
        client._stop = AsyncMock()
        with pytest.raises(NinebotError) as error:
            await client.async_get_status(vehicle)
        assert error.value.kind == ErrorKind.PROTOCOL
        client._stop.assert_awaited_once()


async def test_cancel_request_reaps_child_and_unlocks(tmp_path, proxy):
    url, requests, _, started = proxy
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        client._base = url
        client._start = AsyncMock()
        client._stop = AsyncMock()
        task = asyncio.create_task(client.async_get_status("slow"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        client._stop.assert_awaited_once()
        assert not client._lock.locked()


async def test_queue_deadline_does_not_kill_another_request(tmp_path):
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session, timeout=0.01)
        await client._lock.acquire()
        with pytest.raises(NinebotError) as error:
            await client.async_list_vehicles()
        assert error.value.kind == ErrorKind.BUSY
        client._lock.release()


async def test_reap_terminate_then_kill(tmp_path):
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        process = await asyncio.create_subprocess_exec(
            "python3",
            "-c",
            "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            'print("ready",flush=True);time.sleep(30)',
            stdout=asyncio.subprocess.PIPE,
        )
        await process.stdout.readline()
        client._process = process
        await client.async_close()
        assert process.returncode is not None
        assert client._process is None


async def test_actual_proxy_startup_auth_and_unload(tmp_path):
    # No tokens: an authenticated local request returns unauthorized before
    # any upstream query. This runs the pinned binary without cloud access.
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session, timeout=5)
        try:
            with pytest.raises(NinebotError) as error:
                await client.async_list_vehicles()
            assert error.value.kind == ErrorKind.AUTH
            process = client._process
            assert process and process.returncode is None
        finally:
            await client.async_close()
        assert process.returncode is not None
