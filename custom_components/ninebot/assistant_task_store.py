"""Bounded, durable task confirmation; no listener, model or cloud executor."""

import asyncio
import json
import logging
from dataclasses import replace
from datetime import date, datetime
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.importlib import async_import_module
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .assistant import (
    CONF_ASSISTANT,
    CONF_ASSISTANT_TASKS,
    TASK_POLICY_EPOCH,
    valid_policy_epoch,
)
from .assistant_approval import (
    APPROVAL_TTL,
    EXECUTION_TTL,
    ApprovalTicket,
    DetailPlan,
    TaskProgress,
    async_approve_ticket,
    async_plan_scope,
)
from .assistant_policy import AssistantError, AssistantScope, async_require_admin
from .async_utils import async_drain
from .preservation import DetailBudget, DetailBudgetUnavailable, DetailScope
from .ride_archive import ArchiveError

MAX_TASKS = 24
MAX_PENDING_PER_USER = 5
MAX_BYTES = 256 * 1024
TASK_MINOR = 4
_LOGGER = logging.getLogger(__name__)


class TaskStoreUnavailable(Exception):
    """Task authority cannot be read or durably acknowledged."""


def ticket_data(ticket: ApprovalTicket) -> dict[str, Any]:
    p = ticket.plan
    return {
        "task_id": ticket.task_id,
        "nonce_hash": ticket.nonce_hash,
        "plan_hash": p.digest,
        "approved_at": ticket.approved_at.isoformat() if ticket.approved_at else None,
        "context_id": ticket.context_id,
        "progress": {
            "state": ticket.progress.state,
            "saved": ticket.progress.saved,
            "attempts": ticket.progress.attempts,
            "updated_at": ticket.progress.updated_at.isoformat(),
            "reason": ticket.progress.reason,
            "retry_at": ticket.progress.retry_at.isoformat() if ticket.progress.retry_at else None,
        }
        if ticket.progress is not None
        else None,
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
        keys = {
            "task_id",
            "plan",
            "plan_hash",
            "nonce_hash",
            "approved_at",
        }
        if not isinstance(raw, dict) or set(raw) not in (
            keys,
            keys | {"progress"},
            keys | {"progress", "context_id"},
        ):
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
        progress = raw.get("progress")
        if progress is not None:
            progress_keys = {
                "state",
                "saved",
                "attempts",
                "updated_at",
                "reason",
            }
            if not isinstance(progress, dict) or set(progress) not in (
                progress_keys,
                progress_keys | {"retry_at"},
            ):
                raise ValueError
            progress = TaskProgress(
                progress["state"],
                progress["saved"],
                progress["attempts"],
                datetime.fromisoformat(progress["updated_at"]),
                progress["reason"],
                datetime.fromisoformat(progress["retry_at"])
                if progress.get("retry_at") is not None
                else None,
            )
        return ApprovalTicket(
            raw["task_id"], plan, raw["nonce_hash"], approved, progress, raw.get("context_id")
        )
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
            or raw["minor_version"] not in (1, 2, 3, TASK_MINOR)
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
    await async_drain(asyncio.create_task(_async_remove_ack(hass, store)))


def _task_path_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True  # Permission and other OS failures must propagate, never acknowledge absence.


async def _async_remove_ack(hass: HomeAssistant, store: Store[dict[str, Any]]) -> None:
    try:
        await store.async_remove()
        if await hass.async_add_executor_job(_task_path_exists, Path(store.path)):
            raise TaskStoreUnavailable("Task removal was not acknowledged")
    except OSError as err:
        raise TaskStoreUnavailable("Task store cannot be removed") from err


class ApprovalStore:
    """One runtime owner per entry. Every transition reloads actual persisted authority."""

    def __init__(self, hass: HomeAssistant, entry_id: str, account_key: str) -> None:
        self.hass, self.entry_id, self.account_key = hass, entry_id, account_key
        self.key = store_key(entry_id)
        entry = hass.config_entries.async_get_entry(entry_id)
        self.policy_epoch = entry.data.get(TASK_POLICY_EPOCH) if entry is not None else None
        self._store = Store[dict[str, Any]](
            hass, 1, self.key, minor_version=TASK_MINOR, private=True, atomic_writes=True
        )
        self._lock = asyncio.Lock()
        self._closed = False
        self._cache: dict[str, ApprovalTicket] = {}

    @property
    def closed(self) -> bool:
        return self._closed

    async def _read(self) -> dict[str, ApprovalTicket]:
        if self._closed:
            raise TaskStoreUnavailable("Task store is closed")
        self._assert_policy()
        try:
            data = await self.hass.async_add_executor_job(
                partial(read_envelope, Path(self._store.path), self.key)
            )
            if data is None:
                self._cache = {}
                return {}
            if set(data) not in ({"tickets"}, {"tickets", "policy_epoch"}) or not isinstance(
                data["tickets"], dict
            ):
                raise TaskStoreUnavailable("Invalid task collection")
            epoch = data.get("policy_epoch")
            if epoch is not None and not valid_policy_epoch(epoch):
                raise TaskStoreUnavailable("Invalid task policy")
            if epoch is not None and epoch != self.policy_epoch:
                self._cache = {}
                return {}  # A previous consent epoch cannot authorize or deduplicate a new task.
            if len(data["tickets"]) > MAX_TASKS:
                raise TaskStoreUnavailable("Too many task records")
            records = {key: restore_ticket(row) for key, row in data["tickets"].items()}
            if epoch is None:
                records = {
                    key: replace(row, approved_at=None, nonce_hash=None)
                    for key, row in records.items()
                }  # Legacy plans remain reviewable, never grandfathered authority.
            if any(
                key != row.task_id or row.plan.account_key != self.account_key
                for key, row in records.items()
            ):
                raise TaskStoreUnavailable("Task account binding mismatch")
            self._assert_policy()
            self._cache = dict(records)
            return records
        except (OSError, UnicodeError) as err:
            raise TaskStoreUnavailable("Task store cannot be read") from err

    async def _commit(self, records: dict[str, ApprovalTicket]) -> None:
        if self._closed:
            raise AssistantError("not_authorized")
        self._assert_policy()
        data = {
            "policy_epoch": self.policy_epoch,
            "tickets": {key: ticket_data(row) for key, row in records.items()},
        }
        # Leave room for the native envelope and pretty-printing before replacing a valid file.
        if len(records) > MAX_TASKS or len(json.dumps(data).encode()) > MAX_BYTES // 2:
            raise AssistantError("task_capacity")

        previous = self._cache

        async def persist() -> None:
            try:
                await self._store.async_save(data)
                actual = await self.hass.async_add_executor_job(
                    partial(read_envelope, Path(self._store.path), self.key)
                )
                if actual != data:
                    raise TaskStoreUnavailable("Task write was not acknowledged")
                self._cache = dict(records)
            except OSError as err:
                raise TaskStoreUnavailable("Task store cannot be saved") from err
            await self._async_notify(previous, records)

        # Keep the caller's lock until a cancelled write has actually completed.
        await async_drain(asyncio.create_task(persist()))

    def _notice_current(self) -> bool:
        try:
            self._assert_policy()
        except AssistantError:
            return False
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        runtime = getattr(entry, "runtime_data", None)
        return bool(
            not self._closed
            and entry is not None
            and runtime is not None
            and runtime.assistant_tasks is self
            and not runtime.assistant_task_blocks
            and not runtime.coordinator._stopping
            and entry.options.get(CONF_ASSISTANT) is True
            and entry.options.get(CONF_ASSISTANT_TASKS) is True
        )

    async def _async_notify(
        self, previous: dict[str, ApprovalTicket], records: dict[str, ApprovalTicket]
    ) -> None:
        if not self._notice_current():
            return
        try:
            notices = await async_import_module(self.hass, f"{__package__}.assistant_notifications")
            if self._notice_current():
                kind = notices.notice_kind(previous, records, dt_util.utcnow())
                if kind is not None:
                    notices.async_publish(self.hass, self.entry_id, kind)
        except (ImportError, AttributeError, HomeAssistantError, OSError):
            # Notices are a convenience; a saved task must not become a failed request.
            _LOGGER.debug("Ninebot task notice could not be shown")

    async def _scope(self, plan: DetailPlan, context: Context | None) -> AssistantScope:
        self._assert_policy()
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
                if (
                    row.terminal
                    and row.progress is not None
                    and now < row.progress.updated_at + EXECUTION_TTL
                )
                or row.execution_valid(now)
                or (row.approved_at is None and now < row.plan.created_at + APPROVAL_TTL)
            }
            for row in records.values():
                # Only the request timestamp can differ for an identical active plan.
                if (
                    not row.terminal
                    and replace(plan, created_at=row.plan.created_at).digest == row.plan.digest
                ):
                    return row
            if (
                sum(
                    row.plan.user_id == plan.user_id
                    and row.approved_at is None
                    and not row.terminal
                    for row in records.values()
                )
                >= MAX_PENDING_PER_USER
            ):
                raise AssistantError("task_capacity")
            # Keep terminal history only while space permits another useful application.
            terminals = sorted(
                (row for row in records.values() if row.terminal and row.progress is not None),
                key=lambda row: (
                    row.progress.updated_at if row.progress else row.plan.created_at,
                    row.task_id,
                ),
            )
            while len(records) >= MAX_TASKS and terminals:
                records.pop(terminals.pop(0).task_id)
            pending = ApprovalTicket(uuid4().hex, plan, None, context_id=scope.context.id)
            records[pending.task_id] = pending
            await self._commit(records)
            await self._validate(scope)
            if scope.runtime.coordinator.statistics.writable:
                try:
                    await scope.runtime.coordinator.statistics.archive.async_prune_detail_budgets(
                        now, guard=lambda: self._current(scope)
                    )
                except ArchiveError:
                    # Maintenance pressure cannot undo a saved application. Revocation
                    # still uses the original public authorization error contract.
                    await self._validate(scope)
            await self._validate(scope)
            return pending

    async def async_get(self, task_id: str, context: Context | None) -> ApprovalTicket:
        await async_require_admin(self.hass, context)
        async with self._lock:
            ticket = self._find(await self._read(), task_id)
            scope = await self._scope(ticket.plan, context)
            await self._validate(scope)
            return ticket

    def is_current(self, expected: ApprovalTicket) -> bool:
        """Synchronous commit guard after a validated read; not a substitute for live auth."""
        try:
            self._assert_policy()
        except AssistantError:
            return False
        return not self._closed and self._cache.get(expected.task_id) == expected

    async def async_execution_tickets(self) -> tuple[ApprovalTicket, ...]:
        """Trusted scheduler only; each candidate must authenticate its original user."""
        async with self._lock:
            return tuple(
                row
                for row in (await self._read()).values()
                if row.approved_at is not None and not row.terminal
            )

    async def async_fail_execution(self, expected: ApprovalTicket, reason: str) -> None:
        """System-owned revocation can only terminate, never approve or enlarge a plan."""
        async with self._lock:
            records = await self._read()
            current = records.get(expected.task_id)
            if current != expected or current is None or current.terminal:
                return
            previous = current.progress
            saved = previous.saved if previous else 0
            records[current.task_id] = replace(
                current,
                progress=TaskProgress(
                    "partial" if saved else "failed", saved, None, dt_util.utcnow(), reason
                ),
            )
            await self._commit(records)

    async def async_list(
        self, context: Context | None, vehicle_ref: str
    ) -> tuple[ApprovalTicket, ...]:
        user_id = await async_require_admin(self.hass, context)
        async with self._lock:
            records = await self._read()
            tickets = tuple(
                row
                for row in records.values()
                if row.plan.user_id == user_id and row.plan.vehicle_ref == vehicle_ref
            )
            scopes = [await self._scope(row.plan, context) for row in tickets]
            for scope in scopes:
                await self._validate(scope)
            for scope in scopes:
                if not self._current(scope):
                    raise AssistantError("not_authorized")
            return tickets

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
            assert approved.approved_at is not None
            await scope.runtime.coordinator.statistics.archive.async_initialize_detail_budget(
                scope.sn,
                ticket.plan.scope,
                DetailBudget(task_id, ticket.plan.attempts),
                guard=lambda: self._current(scope),
                expires_at=approved.approved_at + EXECUTION_TTL,
            )
            await scope.async_validate(self.hass)
            records[task_id] = approved
            await self._commit(records)
            await self._validate(scope)
            return approved

    async def async_checkpoint(
        self, expected: ApprovalTicket, progress: TaskProgress, context: Context | None
    ) -> ApprovalTicket:
        """Runner-only compare-and-set; cancellation wins over a late completion receipt."""
        await async_require_admin(self.hass, context)
        async with self._lock:
            records = await self._read()
            current = self._find(records, expected.task_id)
            scope = await self._scope(current.plan, context)
            if current != expected or not current.execution_valid(dt_util.utcnow()):
                raise AssistantError("retry_later")
            old = current.progress
            if old is not None and (
                progress.saved < old.saved
                or (
                    old.attempts is not None
                    and (progress.attempts is None or progress.attempts < old.attempts)
                )
                or progress.updated_at < old.updated_at
            ):
                raise ValueError("Task progress cannot move backwards")
            archive = scope.runtime.coordinator.statistics.archive
            used = await archive.async_detail_budget_used(
                scope.sn,
                current.plan.scope,
                DetailBudget(current.task_id, current.plan.attempts),
                guard=lambda: self._current(scope),
            )
            if progress.attempts != used:
                raise ValueError("Task progress must use durable attempt consumption")
            if progress.state == "completed":
                # A runner receipt alone is not durable completion evidence.
                for ride_id, _ in current.plan.scope.rides:
                    detail = await archive.async_private_detail(
                        scope.sn, ride_id, max_points=1, guard=lambda: self._current(scope)
                    )
                    if detail is None or detail.latest is None:
                        raise AssistantError("local_data_missing")
                await self._validate(scope)
            if old is not None and replace(progress, updated_at=old.updated_at) == old:
                # Keep the last durable observation time; a polling heartbeat is not
                # a new checkpoint. Current permissions/counter were still checked.
                await self._validate(scope)
                return current
            updated = replace(current, progress=progress)
            records[current.task_id] = updated
            await self._commit(records)
            await self._validate(scope)
            return updated

    async def async_cancel(self, task_id: str, context: Context | None) -> ApprovalTicket:
        """Cancel only this requester's task; no account pause or shared-call cancellation."""
        await async_require_admin(self.hass, context)
        async with self._lock:
            records = await self._read()
            current = self._find(records, task_id)
            scope = await self._scope(current.plan, context)
            if current.terminal:
                await self._validate(scope)
                return current
            old = current.progress
            attempts: int | None = 0
            if current.approved_at:
                try:
                    attempts = (
                        await scope.runtime.coordinator.statistics.archive.async_detail_budget_used(
                            scope.sn,
                            current.plan.scope,
                            DetailBudget(task_id, current.plan.attempts),
                            guard=lambda: self._current(scope),
                        )
                    )
                except (DetailBudgetUnavailable, ArchiveError):
                    attempts = None  # Cancelling must not require a readable archive.
            updated = replace(
                current,
                nonce_hash="" if current.approved_at else None,
                progress=TaskProgress(
                    "cancelled", old.saved if old else 0, attempts, dt_util.utcnow()
                ),
            )
            records[task_id] = updated
            await self._commit(records)
            await self._validate(scope)
            return updated

    def _current(self, scope: AssistantScope) -> bool:
        self._assert_policy()
        scope.assert_task_current(self.hass)
        return not self._closed

    def _assert_policy(self) -> None:
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if (
            not valid_policy_epoch(self.policy_epoch)
            or entry is None
            or entry.data.get(TASK_POLICY_EPOCH) != self.policy_epoch
        ):
            raise AssistantError("not_authorized")

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
