"""Approved detail work on the existing account preservation tick, never a new worker."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized
from homeassistant.util import dt as dt_util

from .assistant_approval import ApprovalTicket, TaskProgress, async_plan_scope
from .assistant_policy import AssistantError, AssistantScope
from .assistant_task_store import ApprovalStore, TaskStoreUnavailable
from .assistant_tasks import assert_task_owner, entry_task_owner
from .history_budget import configured_budget
from .preservation import DetailBudget, DetailBudgetUnavailable, DetailScope, DetailScopeState
from .ride_archive import ArchiveError, ArchiveFailure

if TYPE_CHECKING:
    from .coordinator import NinebotCoordinator
    from .runtime import NinebotConfigEntry


class AssistantExecution:
    def __init__(self, hass: HomeAssistant, co: "NinebotCoordinator", store: ApprovalStore) -> None:
        self.hass, self.co, self.store = hass, co, store
        self.tickets: dict[str, ApprovalTicket] = {}
        self.contexts: dict[str, Context] = {}
        self.closed = False

    @property
    def has_work(self) -> bool:
        return bool(not self.closed and self.tickets)

    def close(self) -> None:
        self.closed = True
        self.tickets.clear()
        self.contexts.clear()

    def _drop(self, task_id: str) -> None:
        self.tickets.pop(task_id, None)
        self.contexts.pop(task_id, None)

    async def _fail(self, ticket: ApprovalTicket, reason: str) -> None:
        try:
            await self.store.async_fail_execution(ticket, reason)
        except (AssistantError, TaskStoreUnavailable):
            pass  # A revoked/closed owner must not recreate its authority file.
        self._drop(ticket.task_id)

    async def _authorize(self, ticket: ApprovalTicket, context: Context) -> AssistantScope:
        actual = await self.store.async_get(ticket.task_id, context)
        if actual != ticket or not ticket.execution_valid(dt_util.utcnow()):
            raise AssistantError("retry_later")
        scope = await async_plan_scope(self.hass, ticket.plan, context)
        assert_task_owner(self.hass, scope, self.store)
        if not self.store.is_current(ticket):
            raise AssistantError("retry_later")
        return scope

    def _owns(self, scope: AssistantScope, ticket: ApprovalTicket) -> bool:
        try:
            scope.assert_task_current(self.hass)
        except AssistantError:
            return False
        return bool(
            not self.closed
            and scope.runtime.assistant_tasks is self.store
            and self.store.is_current(ticket)
            and ticket.execution_valid(dt_util.utcnow())
        )

    async def async_restore(self) -> None:
        for ticket in await self.store.async_execution_tickets():
            self.tickets.setdefault(ticket.task_id, ticket)
            # Server-owned persisted principal; never use an anonymous automation Context.
            self.contexts.setdefault(
                ticket.task_id, Context(user_id=ticket.plan.user_id, parent_id=ticket.context_id)
            )
        self.co.preservation.attach_assistant(self)

    async def async_accept(self, ticket: ApprovalTicket, context: Context) -> ApprovalTicket:
        scope = await self._authorize(ticket, context)
        # Approval is already durable. Keep it reachable even if the queue checkpoint
        # meets temporary actor/Store pressure; the next existing tick revalidates it.
        self.tickets[ticket.task_id] = ticket
        self.contexts[ticket.task_id] = context
        try:
            archive = self.co.statistics.archive
            used = await archive.async_detail_budget_used(
                scope.sn,
                ticket.plan.scope,
                DetailBudget(ticket.task_id, ticket.plan.attempts),
                guard=lambda: self._owns(scope, ticket),
            )
            queued = await self.store.async_checkpoint(
                ticket,
                TaskProgress(
                    "queued",
                    ticket.progress.saved if ticket.progress else 0,
                    used,
                    dt_util.utcnow(),
                ),
                context,
            )
            assert_task_owner(self.hass, scope, self.store)
            self.tickets[queued.task_id] = queued
            return queued
        finally:
            if not self.closed and scope.runtime.assistant_tasks is self.store:
                self.co.preservation.attach_assistant(self)

    async def async_cancel(self, task_id: str, context: Context) -> ApprovalTicket:
        """Stop only this requester's work; leave in-flight shared transport alone."""
        ticket = await self.store.async_cancel(task_id, context)
        self._drop(task_id)
        if self.co.preservation._assistant is self:
            self.co.preservation.start()
        return ticket

    async def _checkpoint(
        self,
        ticket: ApprovalTicket,
        context: Context,
        state: str,
        local: DetailScopeState,
        used: int,
        *,
        reason: str | None = None,
        retry_at: float | None = None,
    ) -> ApprovalTicket:
        changed = await self.store.async_checkpoint(
            ticket,
            TaskProgress(
                state,
                len(local.saved),
                used,
                dt_util.utcnow(),
                reason,
                datetime.fromtimestamp(retry_at, UTC) if retry_at is not None else None,
            ),
            context,
        )
        self.tickets[ticket.task_id] = changed
        if changed.terminal:
            self._drop(changed.task_id)
        return changed

    async def async_step(self, now: datetime) -> bool:
        if not self.has_work:
            return False
        task_id = next(iter(self.tickets))
        expected = self.tickets.pop(task_id)
        self.tickets[task_id] = expected  # Fair rotation, including paused vehicles/users.
        context = self.contexts[task_id]
        claimed_operation = False
        try:
            ticket = await self.store.async_get(task_id, context)
            expected = ticket
            if ticket.terminal:
                self._drop(task_id)
                return False
            if not ticket.execution_valid(now):
                await self._fail(ticket, "deadline_expired")
                return False
            scope = await self._authorize(ticket, context)
            archive = self.co.statistics.archive
            if not self.co.statistics.writable:
                return False

            def owns() -> bool:
                return self._owns(scope, ticket)

            await archive.async_prune_detail_budgets(now, guard=owns)
            budget = DetailBudget(task_id, ticket.plan.attempts)
            local = await archive.async_detail_scope_state(scope.sn, ticket.plan.scope, guard=owns)
            used = await archive.async_detail_budget_used(
                scope.sn, ticket.plan.scope, budget, guard=owns
            )
            if ticket.progress and len(local.saved) < ticket.progress.saved:
                await self._fail(ticket, "unavailable")
                return False
            if len(local.saved) == len(ticket.plan.scope.rides):
                await self._checkpoint(ticket, context, "completed", local, used)
                return False
            if not local.candidates or used >= ticket.plan.attempts:
                await self._checkpoint(
                    ticket,
                    context,
                    "partial" if local.saved else "failed",
                    local,
                    used,
                    reason="scope_changed" if not local.candidates else "budget_exhausted",
                )
                return False
            if (
                local.paused
                or not self.co.background_ready(scope.sn)
                or not self.co.fresh(scope.sn, "profile")
            ):
                await self._checkpoint(
                    ticket,
                    context,
                    "paused",
                    local,
                    used,
                    reason="auth_required" if not self.co._authenticated else None,
                )
                return False
            entry = self.co.config_entry
            assert entry is not None
            rate = configured_budget(entry.options)
            claimed = await archive.async_preservation_claim(
                scope.sn,
                now,
                rate,
                scope=ticket.plan.scope,
                task_budget=budget,
                guard=owns,
            )
            if claimed is None:
                account_at = local.account_at if rate else None
                waiting_budget = account_at is not None and account_at > now.timestamp()
                await self._checkpoint(
                    ticket,
                    context,
                    "waiting_budget" if waiting_budget else "retry_wait",
                    local,
                    used,
                    retry_at=account_at if waiting_budget else local.retry_at,
                )
                return False
            claimed_operation = True
            used = await archive.async_detail_budget_used(
                scope.sn, ticket.plan.scope, budget, guard=owns
            )
            running = await self._checkpoint(ticket, context, "running", local, used)
            expected = running
            single = DetailScope(
                tuple(row for row in ticket.plan.scope.rides if row[0] == claimed.ride.ride_id)
            )

            async def before_request() -> None:
                current = await self._authorize(running, context)
                mapping = await archive.async_detail_scope_state(
                    current.sn, single, guard=lambda: self._owns(current, running)
                )
                if not mapping.candidates:
                    raise AssistantError("retry_later")
                await current.async_validate(self.hass)
                if not self._owns(current, running):
                    raise AssistantError("not_authorized")

            await self.co.preservation.async_execute_detail(
                scope.sn,
                claimed,
                lambda: self._owns(scope, running),
                now,
                before_request=before_request,
            )
            if not self.store.is_current(running):
                self._drop(task_id)
                return True
            scope = await self._authorize(running, context)

            def owns_after() -> bool:
                return self._owns(scope, running)

            local = await archive.async_detail_scope_state(
                scope.sn, ticket.plan.scope, guard=owns_after
            )
            used = await archive.async_detail_budget_used(
                scope.sn, ticket.plan.scope, budget, guard=owns_after
            )
            if len(local.saved) == len(ticket.plan.scope.rides):
                await self._checkpoint(running, context, "completed", local, used)
            elif used >= ticket.plan.attempts:
                await self._checkpoint(
                    running,
                    context,
                    "partial" if local.saved else "failed",
                    local,
                    used,
                    reason="budget_exhausted",
                )
            else:
                await self._checkpoint(
                    running,
                    context,
                    "retry_wait",
                    local,
                    used,
                    reason="unavailable",
                    retry_at=local.retry_at,
                )
            return True
        except (AssistantError, Unauthorized) as err:
            if isinstance(err, AssistantError) and err.code == "retry_later":
                if not self.store.is_current(expected):
                    self._drop(task_id)
                return claimed_operation
            await self._fail(
                expected,
                "not_authorized"
                if not isinstance(err, AssistantError) or err.code == "not_authorized"
                else "unavailable",
            )
            return claimed_operation
        except DetailBudgetUnavailable:
            await self._fail(expected, "budget_unavailable")
            return claimed_operation
        except ArchiveError as err:
            if err.kind is ArchiveFailure.OBJECT:
                await self._fail(expected, "unavailable")
            return claimed_operation
        except TaskStoreUnavailable:
            # Preserve work for the next existing tick without repeated trace logging.
            return claimed_operation


def driver_for(hass: HomeAssistant, entry: "NinebotConfigEntry") -> AssistantExecution:
    store = entry_task_owner(hass, entry)
    preservation = entry.runtime_data.coordinator.preservation
    driver = preservation._assistant
    if driver is None or driver.store is not store or driver.closed:
        if driver is not None:
            driver.close()
        driver = AssistantExecution(hass, entry.runtime_data.coordinator, store)
        preservation._assistant = driver
    return driver


async def async_resume(hass: HomeAssistant, entry: "NinebotConfigEntry") -> None:
    if entry.runtime_data.assistant_tasks is None:
        probe = ApprovalStore(
            hass, entry.entry_id, entry.runtime_data.coordinator.statistics.archive.owner
        )
        if not await probe.async_execution_tickets():
            return
    await driver_for(hass, entry).async_restore()
