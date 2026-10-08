"""Managed, authenticated loopback transport for the pinned ninecli package.

The Go child owns the encrypted upstream protocol. Passwords are sent only in
the local HTTP request body, never argv. Same-user process inspection remains
outside this isolation boundary; neither the proxy nor config path is exposed
as a user setting. No response/error body is logged.
"""

import asyncio
import json
import math
import os
import secrets
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp

from .adapters import profiles
from .const import CLI_TIMEOUT, MAX_RESPONSE_BYTES
from .exceptions import ErrorKind, NinebotAuthError, NinebotError
from .vehicle_cache import cache_io, merge_partial_cache, read_cache, restore_cache


def response_data(status: int, raw: object) -> Any:
    """Interpret the proxy envelope without exporting raw upstream errors."""
    if not isinstance(raw, dict) or not isinstance(raw.get("ok"), bool):
        raise NinebotError(ErrorKind.PROTOCOL)
    if raw["ok"] is True:
        if status != 200 or "data" not in raw:
            raise NinebotError(ErrorKind.PROTOCOL)
        return raw["data"]
    error = raw.get("error")
    if not isinstance(error, dict):
        raise NinebotError(ErrorKind.PROTOCOL)
    # Proxy unauthorized explicitly includes missing/expired local sessions.
    # upstream_error and numeric 503 are NOT evidence of bad credentials.
    if status == 401 and error.get("code") == "unauthorized":
        raise NinebotAuthError()
    if error.get("code") in ("invalid_auth", "token_expired"):
        raise NinebotAuthError()
    if status == 400:
        raise NinebotError(ErrorKind.PROTOCOL)
    raise NinebotError(ErrorKind.SERVICE, retryable=500 <= status <= 599)


def retry_after_seconds(value: str | None, now: datetime) -> float | None:
    """Only a bounded transport header; no guessing from upstream error prose."""
    if value is None or len(value) > 128:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            stamp = parsedate_to_datetime(value)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            seconds = (stamp - now).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(900, seconds)


class NinecliClient:
    """One serialized session, bounded requests, and a reaped child process."""

    def __init__(
        self, config_dir: Path, session: aiohttp.ClientSession, *, timeout: float = CLI_TIMEOUT
    ) -> None:
        self.config_dir = config_dir
        self._session = session
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._base = ""
        self._bearer = ""
        self._closed = False
        self._pending = 0
        self._request_ready = False
        self.vehicle_discovery_complete = False

    @staticmethod
    def _environment() -> dict[str, str]:
        """Both native modes use fixed hosts, never ambient CLI overrides."""
        env = {key: value for key, value in os.environ.items() if not key.startswith("NINEBOT_")}
        env["NO_PROXY"] = "127.0.0.1,localhost"
        return env

    async def _start(self) -> None:
        if self._closed:
            raise NinebotError(ErrorKind.CLOSED)
        if self._process is not None and self._process.returncode is None:
            return
        await self._stop()
        self._bearer = secrets.token_urlsafe(32)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self._base = f"http://127.0.0.1:{port}"
        # Ignore ambient CLI overrides; production hosts are fixed by ninecli.
        env = self._environment()
        env["NINEBOT_SERVE_TOKEN"] = self._bearer
        env["NINEBOT_SERVE_BIND"] = f"127.0.0.1:{port}"
        try:
            self._process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "ninecli",
                "--config",
                str(self.config_dir),
                "serve",
                "--bind",
                f"127.0.0.1:{port}",
                "--quiet",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )
            async with asyncio.timeout(self._timeout):
                while True:
                    if self._process.returncode is not None:
                        raise NinebotError(ErrorKind.PLATFORM)
                    try:
                        # Check auth is actually enabled before sending credentials.
                        async with self._session.get(
                            f"{self._base}/vehicles",
                            proxy=None,
                            timeout=aiohttp.ClientTimeout(total=1),
                        ) as response:
                            await response.content.read(MAX_RESPONSE_BYTES + 1)
                            if response.status != 401:
                                raise NinebotError(ErrorKind.PROTOCOL)
                        # Authenticated nonexistent route must NOT return 401.
                        async with self._session.get(
                            f"{self._base}/__ninebot_auth_probe__",
                            proxy=None,
                            headers={"Authorization": f"Bearer {self._bearer}"},
                            timeout=aiohttp.ClientTimeout(total=1),
                        ) as response:
                            await response.content.read(MAX_RESPONSE_BYTES + 1)
                            if response.status != 404:
                                raise NinebotError(ErrorKind.PROTOCOL)
                        if self._process.returncode is not None:
                            raise NinebotError(ErrorKind.PLATFORM)
                        return
                    except aiohttp.ClientConnectionError:
                        await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            await self._stop()
            raise
        except (OSError, TimeoutError):
            await self._stop()
            raise NinebotError(ErrorKind.PLATFORM) from None
        except NinebotError:
            await self._stop()
            raise

    async def _stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        task = asyncio.create_task(self._reap(process))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    @staticmethod
    async def _reap(process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
        await process.wait()

    async def async_close(self) -> None:
        """Stop even when another request is waiting on the local HTTP child."""
        self._closed = True
        try:
            await self._stop()
        finally:
            self._bearer = ""

    @asynccontextmanager
    async def _operation(self) -> AsyncIterator[None]:
        """One queue/lock/lifecycle for REST requests and native cache refresh."""
        if self._closed:
            raise NinebotError(ErrorKind.CLOSED)
        if self._pending >= 8:
            raise NinebotError(ErrorKind.BUSY)
        self._pending += 1
        acquired = False
        try:
            try:
                await asyncio.wait_for(self._lock.acquire(), self._timeout)
            except TimeoutError:
                raise NinebotError(ErrorKind.BUSY) from None
            acquired = True
            if self._closed:
                raise NinebotError(ErrorKind.CLOSED)
            yield
        except asyncio.CancelledError:
            if acquired:
                await self._stop()
            raise
        finally:
            self._pending -= 1
            if acquired:
                self._lock.release()

    async def _request_locked(
        self, method: str, path: str, body: dict[str, str] | None = None
    ) -> Any:
        """Caller owns the session operation; raw errors never leave this layer."""
        try:
            self._request_ready = False
            await self._start()
            self._request_ready = True
            async with asyncio.timeout(self._timeout):
                async with self._session.request(
                    method,
                    f"{self._base}{path}",
                    json=body,
                    proxy=None,
                    headers={"Authorization": f"Bearer {self._bearer}"},
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                    allow_redirects=False,
                ) as response:
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(16384):
                        data.extend(chunk)
                        if len(data) > MAX_RESPONSE_BYTES:
                            raise NinebotError(ErrorKind.PROTOCOL)
                    try:
                        raw = await asyncio.to_thread(json.loads, data)
                    except (ValueError, UnicodeError):
                        raise NinebotError(ErrorKind.PROTOCOL) from None
                    try:
                        return response_data(response.status, raw)
                    except NinebotError as err:
                        if response.status == 429 and err.kind is ErrorKind.SERVICE:
                            delay = retry_after_seconds(
                                response.headers.get("Retry-After"), datetime.now(UTC)
                            )
                            err.retry_after = delay if delay is not None else 60
                            err.retryable = False
                        raise
        except (aiohttp.ClientError, TimeoutError):
            await self._stop()
            raise NinebotError(ErrorKind.CONNECTION, retryable=True) from None
        except NinebotError as err:
            if err.kind == ErrorKind.PROTOCOL:
                await self._stop()
            raise

    async def _request(
        self, method: str, path: str, body: dict[str, str] | None = None, *, retry: bool = True
    ) -> Any:
        async with self._operation():
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._timeout
            try:
                async with asyncio.timeout(self._timeout):
                    for attempt in range(2):
                        try:
                            return await self._request_locked(method, path, body)
                        except NinebotError as err:
                            if (
                                method != "GET"
                                or not retry
                                or attempt
                                or not err.retryable
                                or deadline - loop.time() < 0.1
                            ):
                                raise
                            await asyncio.sleep(0.1)
            except TimeoutError:
                kind = ErrorKind.CONNECTION if self._request_ready else ErrorKind.PLATFORM
                await self._stop()
                raise NinebotError(kind) from None
            raise AssertionError("unreachable request retry")

    async def async_login(self, account: str, password: str) -> None:
        await self._request("POST", "/auth/login", {"account": account, "password": password})

    async def async_get_account(self) -> Any:
        """Explicit login or one missing-title enrichment at setup; never polling."""
        return await self._request("GET", "/whoami")

    async def async_send_login_code(self, account: str) -> None:
        await self._request("POST", "/auth/login-code", {"account": account})

    async def async_consume_login_code(self, account: str, code: str) -> None:
        await self._request("POST", "/auth/login-code/consume", {"account": account, "code": code})

    async def async_list_vehicles(self) -> Any:
        """Discover vehicles and let native ninecli prepare its routing cache.

        In 0.1.7 REST /vehicles does not write vehicles.json, but battery
        routing requires it. CLI vehicles writes the verified business lines;
        inferring those lines from the merged REST response would be unsafe.
        Stop serve before native token/cache updates, then restart lazily with
        the new files. This operation contains no credentials in argv.
        """
        async with self._operation():
            await self._stop()
            previous_cache = await cache_io(read_cache, self.config_dir)
            accepted = False
            self.vehicle_discovery_complete = False
            try:
                async with asyncio.timeout(self._timeout):
                    self._process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-m",
                        "ninecli",
                        "--config",
                        str(self.config_dir),
                        "--json",
                        "vehicles",
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=self._environment(),
                    )
                    process = self._process
                    if self._closed:
                        raise NinebotError(ErrorKind.CLOSED)
                    assert process.stdout is not None and process.stderr is not None
                    data, diagnostic = await self._read_cli_output(process.stdout, process.stderr)
                    code = await process.wait()
                    await self._stop()
                    if self._closed:
                        raise NinebotError(ErrorKind.CLOSED)
                    if code != 0:
                        # The CLI has no reviewed structured error contract.
                        # Use REST's explicit authentication evidence instead
                        # of matching secrets-bearing stderr or exit strings.
                        await self._request_locked("GET", "/whoami")
                        raise NinebotError(ErrorKind.SERVICE)
                    try:
                        raw = await asyncio.to_thread(json.loads, data)
                    except (ValueError, UnicodeError):
                        raise NinebotError(ErrorKind.PROTOCOL) from None
                    if not isinstance(raw, list):
                        raise NinebotError(ErrorKind.PROTOCOL)
                    # The same identity contract used by the coordinator must
                    # pass before accepting the native routing-cache update.
                    profiles(raw)
                    # Native 0.1.7 returns exit 0 even if one or both business
                    # lists failed. Diagnostic output is opaque, not auth proof.
                    complete = not diagnostic.strip()
                    if not complete:
                        if not raw:
                            raise NinebotError(ErrorKind.SERVICE)
                        await cache_io(merge_partial_cache, self.config_dir, previous_cache)
                    self.vehicle_discovery_complete = complete
                    accepted = True
                    return raw
            except TimeoutError:
                raise NinebotError(ErrorKind.CONNECTION) from None
            except OSError:
                raise NinebotError(ErrorKind.PLATFORM) from None
            finally:
                try:
                    await self._stop()
                finally:
                    if not accepted:
                        await cache_io(restore_cache, self.config_dir, previous_cache)

    @staticmethod
    async def _read_cli_output(
        stdout: asyncio.StreamReader, stderr: asyncio.StreamReader
    ) -> tuple[bytes, bytes]:
        """Drain both pipes concurrently, bounded and never logged."""

        async def read(stream: asyncio.StreamReader) -> bytes:
            data = bytearray()
            while chunk := await stream.read(16384):
                data.extend(chunk)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise NinebotError(ErrorKind.PROTOCOL)
            return bytes(data)

        tasks = [asyncio.create_task(read(stream)) for stream in (stdout, stderr)]
        try:
            data, diagnostic = await asyncio.gather(*tasks)
            return data, diagnostic
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def async_get_status(self, sn: str) -> Any:
        return await self._request("GET", f"/vehicles/{quote(sn, safe='')}/status")

    async def async_get_status_once(self, sn: str) -> Any:
        """The bounded control-confirmation budget owns retries on this path."""
        return await self._request("GET", f"/vehicles/{quote(sn, safe='')}/status", retry=False)

    async def async_get_battery(self, sn: str) -> Any:
        return await self._request("GET", f"/vehicles/{quote(sn, safe='')}/battery")

    async def async_get_travel(self, sn: str, month: str) -> Any:
        return await self._request(
            "GET", f"/vehicles/{quote(sn, safe='')}/travel?month={quote(month, safe='')}"
        )

    async def async_get_trip_detail(self, sn: str, detail_id: str) -> Any:
        return await self._request(
            "GET", f"/vehicles/{quote(sn, safe='')}/travel/{quote(detail_id, safe='')}"
        )

    async def async_control(self, sn: str, action: str) -> None:
        """One attempt only. A timeout leaves the physical outcome unknown."""
        if action not in {"engine/start", "engine/stop", "bell", "buck"}:
            raise ValueError("Unsupported action")
        await self._request("POST", f"/vehicles/{quote(sn, safe='')}/{action}")
