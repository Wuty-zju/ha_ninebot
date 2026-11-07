"""Bounded, durable task confirmation; no listener, model or cloud executor."""

import asyncio
import json
from dataclasses import replace
from datetime import date, datetime
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .assistant_approval import (
    APPROVAL_TTL,
    ApprovalTicket,
    DetailPlan,
    async_approve_ticket,
    async_plan_scope,
)
from .assistant_policy import AssistantError, AssistantScope, async_require_admin
from .async_utils import async_drain
from .preservation import DetailBudget, DetailScope

MAX_TASKS = 24
MAX_PENDING_PER_USER = 5
MAX_BYTES = 256 * 1024


class TaskStoreUnavailable(Exception):
    """Task authority cannot be read or durably acknowledged."""


def ticket_data(ticket: ApprovalTicket) -> dict[str, Any]:
    p = ticket.plan
    return {
        "task_id": ticket.task_id,
        "nonce_hash": ticket.nonce_hash,
        "plan_hash": p.digest,
        "approved_at": ticket.approved_at.isoformat() if ticket.approved_at else None,
        "plan": {
            "user_id": p.user_id,
            "account_key": p.account_key,
            "vehicle_ref": p.vehicle_ref,
            "sn": p.sn,
            "start_date": p.start_date.isoformat(),
            "end_date": p.end_date.isoformat(),
            "rides": [list(row) for row in p.scope.rides],
            "attempts": p.attempts,
            "created_at": p.created_at.isoformat(),
        },
    }


def restore_ticket(raw: Any) -> ApprovalTicket:
    try:
        if not isinstance(raw, dict) or set(raw) != {
            "task_id",
            "plan",
            "plan_hash",
            "nonce_hash",
            "approved_at",
        }:
            raise ValueError
        p = raw["plan"]
        if not isinstance(p, dict) or set(p) != {
            "user_id",
            "account_key",
            "vehicle_ref",
            "sn",
            "start_date",
            "end_date",
            "rides",
            "attempts",
            "created_at",
        }:
            raise ValueError
        if not isinstance(p["rides"], list) or not 1 <= len(p["rides"]) <= 20:
            raise ValueError
        if any(not isinstance(row, list) or len(row) != 2 for row in p["rides"]):
            raise ValueError
        plan = DetailPlan(
            p["user_id"],
            p["account_key"],
            p["vehicle_ref"],
            p["sn"],
            date.fromisoformat(p["start_date"]),
            date.fromisoformat(p["end_date"]),
            DetailScope(tuple((row[0], row[1]) for row in p["rides"])),
            p["attempts"],
            datetime.fromisoformat(p["created_at"]),
        )
        if raw["plan_hash"] != plan.digest:
            raise ValueError("Plan contract changed")
        approved = (
            datetime.fromisoformat(raw["approved_at"]) if raw["approved_at"] is not None else None
        )
        return ApprovalTicket(raw["task_id"], plan, raw["nonce_hash"], approved)
    except (KeyError, TypeError, ValueError, OverflowError) as err:
        raise TaskStoreUnavailable("Invalid task record") from err


def read_envelope(path: Path, key: str) -> dict[str, Any] | None:
    try:
        with path.open("rb") as stream:
            encoded = stream.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(encoded) > MAX_BYTES:
        raise TaskStoreUnavailable("Task store exceeds its bound")
    try:
        raw = json.loads(encoded)
        if (
            not isinstance(raw, dict)
            or type(raw.get("version")) is not int
            or raw["version"] != 1
            or type(raw.get("minor_version")) is not int
            or raw["minor_version"] != 1
            or raw.get("key") != key
            or not isinstance(raw.get("data"), dict)
        ):
            raise ValueError
        return raw["data"]
    except (ValueError, RecursionError) as err:
        raise TaskStoreUnavailable("Invalid task store envelope") from err


def store_key(entry_id: str) -> str:
    return f"ninebot.{entry_id}.assistant_tasks_v1"


async def async_remove_task_data(hass: HomeAssistant, entry_id: str) -> None:
    """Trusted entry lifecycle only, independent of retained portable history."""
    store = Store[dict[str, Any]](hass, 1, store_key(entry_id), private=True, atomic_writes=True)
    await _async_remove_ack(hass, store)


async def _async_remove_ack(hass: HomeAssistant, store: Store[dict[str, Any]]) -> None:
    try:
        await store.async_remove()
        if await hass.async_add_executor_job(Path(store.path).exists):
            raise TaskStoreUnavailable("Task removal was not acknowledged")
    except OSError as err:
        raise TaskStoreUnavailable("Task store cannot be removed") from err


class ApprovalStore:
    """One runtime owner per entry. Every transition reloads actual persisted authority."""

    def __init__(self, hass: HomeAssistant, entry_id: str, account_key: str) -> None:
        self.hass, self.entry_id, self.account_key = hass, entry_id, account_key
        self.key = store_key(entry_id)
        self._store = Store[dict[str, Any]](hass, 1, self.key, private=True, atomic_writes=True)
        self._lock = asyncio.Lock()
        self._closed = False

    async def _read(self) -> dict[str, ApprovalTicket]:
        if self._closed:
            raise TaskStoreUnavailable("Task store is closed")
        try:
            data = await self.hass.async_add_executor_job(
                partial(read_envelope, Path(self._store.path), self.key)
            )
            if data is None:
                return {}
            if set(data) != {"tickets"} or not isinstance(data["tickets"], dict):
                raise TaskStoreUnavailable("Invalid task collection")
            if len(data["tickets"]) > MAX_TASKS:
                raise TaskStoreUnavailable("Too many task records")
            records = {key: restore_ticket(row) for key, row in data["tickets"].items()}
            if any(
                key != row.task_id or row.plan.account_key != self.account_key
                for key, row in records.items()
            ):
                raise TaskStoreUnavailable("Task account binding mismatch")
            return records
        except (OSError, UnicodeError) as err:
            raise TaskStoreUnavailable("Task store cannot be read") from err

    async def _commit(self, records: dict[str, ApprovalTicket]) -> None:
        if self._closed:
            raise AssistantError("not_authorized")
        data = {"tickets": {key: ticket_data(row) for key, row in records.items()}}
        # Leave room for the native envelope and pretty-printing before replacing a valid file.
        if len(records) > MAX_TASKS or len(json.dumps(data).encode()) > MAX_BYTES // 2:
            raise AssistantError("task_capacity")

        async def persist() -> None:
            try:
                await self._store.async_save(data)
                actual = await self.hass.async_add_executor_job(
                    partial(read_envelope, Path(self._store.path), self.key)
                )
                if actual != data:
                    raise TaskStoreUnavailable("Task write was not acknowledged")
            except OSError as err:
                raise TaskStoreUnavailable("Task store cannot be saved") from err

        # Keep the caller's lock until a cancelled write has actually completed.
        await async_drain(asyncio.create_task(persist()))

    async def _scope(self, plan: DetailPlan, context: Context | None) -> AssistantScope:
        scope = await async_plan_scope(self.hass, plan, context)
        if (
            self._closed
            or scope.entry.entry_id != self.entry_id
            or plan.account_key != self.account_key
        ):
            raise AssistantError("not_authorized")
        return scope

    async def async_request(self, plan: DetailPlan, context: Context | None) -> ApprovalTicket:
        await self._scope(plan, context)
        async with self._lock:
            records = await self._read()
            scope = await self._scope(plan, context)
            now = dt_util.utcnow()
            if not plan.created_at <= now < plan.created_at + APPROVAL_TTL:
                raise AssistantError("approval_expired")
            records = {
                key: row
                for key, row in records.items()
                if row.execution_valid(now)
                or (row.approved_at is None and now < row.plan.created_at + APPROVAL_TTL)
            }
            for row in records.values():
                # Only the request timestamp can differ for an identical active plan.
                if replace(plan, created_at=row.plan.created_at).digest == row.plan.digest:
                    return row
            if (
                sum(
                    row.plan.user_id == plan.user_id and row.approved_at is None
                    for row in records.values()
                )
                >= MAX_PENDING_PER_USER
            ):
                raise AssistantError("task_capacity")
            pending = ApprovalTicket(uuid4().hex, plan, None)
            records[pending.task_id] = pending
            await self._commit(records)
            await self._validate(scope)
            return pending

    async def async_prepare(
        self, task_id: str, context: Context | None
    ) -> tuple[ApprovalTicket, str]:
        await async_require_admin(self.hass, context)
        async with self._lock:
            records = await self._read()
            ticket = self._find(records, task_id)
            scope = await self._scope(ticket.plan, context)
            prepared, nonce = ticket.prepare(dt_util.utcnow())
            records[task_id] = prepared
            await self._commit(records)
            await self._validate(scope)
            return prepared, nonce  # Private human review only; never an Action or AI response.

    async def async_approve(
        self, task_id: str, context: Context | None, nonce: str, plan_hash: str
    ) -> ApprovalTicket:
        await async_require_admin(self.hass, context)
        async with self._lock:
            records = await self._read()
            ticket = self._find(records, task_id)
            scope = await self._scope(ticket.plan, context)
            approved = await async_approve_ticket(self.hass, ticket, context, nonce, plan_hash)
            await scope.runtime.coordinator.statistics.archive.async_initialize_detail_budget(
                scope.sn,
                ticket.plan.scope,
                DetailBudget(task_id, ticket.plan.attempts),
                guard=lambda: self._current(scope),
            )
            await scope.async_validate(self.hass)
            records[task_id] = approved
            await self._commit(records)
            await self._validate(scope)
            return approved

    def _current(self, scope: AssistantScope) -> bool:
        scope.assert_task_current(self.hass)
        return not self._closed

    async def _validate(self, scope: AssistantScope) -> None:
        await scope.async_validate(self.hass)
        if not self._current(scope):
            raise AssistantError("not_authorized")

    async def async_close(self, *, remove: bool = False) -> None:
        """Revoke this owner immediately, then drain its writer before optional removal."""
        self._closed = True

        async def finish() -> None:
            async with self._lock:
                if remove:
                    await _async_remove_ack(self.hass, self._store)

        await async_drain(asyncio.create_task(finish()))

    @staticmethod
    def _find(records: dict[str, ApprovalTicket], task_id: str) -> ApprovalTicket:
        if not isinstance(task_id, str) or task_id not in records:
            raise AssistantError("task_not_found")
        return records[task_id]
