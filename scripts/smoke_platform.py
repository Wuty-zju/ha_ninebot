"""Offline platform smoke: empty temporary session, no upstream queries or controls.

Run in an isolated HA/Python environment with ninecli==0.1.7 installed.
Docker --network none preserves loopback while preventing cloud connections.
"""

import asyncio
import json
import platform
import tempfile
from importlib.metadata import version
from pathlib import Path

import aiohttp

from custom_components.ninebot.client import NinecliClient
from custom_components.ninebot.exceptions import ErrorKind, NinebotAuthError


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ninebot-empty-smoke-") as temporary:
        directory = Path(temporary)
        await asyncio.to_thread(directory.chmod, 0o700)
        async with aiohttp.ClientSession() as session:
            client = NinecliClient(directory, session, timeout=10)
            process = None
            try:
                try:
                    await client.async_list_vehicles()
                except NinebotAuthError as error:
                    assert error.kind is ErrorKind.AUTH
                else:
                    raise AssertionError("An empty private session must require authentication")
                process = client._process
                assert process is not None and process.returncode is None
            finally:
                await client.async_close()
            assert process.returncode is not None
            assert client._process is None
            assert client._bearer == ""
        print(
            json.dumps(
                {
                    "system": platform.system(),
                    "machine": platform.machine(),
                    "libc": platform.libc_ver(),
                    "python": platform.python_version(),
                    "homeassistant": version("homeassistant"),
                    "ninecli": version("ninecli"),
                    "empty_session_auth": "passed",
                    "child_reaped": "passed",
                    "cloud_or_control_requests": "none; run with network disabled",
                }
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
