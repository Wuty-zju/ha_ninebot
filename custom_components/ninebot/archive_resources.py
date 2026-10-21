"""Cancellation-safe temporary export ownership and bounded admission."""

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN

PENDING_EXPORTS = f"{DOMAIN}_pending_exports"
MAX_EXPORTS = 4


class ExportBusy(Exception):
    """Active downloads and in-progress exports have filled the export slots."""


async def finish_job[T](hass: HomeAssistant, operation: Callable[..., T], *args: Any) -> T:
    """Drain filesystem work before cancellation can release its directory."""
    job = hass.async_add_executor_job(operation, *args)
    try:
        return await asyncio.shield(job)
    except asyncio.CancelledError:
        try:
            await job
        finally:
            raise


@dataclass
class ExportWorkspace:
    """Owned by the flow until the download manager accepts the completed file."""

    directory: Path | None = None
    published: bool = False

    def create(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="ninebot-export-"))


@asynccontextmanager
async def export_workspace(
    hass: HomeAssistant, downloads: Mapping[str, object]
) -> AsyncIterator[ExportWorkspace]:
    """Reserve before awaiting; release failed/cancelled exports after I/O drains."""
    pending: set[object] = hass.data.setdefault(PENDING_EXPORTS, set())
    if len(pending) + len(downloads) >= MAX_EXPORTS:
        raise ExportBusy
    ticket = object()
    pending.add(ticket)
    workspace = ExportWorkspace()
    try:
        await finish_job(hass, workspace.create)
        yield workspace
    finally:
        try:
            if workspace.directory is not None and not workspace.published:
                await finish_job(hass, shutil.rmtree, workspace.directory, True)
        finally:
            pending.discard(ticket)
