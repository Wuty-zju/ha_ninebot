"""HA backup copies retain plans, never reusable task approval authority."""

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .assistant_task_store import (
    MAX_TASKS,
    ApprovalStore,
    TaskStoreUnavailable,
    read_envelope,
    restore_ticket,
    store_key,
)
from .async_utils import async_drain
from .const import DOMAIN


@dataclass
class TaskBackup:
    """Original authority exists only in this process while HA takes its copy."""

    store: Store[dict[str, Any]]
    original: dict[str, Any]
    sanitized: dict[str, Any]
    owner: ApprovalStore | None


async def _read(hass: HomeAssistant, store: Store[dict[str, Any]]) -> dict[str, Any] | None:
    return await hass.async_add_executor_job(partial(read_envelope, Path(store.path), store.key))


async def _write(hass: HomeAssistant, store: Store[dict[str, Any]], data: dict[str, Any]) -> None:
    async def persist() -> None:
        await store.async_save(data)
        if await _read(hass, store) != data:
            raise TaskStoreUnavailable("Task backup write was not acknowledged")

    await async_drain(asyncio.create_task(persist()))


async def async_prepare_tasks(hass: HomeAssistant, copies: list[TaskBackup]) -> None:
    """Called after the global backup barrier; drain existing entry writers first."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        owner = getattr(runtime, "assistant_tasks", None)
        store = Store[dict[str, Any]](
            hass, 1, store_key(entry.entry_id), private=True, atomic_writes=True
        )
        async with owner._lock if owner is not None else nullcontext():
            data = await _read(hass, store)
            if data is None:
                continue
            if (
                set(data) != {"tickets"}
                or not isinstance(data["tickets"], dict)
                or len(data["tickets"]) > MAX_TASKS
            ):
                raise TaskStoreUnavailable("Invalid task backup collection")
            # Use the same strict codec before touching a valid authority file.
            for task_id, raw in data["tickets"].items():
                if restore_ticket(raw).task_id != task_id:
                    raise TaskStoreUnavailable("Invalid task backup identity")
            sanitized = {
                "tickets": {
                    key: {**row, "nonce_hash": None, "approved_at": None}
                    for key, row in data["tickets"].items()
                }
            }
            if sanitized != data:
                # Register first so preparation failure/cancellation can unwind a completed write.
                copies.append(TaskBackup(store, data, sanitized, owner))
                await _write(hass, store, sanitized)


async def async_restore_tasks(hass: HomeAssistant, copies: list[TaskBackup]) -> None:
    """Restore live authority only if neither lifecycle nor file has changed."""

    async def restore(copy: TaskBackup) -> None:
        owner = copy.owner
        async with owner._lock if owner is not None else nullcontext():
            if owner is not None and owner.closed:
                return
            if await _read(hass, copy.store) == copy.sanitized:
                await _write(hass, copy.store, copy.original)

    async def finish() -> None:
        results = await asyncio.gather(*(restore(copy) for copy in copies), return_exceptions=True)
        if any(isinstance(result, BaseException) for result in results):
            raise TaskStoreUnavailable("Task backup authority could not be restored")

    await async_drain(asyncio.create_task(finish()))
