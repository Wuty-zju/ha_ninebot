"""Recent-month reconciliation and sparse native runs, under one history budget."""

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .archive_codec import encoded, month_data
from .archive_statistics import ArchiveStatisticsView
from .const import BUSINESS_TIMEZONE
from .parsing import previous_month
from .ride_archive import ArchiveError, ArchiveFailure

if TYPE_CHECKING:
    from .coordinator import NinebotCoordinator


class IncrementalSync:
    def __init__(self, co: "NinebotCoordinator") -> None:
        self.co = co

    async def async_seed(
        self, vehicles: tuple[str, ...], now: datetime, *, manual: bool = False
    ) -> None:
        runner, archive = self.co.preservation.bootstrap, self.co.statistics.archive
        if not runner.enabled() and not manual:
            return  # Finish already registered manual runs without enabling future automatic work.
        local = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE))
        current = local.strftime("%Y%m")
        months = (current, previous_month(current)) if local.day <= 7 else (current,)
        for sn in vehicles:
            if not runner.enabled(sn) or sn in runner.paused or not self.co.fresh(sn, "profile"):
                continue
            baseline = await archive.async_history_plan(sn)
            if baseline is None:
                continue
            # Register at most one unresolved baseline month; no wire request here.
            # Manual-only calls do not enable new automatic gap discovery.
            if not manual:
                try:
                    await archive.async_seed_history_gap(sn, now, guard=runner.guard(sn))
                except ArchiveError as err:
                    if err.kind is not ArchiveFailure.BUSY:
                        raise
            for month in months:
                if (
                    baseline.next_month is not None
                    and baseline.start_month <= month <= baseline.next_month
                ):
                    continue  # The first scan already has this month ahead; do not duplicate it.
                if await archive.async_history_gap_unresolved(sn, month):
                    if manual:
                        await archive.async_seed_history_gap(
                            sn, now, guard=runner.guard(sn), month=month
                        )
                    continue  # A serve/known-ID reconciliation cannot resolve an enumeration gap.
                archived = await archive.async_month(sn, month)
                if archived is None:
                    signature = hashlib.sha256(month.encode()).hexdigest()
                    complete = False
                else:
                    signature = hashlib.sha256(
                        encoded(month_data(archived.travel)).encode()
                    ).hexdigest()
                    try:
                        view = await self.co.statistics.async_view(sn, (month,))
                        assert isinstance(view, ArchiveStatisticsView)
                        complete = view.month_rides(sn, month)[1]
                    except ArchiveError as err:
                        if err.kind is not ArchiveFailure.BUDGET:
                            raise
                        complete = False
                try:
                    await archive.async_reconcile_history_month(
                        sn, month, now, signature, complete, guard=runner.guard(sn)
                    )
                except ArchiveError as err:
                    if err.kind is not ArchiveFailure.BUSY:
                        raise
                    # A full queue or stale claim for one vehicle must not starve others.
                    continue

    async def async_step(self, vehicles: tuple[str, ...], now: datetime, budget: int) -> bool:
        return await self.co.preservation.bootstrap.async_step(
            vehicles, now, budget, incremental=True
        )
