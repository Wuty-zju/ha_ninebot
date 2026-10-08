"""Explicit three-missing-month sync batches, durable progress, no auto polling."""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .archive_codec import stamp
from .const import DOMAIN
from .exceptions import NinebotError
from .query_broker import Priority
from .raw import Endpoint, RawRecord
from .ride_archive import utc_string, vehicle_key
from .sync_job import SyncJob, SyncState, month_count

if TYPE_CHECKING:
    from .coordinator import NinebotCoordinator

MAX_MISSING_MONTHS = 3


def sync_error(key: str) -> HomeAssistantError:
    return HomeAssistantError(translation_domain=DOMAIN, translation_key=key)


class HistorySync:
    """One transient executor over one persisted job per ConfigEntry."""

    def __init__(self, coordinator: "NinebotCoordinator") -> None:
        self.co = coordinator
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    async def async_start(self, sn: str, start: str, end: str) -> SyncJob:
        month_count(start, end)
        if not self.co.statistics.archive_ready:
            raise sync_error("statistics_unavailable")
        if self._lock.locked():
            raise sync_error("busy")
        async with self._lock:
            previous = await self.co.statistics.archive.async_sync_job()
            if previous is not None and not previous.terminal:
                if (previous.vehicle, previous.start_month, previous.end_month) != (
                    vehicle_key(sn),
                    start,
                    end,
                ):
                    raise sync_error("sync_conflict")
                return previous
            now = utc_string(dt_util.utcnow())
            job = SyncJob(uuid.uuid4().hex, vehicle_key(sn), start, end, end, now, now)
            await self.co.statistics.archive.async_set_sync_job(
                job, previous, guard=lambda: self.co.local_vehicle_available(sn)
            )
            return job

    async def async_job(self, sn: str, job_id: str | None = None) -> SyncJob:
        if not self.co.statistics.archive_ready:
            raise sync_error("statistics_unavailable")
        job = await self.co.statistics.archive.async_sync_job()
        if job is None or job.vehicle != vehicle_key(sn) or (job_id and job.job_id != job_id):
            raise sync_error("sync_job")
        return job

    async def async_advance(self, sn: str, job_id: str) -> SyncJob:
        if self._lock.locked():
            raise sync_error("busy")
        async with self._lock:
            self._task = asyncio.current_task()
            try:
                return await self._advance(sn, job_id)
            finally:
                self._task = None

    async def _advance(self, sn: str, job_id: str) -> SyncJob:
        job = await self.async_job(sn, job_id)
        if job.terminal:
            return job
        if job.retry_at and dt_util.utcnow() < stamp(job.retry_at):
            return job
        generation, ownership = self.co._generation, self.co._ownership.get(sn, 0)

        def owns() -> bool:
            return self.co.local_vehicle_available(sn) and (
                self.co._generation == generation and self.co._ownership.get(sn, 0) == ownership
            )

        archive = self.co.statistics.archive
        missing = 0
        while job.next_month is not None and not job.terminal and owns():
            month = job.next_month
            cached = await self.co.statistics.async_cached_month(sn, month)
            if not owns():
                raise sync_error("query_unavailable")
            if cached is not None:
                job = await archive.async_skip_sync_month(job, dt_util.utcnow(), guard=owns)
                continue  # Known partial months are not repeatedly fetched as pagination.
            if missing >= MAX_MISSING_MONTHS:
                break
            if not self.co.fresh(sn, "profile"):
                return await self._wait(job, "unavailable", owns)
            job = replace(
                job,
                state=SyncState.QUERYING,
                retry_at=None,
                reason=None,
                revision=job.revision + 1,
                updated_at=utc_string(dt_util.utcnow()),
            )
            before = await self.async_job(sn, job_id)
            await archive.async_set_sync_job(job, before, guard=owns)
            missing += 1
            try:
                record = await self.co.async_query_month(sn, month, priority=Priority.BACKGROUND)
                current = await self.async_job(sn, job_id)
                if current.revision != job.revision:
                    job = current  # The month writer atomically advanced the checkpoint.
                    continue
                # A reusable raw response may exist after a previous storage busy
                # failure; don't make another cloud request to repair persistence.
                from .coordinator import travel_record

                travel = await self.co.hass.async_add_executor_job(travel_record, record, month)

                def source_owned(query_month: str = month, source: RawRecord = record) -> bool:
                    return (
                        owns()
                        and self.co.fresh(sn, "profile")
                        and self.co.raw.get(Endpoint.TRAVEL, sn, query_month, now=dt_util.utcnow())
                        is source
                    )

                await self.co.statistics.async_record(
                    sn,
                    travel,
                    record.received_at,
                    record.backend_version,
                    guard=source_owned,
                    receipt_ordered=True,
                )
                current = await self.async_job(sn, job_id)
                if current.revision == job.revision:
                    stored = await self.co.statistics.async_cached_month(sn, month)
                    if stored is None:
                        return await self._wait(job, "storage", owns)
                    current = await archive.async_skip_sync_month(job, dt_util.utcnow(), guard=owns)
                job = current
            except (HomeAssistantError, NinebotError) as err:
                reason = err.kind.value if isinstance(err, NinebotError) else err.translation_key
                kind = (
                    "auth"
                    if reason == "invalid_auth"
                    else (
                        reason
                        if reason in {"connection", "service", "protocol", "busy"}
                        else "unavailable"
                    )
                )
                # Auth increments the generation: do not store under the old scope.
                if not owns():
                    raise
                return await self._wait(job, kind, owns)
        if not owns():
            raise sync_error("query_unavailable")
        return job

    async def _wait(self, job: SyncJob, reason: str, guard: Callable[[], bool]) -> SyncJob:
        now = dt_util.utcnow()
        failures = min(255, job.failures + 1)
        delay = min(3600, 60 * 2 ** min(failures - 1, 6))
        waiting = replace(
            job,
            state=SyncState.WAITING,
            revision=job.revision + 1,
            failures=failures,
            reason=reason,
            updated_at=utc_string(now),
            retry_at=utc_string(now + timedelta(seconds=delay))
            if reason not in {"auth", "unavailable"}
            else None,
        )
        await self.co.statistics.archive.async_set_sync_job(waiting, job, guard=guard)
        return waiting

    async def async_cancel(self, sn: str, job_id: str) -> SyncJob:
        await self.async_job(sn, job_id)  # Validate scope before interrupting anything.
        task = self._task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with self._lock:
            job = await self.async_job(sn, job_id)
            if job.terminal:
                return job
            cancelled = replace(
                job,
                state=SyncState.CANCELLED,
                revision=job.revision + 1,
                updated_at=utc_string(dt_util.utcnow()),
                retry_at=None,
                reason="cancelled",
            )
            await self.co.statistics.archive.async_set_sync_job(
                cancelled, job, guard=lambda: self.co.local_vehicle_available(sn)
            )
            return cancelled

    async def async_close(self) -> None:
        task = self._task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
