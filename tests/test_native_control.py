"""Synthetic native motor controls: exact routing, sole writer and no command replay."""

import asyncio
import json
import sys
from dataclasses import asdict
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.ninebot.client import NinecliClient, response_data
from custom_components.ninebot.exceptions import BackendError, ErrorKind, NinebotError
from custom_components.ninebot.native_control import control_failure, control_route

pytestmark = pytest.mark.usefixtures("socket_enabled")


def cache(directory, line="motor", sn="synthetic-vehicle"):
    (directory / "vehicles.json").write_text(
        json.dumps({"vehicles": [{"wnumber": sn, "business_line": line}]})
    )


@pytest.mark.parametrize("line", ["motor", "ebike", None])
def test_only_verified_motor_cache_selects_native_control(tmp_path, line):
    if line:
        cache(tmp_path, line)
    route = control_route(tmp_path, "synthetic-vehicle")
    assert route.native is (line == "motor")
    assert route.state == ("known" if line else "missing")
    assert route.business_line == line
    assert "synthetic-vehicle" not in json.dumps(route.diagnostics())


def test_conflicting_cached_lines_are_reported_without_guessing(tmp_path):
    (tmp_path / "vehicles.json").write_text(
        json.dumps(
            {
                "vehicles": [
                    {"wnumber": "synthetic", "business_line": line} for line in ("ebike", "motor")
                ]
            }
        )
    )
    route = control_route(tmp_path, "synthetic")
    assert route.state == "ambiguous" and route.matching_rows == 2
    assert route.business_line is None and not route.native


@pytest.mark.parametrize(
    "value,expected",
    [
        (340000, 340000),
        ("503", 503),
        ("-1", -1),
        (True, None),
        ("13456810000", None),
        (1.5, None),
        ("secret", None),
        ({"token": "private"}, None),
    ],
)
def test_only_bounded_numeric_upstream_code_survives(value, expected):
    with pytest.raises(NinebotError) as caught:
        response_data(
            502,
            {
                "ok": False,
                "error": {"code": "upstream_error", "upstream_code": value, "message": "private"},
            },
        )
    assert caught.value.context.upstream_code == expected
    assert "private" not in json.dumps(asdict(caught.value.context))
    assert caught.value.kind is ErrorKind.SERVICE


@pytest.mark.parametrize(
    "text,code,category",
    [
        (b'control: server code=340000 message="private"\n', 340000, None),
        (b'Error: control: server code=503 message="private"\n', 503, None),
        (
            b"decrypt failed: missing or non-string 'r' field\n",
            None,
            BackendError.RESPONSE_DECRYPTION,
        ),
        (b"unknown private account-token-location\n", None, None),
    ],
)
def test_native_error_keeps_codes_and_known_classification_without_stderr(text, code, category):
    error = control_failure(text)
    assert error.context.upstream_code == code
    assert error.context.backend_error is category
    assert "private" not in json.dumps(asdict(error.context))


@pytest.mark.parametrize("line", [None, "ebike"])
async def test_other_controls_keep_the_existing_serve_transport(tmp_path, line):
    if line:
        cache(tmp_path, line)
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        client._request_locked = AsyncMock(return_value=None)
        await client.async_control("synthetic-vehicle", "buck")
        client._request_locked.assert_awaited_once_with(
            "POST", "/vehicles/synthetic-vehicle/buck", None
        )
        assert client.control_routing["synthetic-vehicle"].native is False
        await client.async_close()
        assert not client.control_routing


@pytest.mark.parametrize(
    "action,command",
    [
        ("buck", "buck"),
        ("bell", "bell"),
        ("engine/start", "engine-start"),
        ("engine/stop", "engine-stop"),
    ],
)
async def test_motor_runs_one_native_command_and_never_posts_rest(
    tmp_path, monkeypatch, action, command
):
    sn = "--synthetic-vehicle"
    cache(tmp_path, "motor", sn)
    execute = asyncio.create_subprocess_exec
    calls, children = [], []

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        child = await execute(sys.executable, "-c", "print('{}')", **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setenv("NINEBOT_EBIKE_HOST", "https://invalid.test")
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        client._request_locked = AsyncMock()
        await client.async_control(sn, action)
        client._request_locked.assert_not_awaited()
        assert calls[0][0][-5:] == ("--json", "--yes", command, "--", sn)
        assert "NINEBOT_EBIKE_HOST" not in calls[0][1]["env"]
        assert client.control_routing[sn].diagnostics()["transport"] == "native_cli"
        await client.async_close()
    assert len(calls) == 1 and all(child.returncode == 0 for child in children)
    assert client._process is None and not client._lock.locked()


async def test_failed_native_command_is_not_retried_and_stderr_is_not_exported(
    tmp_path, monkeypatch
):
    cache(tmp_path)
    execute = asyncio.create_subprocess_exec
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        return await execute(
            sys.executable,
            "-c",
            "import sys;"
            "sys.stderr.write('control: server code=340000 message=\"private-secret\"\\n');"
            "sys.exit(1)",
            **kwargs,
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        with pytest.raises(NinebotError) as caught:
            await client.async_control("synthetic-vehicle", "buck")
        assert caught.value.context.upstream_code == 340000
        assert "private" not in str(caught.value) + json.dumps(asdict(caught.value.context))
        assert len(calls) == 1
        await client.async_close()


@pytest.mark.parametrize("cancel", [False, True])
async def test_close_or_cancel_reaps_native_child_and_releases_session(
    tmp_path, monkeypatch, cancel
):
    cache(tmp_path)
    execute = asyncio.create_subprocess_exec
    started, children = asyncio.Event(), []

    async def spawn(*args, **kwargs):
        child = await execute(sys.executable, "-c", "import time;time.sleep(60)", **kwargs)
        children.append(child)
        started.set()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        pending = asyncio.create_task(client.async_control("synthetic-vehicle", "buck"))
        await asyncio.wait_for(started.wait(), 3)
        if cancel:
            pending.cancel()
        else:
            await asyncio.wait_for(client.async_close(), 3)
        with pytest.raises(asyncio.CancelledError if cancel else NinebotError):
            await pending
        await client.async_close()
    assert len(children) == 1 and children[0].returncode is not None
    assert client._process is None and client._pending == 0 and not client._lock.locked()
