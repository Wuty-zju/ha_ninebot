"""Managed, authenticated loopback transport for the pinned ninecli package.

The Go child owns the encrypted upstream protocol. Passwords are sent only in
the local HTTP request body, never argv. Same-user process inspection remains
outside this isolation boundary; neither the proxy nor config path is exposed
as a user setting. No response/error body is logged.
"""

import asyncio
import json
import os
import secrets
import socket
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import CLI_TIMEOUT, MAX_RESPONSE_BYTES
from .exceptions import ErrorKind, NinebotAuthError, NinebotError


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
    raise NinebotError(ErrorKind.SERVICE)


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
        env = {key: value for key, value in os.environ.items() if not key.startswith("NINEBOT_")}
        env["NINEBOT_SERVE_TOKEN"] = self._bearer
        env["NINEBOT_SERVE_BIND"] = f"127.0.0.1:{port}"
        env["NO_PROXY"] = "127.0.0.1,localhost"
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
        except (OSError, TimeoutError) as err:
            await self._stop()
            raise NinebotError(ErrorKind.PLATFORM) from err
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
        await self._stop()
        self._bearer = ""

    async def _request(self, method: str, path: str, body: dict[str, str] | None = None) -> Any:
        if self._closed:
            raise NinebotError(ErrorKind.CLOSED)
        if self._pending >= 8:
            raise NinebotError(ErrorKind.BUSY)
        self._pending += 1
        acquired = False
        try:
            try:
                await asyncio.wait_for(self._lock.acquire(), self._timeout)
            except TimeoutError as err:
                raise NinebotError(ErrorKind.BUSY) from err
            acquired = True
            await self._start()
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
                        raw = json.loads(data)
                    except (ValueError, UnicodeError) as err:
                        raise NinebotError(ErrorKind.PROTOCOL) from err
                    return response_data(response.status, raw)
        except asyncio.CancelledError:
            if acquired:
                await self._stop()
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            await self._stop()
            raise NinebotError(ErrorKind.CONNECTION) from err
        except NinebotError as err:
            if err.kind == ErrorKind.PROTOCOL:
                await self._stop()
            raise
        finally:
            self._pending -= 1
            if acquired:
                self._lock.release()

    async def async_login(self, account: str, password: str) -> None:
        await self._request("POST", "/auth/login", {"account": account, "password": password})

    async def async_list_vehicles(self) -> Any:
        return await self._request("GET", "/vehicles")

    async def async_get_status(self, sn: str) -> Any:
        return await self._request("GET", f"/vehicles/{quote(sn, safe='')}/status")

    async def async_get_battery(self, sn: str) -> Any:
        return await self._request("GET", f"/vehicles/{quote(sn, safe='')}/battery")

    async def async_get_travel(self, sn: str, month: str) -> Any:
        return await self._request(
            "GET", f"/vehicles/{quote(sn, safe='')}/travel?month={quote(month, safe='')}"
        )

    async def async_control(self, sn: str, action: str) -> None:
        """One attempt only. A timeout leaves the physical outcome unknown."""
        if action not in {"engine/start", "engine/stop", "bell", "buck"}:
            raise ValueError("Unsupported action")
        await self._request("POST", f"/vehicles/{quote(sn, safe='')}/{action}")
