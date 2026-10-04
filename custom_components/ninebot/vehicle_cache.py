"""Preserve native routing entries when vehicle discovery is incomplete."""

import asyncio
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .const import MAX_RESPONSE_BYTES
from .exceptions import ErrorKind, NinebotError


async def cache_io[T](operation: Callable[..., T], *args: Any) -> T:
    """Finish filesystem work before releasing the client's serialization lock."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        try:
            await task
        except (OSError, NinebotError):
            # Do not replace cancellation with a filesystem error containing
            # a private path. The worker has finished; no late write remains.
            pass
        raise cancelled from None
    except OSError:
        raise NinebotError(ErrorKind.SERVICE) from None


def read_cache(directory: Path) -> bytes | None:
    path = directory / "vehicles.json"
    if path.is_symlink():
        raise NinebotError(ErrorKind.PROTOCOL)
    try:
        with path.open("rb") as file:
            data = file.read(MAX_RESPONSE_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(data) > MAX_RESPONSE_BYTES:
        raise NinebotError(ErrorKind.PROTOCOL)
    return data


def restore_cache(directory: Path, data: bytes | None) -> None:
    """Restore only routing cache, never roll back refreshed authentication."""
    path = directory / "vehicles.json"
    if path.is_symlink():
        raise NinebotError(ErrorKind.PROTOCOL)
    if data is None:
        path.unlink(missing_ok=True)
        return
    descriptor, name = tempfile.mkstemp(prefix=".vehicles-", dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def merge_partial_cache(directory: Path, previous: bytes | None) -> None:
    """Merge native rows by identity; never invent a business-line mapping."""
    current = read_cache(directory)

    def rows(data: bytes | None) -> list[dict[str, Any]]:
        try:
            raw = json.loads(data) if data else {}
        except (ValueError, UnicodeError):
            raise NinebotError(ErrorKind.PROTOCOL) from None
        values = raw.get("vehicles") if isinstance(raw, dict) else None
        if values is None:
            return []
        if not isinstance(values, list) or any(
            not isinstance(row, dict)
            or not isinstance(row.get("wnumber"), str)
            or not row["wnumber"].strip()
            or row.get("business_line") not in {"ebike", "motor"}
            for row in values
        ):
            raise NinebotError(ErrorKind.PROTOCOL)
        return values

    new = rows(current)
    if not new:
        raise NinebotError(ErrorKind.SERVICE)
    identities = {row["wnumber"] for row in new}
    merged = {"vehicles": new + [row for row in rows(previous) if row["wnumber"] not in identities]}
    data = json.dumps(merged, ensure_ascii=False).encode()
    if len(data) > MAX_RESPONSE_BYTES:
        raise NinebotError(ErrorKind.PROTOCOL)
    restore_cache(directory, data)
