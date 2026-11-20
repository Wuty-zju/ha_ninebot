"""Preserve native routing entries when vehicle discovery is incomplete."""

import asyncio
import json
import os
import tempfile
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any

from .async_utils import async_drain
from .const import MAX_RESPONSE_BYTES
from .exceptions import ErrorKind, NinebotError


class BusinessLine(StrEnum):
    EBIKE = "ebike"
    MOTOR = "motor"
    UNOBSERVED = "unobserved"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


def observed_business_line(client: object, sn: str) -> BusinessLine:
    lines = getattr(client, "vehicle_business_lines", None)
    value = lines.get(sn) if isinstance(lines, dict) else None
    return value if isinstance(value, BusinessLine) else BusinessLine.UNOBSERVED


async def cache_io[T](operation: Callable[..., T], *args: Any) -> T:
    """Finish filesystem work before releasing the client's serialization lock."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await async_drain(task)
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


def _cache_rows(data: bytes | None) -> list[dict[str, Any]]:
    try:
        raw = json.loads(data) if data else {}
    except (ValueError, UnicodeError, RecursionError):
        raise NinebotError(ErrorKind.PROTOCOL) from None
    values = raw.get("vehicles") if isinstance(raw, dict) else None
    if values is None:
        return []
    if not isinstance(values, list) or any(
        not isinstance(row, dict)
        or not isinstance(row.get("wnumber"), str)
        or not row["wnumber"].strip()
        for row in values
    ):
        raise NinebotError(ErrorKind.PROTOCOL)
    return values


def native_rows(data: bytes | None) -> list[dict[str, Any]]:
    values = _cache_rows(data)
    if any(row.get("business_line") not in ("ebike", "motor") for row in values):
        raise NinebotError(ErrorKind.PROTOCOL)
    return values


def cache_business_lines(directory: Path) -> dict[str, BusinessLine]:
    """Publish only bounded, recognized observations from the accepted cache."""
    try:
        rows = _cache_rows(read_cache(directory))
    except (OSError, NinebotError):
        return {}
    lines: dict[str, BusinessLine] = {}
    for row in rows:
        sn, value = row["wnumber"], row.get("business_line")
        match value:
            case "ebike":
                line = BusinessLine.EBIKE
            case "motor":
                line = BusinessLine.MOTOR
            case None:
                line = BusinessLine.UNOBSERVED
            case _:
                line = BusinessLine.UNSUPPORTED
        lines[sn] = line if lines.get(sn, line) == line else BusinessLine.AMBIGUOUS
    return lines


def merge_partial_cache(directory: Path, previous: bytes | None) -> None:
    """Merge native rows by identity; never invent a business-line mapping."""
    current = read_cache(directory)

    new = native_rows(current)
    if not new:
        raise NinebotError(ErrorKind.SERVICE)
    identities = {row["wnumber"] for row in new}
    merged = {
        "vehicles": new + [row for row in native_rows(previous) if row["wnumber"] not in identities]
    }
    data = json.dumps(merged, ensure_ascii=False).encode()
    if len(data) > MAX_RESPONSE_BYTES:
        raise NinebotError(ErrorKind.PROTOCOL)
    restore_cache(directory, data)


def reconcile_cache(
    directory: Path, previous: bytes | None, observed: frozenset[str], complete: bool
) -> bool:
    """Retain known native routes on silent omissions; never infer new routes."""
    missing = {row["wnumber"] for row in native_rows(previous)} - observed
    if not observed and (missing or not complete):
        restore_cache(directory, previous)
    elif missing or not complete:
        merge_partial_cache(directory, previous)
    return complete and not missing
