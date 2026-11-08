"""Entry-owned task requests and lifecycle barriers; no execution endpoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Context, HomeAssistant

from .assistant import async_revoke_task_policy
from .assistant_approval import APPROVAL_TTL
from .assistant_plans import async_detail_proposal
from .assistant_policy import AssistantError, AssistantScope, async_authorize
from .assistant_task_store import ApprovalStore, TaskStoreUnavailable, async_remove_task_data
from .assistant_task_views import ticket_view
from .ride_archive import ArchiveError, ArchiveFailure
from .runtime import NinebotConfigEntry, RuntimeData


def task_owner(hass: HomeAssistant, scope: AssistantScope) -> ApprovalStore:
    """No await between checking the scope and selecting its one owner."""
    scope.assert_task_current(hass)
    runtime = scope.runtime
    owner = runtime.coordinator.statistics.archive.owner
    if runtime.assistant_tasks is None:
        runtime.assistant_tasks = ApprovalStore(hass, scope.entry.entry_id, owner)

        async def shutdown(_: object) -> None:
            runtime.assistant_task_stop = None
            await async_reset_tasks(hass, scope.entry, remove=False, permanent=True)

        runtime.assistant_task_stop = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, shutdown)
    store = runtime.assistant_tasks
    if store.entry_id != scope.entry.entry_id or store.account_key != owner:
        raise AssistantError("not_authorized")
    return store


def assert_task_owner(hass: HomeAssistant, scope: AssistantScope, store: ApprovalStore) -> None:
    scope.assert_task_current(hass)
    if scope.runtime.assistant_tasks is not store or store.closed:
        raise AssistantError("not_authorized")


async def async_request_details(
    hass: HomeAssistant,
    context: Context | None,
    vehicle_ref: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """Explicit local write; every private binding is derived by the server."""
    scope = await async_authorize(hass, context, vehicle_ref)
    scope.assert_task_current(hass)
    proposal = await async_detail_proposal(hass, context, vehicle_ref, start_date, end_date)
    await scope.async_validate(hass)
    scope.assert_task_current(hass)
    response = proposal.model_data()
    response.pop("task_created")
    if proposal.plan is None:
        return {**response, "task_recorded": False, "task": None}
    store = task_owner(hass, scope)
    ticket = await store.async_request(proposal.plan, context)
    await scope.async_validate(hass)
    scope.assert_task_current(hass)
    assert_task_owner(hass, scope, store)
    # Dedup can return an earlier plan timestamp; use its actual persisted digest.
    response["plan"] = {
        "digest": ticket.plan.digest,
        "approval_expires_at": (ticket.plan.created_at + APPROVAL_TTL).isoformat(),
    }
    return {
        **response,
        "task_recorded": True,
        "approval_required": ticket.approved_at is None,
        "task": {
            "task_id": ticket.task_id,
            "state": ticket_view(ticket)["state"],
        },
    }


async def async_reset_tasks(
    hass: HomeAssistant,
    entry: NinebotConfigEntry,
    *,
    remove: bool,
    permanent: bool = False,
    revoke_policy: bool = True,
) -> None:
    """Revoke/drain an owner; only successful removal permits its replacement."""
    runtime = getattr(entry, "runtime_data", None)
    if not isinstance(runtime, RuntimeData):
        if remove:
            if revoke_policy:
                async_revoke_task_policy(hass, entry, cleanup_owned=True)
            await async_remove_task_data(hass, entry.entry_id)
        return
    runtime.assistant_task_blocks += 1
    try:
        if remove and revoke_policy:
            async_revoke_task_policy(hass, entry, cleanup_owned=True)
        if runtime.assistant_task_stop is not None:
            runtime.assistant_task_stop()
            runtime.assistant_task_stop = None
        store = runtime.assistant_tasks
        if store is not None:
            await store.async_close(remove=remove)
        elif remove:
            await async_remove_task_data(hass, entry.entry_id)
        runtime.assistant_tasks = None
    finally:
        if not permanent:
            runtime.assistant_task_blocks -= 1


@asynccontextmanager
async def async_task_barrier(hass: HomeAssistant, entry: NinebotConfigEntry) -> AsyncIterator[None]:
    """An archive import cannot race a new approval or reuse existing authority."""
    runtime = entry.runtime_data
    runtime.assistant_task_blocks += 1
    try:
        try:
            await async_reset_tasks(hass, entry, remove=True)
        except TaskStoreUnavailable:
            raise ArchiveError(ArchiveFailure.BUSY) from None
        yield
    finally:
        runtime.assistant_task_blocks -= 1
