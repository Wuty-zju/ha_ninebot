"""Bounded account scheduling; wire transactions and result ownership stay separate."""

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Any, cast

from .exceptions import ErrorKind, NinebotError


class Priority(IntEnum):
    STATUS = 0
    INTERACTIVE = 1
    BACKGROUND = 2


@dataclass(frozen=True)
class QueryKey:
    generation: int
    vehicle: str
    endpoint: str
    scope: str = ""
    barrier: int = 0
    ownership: int = 0


@dataclass(frozen=True)
class QueryReceipt[T]:
    value: T
    started_at: datetime
    received_at: datetime
    revision: int


@dataclass
class _Job:
    key: QueryKey | None
    vehicle: str
    operation: Callable[[], Awaitable[Any]]
    guard: Callable[[], bool]
    priority: Priority
    sequence: int
    future: asyncio.Future[QueryReceipt[Any]]
    waiters: int = 0
    task: asyncio.Task[QueryReceipt[Any]] | None = None


class _Cooldown(NinebotError):
    """Reject queued work without extending the original recovery deadline."""


class QueryBroker:
    """One account worker, a shared two-wire gate, and no retained raw results.

    GET consumers share an in-flight receipt, including failures. Cancelling
    one waiter cannot cancel other consumers. The last waiter cancels unused
    work. POST jobs have no coalescing key and are never retried by this layer.
    A maximum high-priority streak gives old background work a bounded turn.
    """

    def __init__(
        self,
        wire_gate: asyncio.Semaphore,
        clock: Callable[[], datetime],
        *,
        max_pending: int = 16,
        on_auth_failure: Callable[[], None] | None = None,
    ) -> None:
        self._wire_gate = wire_gate
        self._clock = clock
        self._max_pending = max_pending
        self._command_limit = min(4, max_pending)
        self._read_limit = max(1, max_pending - min(4, max_pending // 4))
        self._on_auth_failure = on_auth_failure
        self._jobs: list[_Job] = []
        self._flights: dict[QueryKey, _Job] = {}
        self._worker: asyncio.Task[None] | None = None
        self._active: _Job | None = None
        self._closed = False
        self._sequence = 0
        self._revision = 0
        self._last_vehicle = ""
        self._streak = 0
        self._waiters = 0
        self._cooldown_until = 0.0
        self._cooldown_kind = ErrorKind.CONNECTION
        self._connection_failures = 0
        self._counters: Counter[str] = Counter()
        self._errors: Counter[str] = Counter()

    async def async_read[T](
        self,
        key: QueryKey,
        operation: Callable[[], Awaitable[T]],
        guard: Callable[[], bool],
        priority: Priority = Priority.INTERACTIVE,
    ) -> QueryReceipt[T]:
        return await self._submit(key, key.vehicle, operation, guard, priority)

    async def async_command(
        self, vehicle: str, operation: Callable[[], Awaitable[None]], guard: Callable[[], bool]
    ) -> QueryReceipt[None]:
        return await self._submit(None, vehicle, operation, guard, Priority.STATUS)

    async def _submit[T](
        self,
        key: QueryKey | None,
        vehicle: str,
        operation: Callable[[], Awaitable[T]],
        guard: Callable[[], bool],
        priority: Priority,
    ) -> QueryReceipt[T]:
        if self._closed:
            raise NinebotError(ErrorKind.CLOSED)
        if self._waiters >= 64:
            raise NinebotError(ErrorKind.BUSY)
        job = self._flights.get(key) if key else None
        if job is not None and not job.future.done():
            if job.waiters >= 32:
                raise NinebotError(ErrorKind.BUSY)
            job.priority = min(job.priority, priority)
            self._counters["coalesced"] += 1
        else:
            pending = [*self._jobs, *([self._active] if self._active else [])]
            limit = self._read_limit if key else self._command_limit
            same_budget = sum((job.key is not None) == (key is not None) for job in pending)
            if len(pending) >= self._max_pending or same_budget >= limit:
                raise NinebotError(ErrorKind.BUSY)
            self._sequence += 1
            future = asyncio.get_running_loop().create_future()
            future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            job = _Job(key, vehicle, operation, guard, priority, self._sequence, future)
            self._jobs.append(job)
            if key:
                self._flights[key] = job
            self._counters["submitted"] += 1
            if self._worker is None or self._worker.done():
                self._worker = asyncio.create_task(self._run())
        job.waiters += 1
        self._waiters += 1
        try:
            return cast(QueryReceipt[T], await asyncio.shield(job.future))
        finally:
            job.waiters -= 1
            self._waiters -= 1
            if job.waiters == 0 and not job.future.done():
                job.future.cancel()
                if job.task:
                    job.task.cancel()
                elif job in self._jobs:
                    self._jobs.remove(job)
                self._counters["cancelled"] += 1
            if job.future.done() and key and self._flights.get(key) is job:
                self._flights.pop(key)

    def _choose(self) -> _Job:
        if self._streak >= 3 and len({job.priority for job in self._jobs}) > 1:
            highest = min(job.priority for job in self._jobs)
            job = min(
                (job for job in self._jobs if job.priority > highest),
                key=lambda job: job.sequence,
            )
            self._streak = 0
        else:
            job = min(
                self._jobs,
                key=lambda job: (
                    job.priority,
                    job.vehicle == self._last_vehicle,
                    job.sequence,
                ),
            )
            self._streak += 1
        self._last_vehicle = job.vehicle
        self._jobs.remove(job)
        return job

    async def _execute(self, job: _Job) -> QueryReceipt[Any]:
        async with self._wire_gate:
            if self._closed or not job.guard():
                raise NinebotError(ErrorKind.CLOSED)
            delay = self._cooldown_until - asyncio.get_running_loop().time()
            if delay > 0:
                raise _Cooldown(self._cooldown_kind, retry_after=delay)
            self._revision += 1
            revision = self._revision
            started = self._clock()
            try:
                value = await job.operation()
            except NinebotError as err:
                err.request_revision = revision
                raise
            if self._closed or (job.key is not None and not job.guard()):
                raise NinebotError(ErrorKind.CLOSED)
            self._connection_failures = 0
            self._counters["completed"] += 1
            return QueryReceipt(value, started, self._clock(), revision)

    async def _run(self) -> None:
        try:
            while self._jobs and not self._closed:
                job = self._choose()
                if job.future.cancelled():
                    continue
                self._active = job
                job.task = asyncio.create_task(self._execute(job))
                try:
                    result = await job.task
                except asyncio.CancelledError:
                    job.future.cancel()
                    if self._closed:
                        raise
                except Exception as err:  # Worker transfers the sanitized failure to its callers.
                    if not isinstance(err, NinebotError):
                        err = NinebotError(ErrorKind.PROTOCOL)
                    if isinstance(err, NinebotError):
                        self._errors[err.kind.value] += 1
                        if err.kind is ErrorKind.AUTH and self._on_auth_failure:
                            self._on_auth_failure()
                        delay = err.retry_after if not isinstance(err, _Cooldown) else None
                        if not isinstance(err, _Cooldown) and err.kind in {
                            ErrorKind.CONNECTION,
                            ErrorKind.PLATFORM,
                        }:
                            self._connection_failures += 1
                            if self._connection_failures >= 2:
                                delay = max(delay or 0, min(120, 15 * self._connection_failures))
                        if delay:
                            self._cooldown_kind = err.kind
                            self._cooldown_until = max(
                                self._cooldown_until,
                                asyncio.get_running_loop().time() + delay,
                            )
                    if not job.future.done():
                        job.future.set_exception(err)
                else:
                    if not job.future.done():
                        job.future.set_result(result)
                finally:
                    self._active = None
        finally:
            if self._closed:
                for job in self._jobs:
                    job.future.cancel()
                self._jobs.clear()
                self._flights.clear()

    def diagnostics(self) -> dict[str, Any]:
        """Counts only: keys contain private identities and must never be exported."""
        return {
            "max_pending": self._max_pending,
            "read_pending_limit": self._read_limit,
            "command_pending_limit": self._command_limit,
            "account_wire_limit": 1,
            "global_wire_limit": 2,
            "pending": len(self._jobs) + (self._active is not None),
            "waiters": self._waiters,
            "closed": self._closed,
            "counts": dict(self._counters),
            "errors": dict(self._errors),
        }

    @property
    def cooling_down(self) -> bool:
        return self._cooldown_until > asyncio.get_running_loop().time()

    async def async_close(self) -> None:
        self._closed = True
        for job in [*self._jobs, *([self._active] if self._active else [])]:
            job.future.cancel()
            if job.task:
                job.task.cancel()
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        self._jobs.clear()
        self._flights.clear()
