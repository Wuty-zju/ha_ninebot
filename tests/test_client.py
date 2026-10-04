import asyncio
import json
from pathlib import Path
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
        if request.method == "POST" and any(
            request.path.endswith(f"/{action}")
            for action in ("bell", "buck", "engine/start", "engine/stop")
        ):
            return web.json_response(
                json.loads(
                    (
                        Path(__file__).parent / "fixtures/ninecli/0.1.7/control-accepted.json"
                    ).read_text()
                )
            )
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


@pytest.mark.parametrize("action", ["bell", "buck", "engine/start", "engine/stop"])
async def test_recorded_empty_control_acceptance_is_one_command_not_physical_state(
    tmp_path, proxy, action
):
    url, requests, _, _ = proxy
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        client._base = url
        client._start = AsyncMock()
        assert await client.async_control("synthetic", action) is None
        assert requests == [("POST", f"/vehicles/synthetic/{action}", None)]
        await client.async_close()


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
                await client.async_get_status("synthetic-no-session")
            assert error.value.kind == ErrorKind.AUTH
            process = client._process
            assert process and process.returncode is None
        finally:
            await client.async_close()
        assert process.returncode is not None


@pytest.mark.parametrize("raw", [None, {"code": "token_expired"}, {"code": "invalid_auth"}])
def test_explicit_auth_or_malformed_error_envelope(raw):
    with pytest.raises(NinebotError) as error:
        response_data(502, {"ok": False, "error": raw})
    assert error.value.kind == (ErrorKind.PROTOCOL if raw is None else ErrorKind.AUTH)


@pytest.mark.parametrize(
    "anonymous,authenticated,exit_code,expected",
    [
        (200, 404, None, ErrorKind.PROTOCOL),
        (401, 401, None, ErrorKind.PROTOCOL),
        (401, 404, 7, ErrorKind.PLATFORM),
    ],
)
async def test_startup_rejects_unprotected_wrong_or_exited_server(
    tmp_path, anonymous, authenticated, exit_code, expected
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    process = MagicMock()
    process.returncode = exit_code
    process.wait = AsyncMock(return_value=0)

    def terminate():
        process.returncode = -15

    process.terminate.side_effect = terminate
    session = MagicMock()

    @asynccontextmanager
    async def get(url, **kwargs):
        yield SimpleNamespace(
            status=authenticated if "auth_probe" in url else anonymous,
            content=SimpleNamespace(read=AsyncMock(return_value=b"")),
        )

    session.get.side_effect = get
    client = NinecliClient(tmp_path, session)
    with patch(
        "custom_components.ninebot.client.asyncio.create_subprocess_exec", return_value=process
    ) as spawn:
        with pytest.raises(NinebotError) as error:
            await client._start()
    assert error.value.kind == expected
    assert client._process is None
    process.wait.assert_awaited()
    assert "NINEBOT_SERVE_TOKEN" in spawn.call_args.kwargs["env"]
    assert "--token" not in spawn.call_args.args


async def test_startup_spawn_failure_and_closed_client(tmp_path):
    from unittest.mock import MagicMock, patch

    client = NinecliClient(tmp_path, MagicMock())
    with patch(
        "custom_components.ninebot.client.asyncio.create_subprocess_exec",
        side_effect=OSError("synthetic"),
    ):
        with pytest.raises(NinebotError) as error:
            await client._start()
    assert error.value.kind == ErrorKind.PLATFORM
    await client.async_close()
    with pytest.raises(NinebotError) as error:
        await client._start()
    assert error.value.kind == ErrorKind.CLOSED


async def test_request_timeout_cleans_up_and_releases_queue(tmp_path, proxy):
    url, _, _, _ = proxy
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session, timeout=0.03)
        client._base = url
        client._start = AsyncMock()
        client._stop = AsyncMock()
        with pytest.raises(NinebotError) as error:
            await client.async_get_status("slow")
        assert error.value.kind == ErrorKind.CONNECTION
        client._stop.assert_awaited_once()
        assert client._pending == 0 and not client._lock.locked()


async def test_cancel_queued_request_never_stops_active_child(tmp_path):
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        await client._lock.acquire()
        client._stop = AsyncMock()
        queued = asyncio.create_task(client.async_get_battery("synthetic"))
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        client._stop.assert_not_awaited()
        assert client._pending == 0
        client._lock.release()


async def test_session_queue_is_bounded(tmp_path, proxy):
    url, _, wait, started = proxy
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        client._base = url
        client._start = AsyncMock()
        client._stop = AsyncMock()
        first = asyncio.create_task(client.async_get_status("slow"))
        await started.wait()
        rest = [asyncio.create_task(client.async_get_battery("synthetic")) for _ in range(7)]
        await asyncio.sleep(0)
        with pytest.raises(NinebotError) as error:
            await client.async_get_travel("synthetic", "202610")
        assert error.value.kind == ErrorKind.BUSY
        wait.set()
        await asyncio.gather(first, *rest)
        assert client._pending == 0


async def test_cancel_close_reaps_child_before_clearing_bearer(tmp_path):
    from unittest.mock import MagicMock

    started, release = asyncio.Event(), asyncio.Event()
    client = NinecliClient(tmp_path, MagicMock())
    client._process = MagicMock()
    client._bearer = "synthetic-local-token"

    async def reap(process):
        started.set()
        await release.wait()

    client._reap = AsyncMock(side_effect=reap)
    task = asyncio.create_task(client.async_close())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client._process is None
    assert client._bearer == ""
    await client.async_close()
    client._reap.assert_awaited_once()


async def test_transport_failure_does_not_leak_request_details_in_traceback(tmp_path):
    import traceback
    from unittest.mock import MagicMock

    session = MagicMock()
    session.request.side_effect = aiohttp.ClientError("synthetic-secret-in-error")
    client = NinecliClient(tmp_path, session)
    client._start = AsyncMock()
    client._stop = AsyncMock()
    with pytest.raises(NinebotError) as caught:
        await client.async_get_status("synthetic")
    assert caught.value.kind is ErrorKind.CONNECTION
    rendered = "".join(traceback.format_exception(caught.value))
    assert "synthetic-secret-in-error" not in rendered
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("cancelled", [False, True])
async def test_startup_deadline_or_cancellation_reaps_child(tmp_path, cancelled):
    from unittest.mock import MagicMock, patch

    started = asyncio.Event()
    process = MagicMock()
    process.returncode = None
    process.wait = AsyncMock(return_value=0)
    process.terminate.side_effect = lambda: setattr(process, "returncode", -15)
    session = MagicMock()

    def unavailable(*args, **kwargs):
        started.set()
        raise aiohttp.ClientConnectionError()

    session.get.side_effect = unavailable
    client = NinecliClient(tmp_path, session, timeout=0.05 if not cancelled else 10)
    with patch(
        "custom_components.ninebot.client.asyncio.create_subprocess_exec", return_value=process
    ):
        task = asyncio.create_task(client.async_get_status("synthetic"))
        await started.wait()
        if cancelled:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancelled else NinebotError) as error:
            await task
        if not cancelled:
            assert error.value.kind == ErrorKind.PLATFORM
    assert client._process is None
    process.wait.assert_awaited()
    assert not client._lock.locked() and client._pending == 0
