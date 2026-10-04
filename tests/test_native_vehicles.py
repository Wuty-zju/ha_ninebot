"""Native cache discovery shares REST's lifecycle without any cloud I/O."""

import asyncio
import json
import sys
import traceback
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.ninebot.client import NinecliClient
from custom_components.ninebot.const import MAX_RESPONSE_BYTES
from custom_components.ninebot.exceptions import ErrorKind, NinebotAuthError, NinebotError


@pytest.fixture
def native(monkeypatch):
    """Substitute only the executable; exercise actual pipes and child cleanup."""
    execute = asyncio.create_subprocess_exec
    children = []
    calls = []
    started = asyncio.Event()

    def install(program):
        async def spawn(*args, **kwargs):
            calls.append((args, kwargs))
            child = await execute(sys.executable, "-c", program, **kwargs)
            children.append(child)
            started.set()
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    return install, children, calls, started


async def test_native_discovery_returns_raw_and_discards_old_serve(tmp_path, native, monkeypatch):
    install, children, calls, _ = native
    rows = [{"wnumber": "synthetic-ebike"}, {"wnumber": "synthetic-motor"}]
    cache = {
        "vehicles": [
            {"wnumber": row["wnumber"], "business_line": line}
            for row, line in zip(rows, ("ebike", "motor"), strict=True)
        ]
    }
    install(
        f"from pathlib import Path;"
        f"Path({str(tmp_path / 'vehicles.json')!r}).write_text({json.dumps(cache)!r});"
        f"print({json.dumps(json.dumps(rows))})"
    )
    monkeypatch.setenv("NINEBOT_EBIKE_HOST", "http://untrusted.invalid")
    monkeypatch.setenv("NINEBOT_SERVE_TOKEN", "synthetic-old-secret")
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        old = await asyncio.create_subprocess_exec(sys.executable)
        # The executable substitute handles the intentionally unused arguments.
        client._process = old
        assert await client.async_list_vehicles() == rows
        assert json.loads((tmp_path / "vehicles.json").read_text()) == cache
        assert old.returncode is not None and client._process is None
        assert children[-1].returncode == 0
        argv, options = calls[-1]
        assert argv == (
            sys.executable,
            "-m",
            "ninecli",
            "--config",
            str(tmp_path),
            "--json",
            "vehicles",
        )
        assert not any(key.startswith("NINEBOT_") for key in options["env"])
        assert options["stdin"] is asyncio.subprocess.DEVNULL
        assert options["stderr"] is asyncio.subprocess.PIPE
        assert client._pending == 0 and not client._lock.locked()
        # REST starts lazily using the newly written native cache/token files.
        client._request_locked = AsyncMock(return_value={"battery_list": []})
        assert await client.async_get_battery("synthetic-ebike") == {"battery_list": []}
        client._request_locked.assert_awaited_once_with(
            "GET", "/vehicles/synthetic-ebike/battery", None
        )
        await client.async_close()


@pytest.mark.parametrize(
    "raw", ["not-json", "{}", '{"ok":true,"data":[]}', '[{"device_name":"no identity"}]']
)
async def test_native_output_requires_json_list(tmp_path, native, raw):
    install, children, _, started = native
    install(f"print({raw!r})")
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        with pytest.raises(NinebotError) as error:
            await client.async_list_vehicles()
        assert error.value.kind is ErrorKind.PROTOCOL
        assert children[0].returncode is not None and client._process is None
        assert not client._lock.locked() and client._pending == 0


async def test_invalid_profile_cannot_commit_native_cache(tmp_path, native):
    install, children, _, _ = native
    old = b'{"vehicles":[{"wnumber":"synthetic-old","business_line":"ebike"}]}'
    (tmp_path / "vehicles.json").write_bytes(old)
    install(
        f"from pathlib import Path;"
        f"Path({str(tmp_path / 'vehicles.json')!r}).write_text('{{\"vehicles\":null}}');"
        'print(\'[{"device_name":"no identity"}]\')'
    )
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        with pytest.raises(NinebotError) as error:
            await client.async_list_vehicles()
        assert error.value.kind is ErrorKind.PROTOCOL
        assert (tmp_path / "vehicles.json").read_bytes() == old
        assert client.vehicle_discovery_complete is False
        assert children[0].returncode == 0 and client._process is None
        await client.async_close()


async def test_native_output_limit_kills_child_without_waiting_for_eof(tmp_path, native):
    install, children, _, started = native
    install(f"import time;print('x'*{MAX_RESPONSE_BYTES + 1},flush=True);time.sleep(60)")
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session, timeout=5)
        with pytest.raises(NinebotError) as error:
            await client.async_list_vehicles()
        assert error.value.kind is ErrorKind.PROTOCOL
        assert children[0].returncode is not None and client._process is None


@pytest.mark.parametrize("auth_failure", [False, True])
async def test_cli_failure_uses_explicit_rest_auth_evidence(tmp_path, native, auth_failure):
    install, children, _, started = native
    install("import sys;sys.stderr.write('synthetic-private-secret');sys.exit(1)")
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        client._request_locked = AsyncMock(
            side_effect=NinebotAuthError() if auth_failure else None,
            return_value={"synthetic": True},
        )
        with pytest.raises(NinebotError) as error:
            await client.async_list_vehicles()
        assert error.value.kind is (ErrorKind.AUTH if auth_failure else ErrorKind.SERVICE)
        client._request_locked.assert_awaited_once_with("GET", "/whoami")
        assert "synthetic-private-secret" not in "".join(traceback.format_exception(error.value))
        assert children[0].returncode == 1 and client._process is None


@pytest.mark.parametrize("mode", ["timeout", "cancel", "close"])
async def test_native_pending_operation_is_reaped(tmp_path, native, mode):
    install, children, _, started = native
    install("import time;time.sleep(60)")
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session, timeout=0.1 if mode == "timeout" else 5)
        task = asyncio.create_task(client.async_list_vehicles())
        await started.wait()
        if mode == "cancel":
            task.cancel()
        elif mode == "close":
            await client.async_close()
        with pytest.raises(asyncio.CancelledError if mode == "cancel" else NinebotError) as error:
            await task
        if mode != "cancel":
            assert error.value.kind is (
                ErrorKind.CONNECTION if mode == "timeout" else ErrorKind.CLOSED
            )
        assert children[0].returncode is not None and client._process is None
        assert not client._lock.locked() and client._pending == 0


async def test_cancel_queued_discovery_does_not_stop_active_rest_child(tmp_path):
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        await client._lock.acquire()
        client._stop = AsyncMock()
        task = asyncio.create_task(client.async_list_vehicles())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        client._stop.assert_not_awaited()
        assert client._pending == 0
        client._lock.release()


async def test_native_spawn_failure_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("synthetic-secret"))
    )
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        with pytest.raises(NinebotError) as error:
            await client.async_list_vehicles()
        assert error.value.kind is ErrorKind.PLATFORM
        assert "synthetic-secret" not in "".join(traceback.format_exception(error.value))
        assert client._process is None and client._pending == 0


async def test_partial_discovery_preserves_missing_native_routes(tmp_path, native):
    install, children, _, _ = native
    old = {"vehicles": [{"wnumber": "synthetic-motor", "business_line": "motor"}]}
    new = {"vehicles": [{"wnumber": "synthetic-ebike", "business_line": "ebike"}]}
    (tmp_path / "vehicles.json").write_text(json.dumps(old))
    install(
        f"import sys;from pathlib import Path;"
        f"Path({str(tmp_path / 'vehicles.json')!r}).write_text({json.dumps(new)!r});"
        f"sys.stderr.write('synthetic-private-discovery-warning');"
        f'print(\'[{{"wnumber":"synthetic-ebike"}}]\')'
    )
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        assert await client.async_list_vehicles() == [{"wnumber": "synthetic-ebike"}]
        assert client.vehicle_discovery_complete is False
        assert json.loads((tmp_path / "vehicles.json").read_text())["vehicles"] == (
            new["vehicles"] + old["vehicles"]
        )
        assert (tmp_path / "vehicles.json").stat().st_mode & 0o777 == 0o600
        assert children[0].returncode == 0
        await client.async_close()


@pytest.mark.parametrize("mode", ["empty_warning", "malformed", "oversized_stderr", "cancel"])
async def test_failed_discovery_restores_cache_not_tokens(tmp_path, native, mode):
    install, children, _, started = native
    old = b'{"vehicles":[{"wnumber":"synthetic-old","business_line":"ebike"}]}'
    (tmp_path / "vehicles.json").write_bytes(old)
    prefix = (
        "import sys,time;from pathlib import Path;"
        f"Path({str(tmp_path / 'vehicles.json')!r}).write_text('{{\"vehicles\":null}}');"
        f"Path({str(tmp_path / 'tokens.json')!r}).write_text('synthetic-refreshed');"
    )
    programs = {
        "empty_warning": "sys.stderr.write('synthetic-private-warning');print('[]')",
        "malformed": "print('{}')",
        "oversized_stderr": (
            f"sys.stderr.write('x'*{MAX_RESPONSE_BYTES + 1});sys.stderr.flush();time.sleep(30)"
        ),
        "cancel": "print('[]',flush=True);time.sleep(30)",
    }
    install(prefix + programs[mode])
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session, timeout=5)
        task = asyncio.create_task(client.async_list_vehicles())
        await started.wait()
        if mode == "cancel":
            # Wait for the synthetic child to overwrite its cache before cancel.
            for _ in range(100):
                if (tmp_path / "tokens.json").exists():
                    break
                await asyncio.sleep(0.01)
            assert (tmp_path / "tokens.json").exists()
            task.cancel()
        with pytest.raises(asyncio.CancelledError if mode == "cancel" else NinebotError) as error:
            await task
        if mode != "cancel":
            assert error.value.kind is (
                ErrorKind.SERVICE if mode == "empty_warning" else ErrorKind.PROTOCOL
            )
            assert "synthetic-private" not in "".join(traceback.format_exception(error.value))
        assert (tmp_path / "vehicles.json").read_bytes() == old
        assert (tmp_path / "tokens.json").read_text() == "synthetic-refreshed"
        assert client.vehicle_discovery_complete is False
        assert children[0].returncode is not None and client._process is None
        assert not client._lock.locked() and client._pending == 0
        await client.async_close()


async def test_clean_empty_account_is_complete_and_keeps_native_removal(tmp_path, native):
    install, _, _, _ = native
    (tmp_path / "vehicles.json").write_text('{"vehicles":[{"wnumber":"synthetic-old"}]}')
    install(
        f"from pathlib import Path;"
        f"Path({str(tmp_path / 'vehicles.json')!r}).write_text('{{\"vehicles\":null}}');print('[]')"
    )
    async with aiohttp.ClientSession() as session:
        client = NinecliClient(tmp_path, session)
        assert await client.async_list_vehicles() == []
        assert client.vehicle_discovery_complete is True
        assert json.loads((tmp_path / "vehicles.json").read_text())["vehicles"] is None
        await client.async_close()
