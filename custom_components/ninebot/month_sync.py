"""One bounded executor for sparse month repair and late-arrival reconciliation."""

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import BUSINESS_TIMEZONE
from .exceptions import NinebotError
from .parsing import previous_month
from .preservation import retry_delay
from .query_broker import Priority
from .ride_archive import ArchiveError, ArchiveFailure

if TYPE_CHECKING:
    from .coordinator import NinebotCoordinator


class SparseMonthSync:
    """Never recursively expands a user selection into a long date range."""

    def __init__(self, co: "NinebotCoordinator") -> None:
        self.co = co
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._vehicle: str | None = None
        self._month: str | None = None
        self._seeded: tuple[str, tuple[str, ...]] | None = None

    def guard(self, sn: str) -> Callable[[], bool]:
        co = self.co
        generation, ownership = co._generation, co._ownership.get(sn, 0)
        return lambda: (
            not co._stopping
            and generation == co._generation
            and ownership == co._ownership.get(sn, 0)
            and co.local_vehicle_available(sn)
        )

    async def async_seed_recent(self, vehicles: tuple[str, ...], now: datetime) -> None:
        local = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE))
        if local.day > 7:
            return
        eligible = tuple(sorted(sn for sn in vehicles if self.co.fresh(sn, "profile")))
        signature = (local.strftime("%Y%m%d"), eligible)
        if signature == self._seeded:
            return
        month = previous_month(local.strftime("%Y%m"))
        for sn in eligible:
            try:
                await self.co.statistics.archive.async_schedule_months(
                    sn, (month,), now, source="recent", guard=self.guard(sn)
                )
            except ArchiveError as err:
                if err.kind is ArchiveFailure.BUSY:
                    return  # A full queue must drain before automatic seeding can retry.
                raise
        self._seeded = signature

    async def async_step(
        self,
        sn: str,
        budget: int,
        now: datetime,
        *,
        months: tuple[str, ...] | None = None,
    ) -> bool:
        co, archive = self.co, self.co.statistics.archive
        if self._lock.locked() or not co.statistics.writable or not co.fresh(sn, "profile"):
            return False
        owns = self.guard(sn)
        async with self._lock:
            task = await archive.async_claim_month(sn, now, budget, guard=owns, months=months)
            if task is None:
                return False
            self._task, self._vehicle, self._month = asyncio.current_task(), sn, task.month
            try:
                record = await co.async_query_month(
                    sn, task.month, priority=Priority.BACKGROUND, refresh=True
                )
                saved = await archive.async_month(sn, task.month)
                if not owns():
                    return True
                if (
                    not co.statistics.writable
                    or saved is None
                    or saved.received_at < record.received_at
                ):
                    raise HomeAssistantError("Month not retained")
                summary = saved.travel.summary
                coverage = (
                    None if summary is None or summary.list_complete is None else saved.complete
                )
                await archive.async_finish_month(
                    sn, task, dt_util.utcnow(), success=True, complete=coverage, guard=owns
                )
            except (NinebotError, HomeAssistantError, ArchiveError) as err:
                if owns():
                    reason = (
                        err.kind.value
                        if isinstance(err, NinebotError)
                        else "storage"
                        if isinstance(err, ArchiveError)
                        else "unavailable"
                    )
                    if reason not in {
                        "connection",
                        "service",
                        "protocol",
                        "busy",
                        "auth",
                        "closed",
                        "storage",
                    }:
                        reason = "unavailable"
                    retry = err.retry_after if isinstance(err, NinebotError) else None
                    await archive.async_finish_month(
                        sn,
                        task,
                        dt_util.utcnow(),
                        success=False,
                        reason=reason,
                        delay=retry_delay(task.attempts, retry),
                        guard=owns,
                    )
            finally:
                self._task = self._vehicle = self._month = None
            return True

    async def async_cancel(self, sn: str, months: tuple[str, ...]) -> None:
        if (
            self._task is not None
            and self._task is not asyncio.current_task()
            and self._vehicle == sn
            and self._month in months
        ):
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self.co.statistics.archive.async_cancel_months(sn, months, guard=self.guard(sn))

    async def async_close(self) -> None:
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
