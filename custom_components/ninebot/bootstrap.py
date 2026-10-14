"""Lazy account-local full-history discovery, sharing the preservation worker.

This runner does not own authentication, a timer, a subprocess or a database.
It only advances one claimed page; detail preservation uses the existing queue.
"""

import secrets
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import BUSINESS_TIMEZONE
from .detail_health import HistoryHealth
from .exceptions import NinebotError
from .history_plan import HistoryPlan, HistoryState
from .preservation import retry_delay
from .ride_archive import ArchiveError, ArchiveFailure, vehicle_key

if TYPE_CHECKING:
    from .coordinator import NinebotCoordinator

CONF_AUTO_HISTORY = "auto_history_sync"


class HistoryBootstrap:
    def __init__(self, co: "NinebotCoordinator") -> None:
        self.co = co
        self.snapshots: dict[str, tuple[HistoryPlan, HistoryHealth]] = {}
        self.paused: set[str] = set()
        self.requested: set[str] = set()
        self.incremental_snapshots: dict[str, tuple[HistoryPlan, HistoryHealth]] = {}

    def enabled(self, sn: str | None = None) -> bool:
        entry = self.co.config_entry
        if entry is None or entry.options.get("preserve_history") is not True:
            return False
        return (sn is not None and sn in self.requested) or (
            entry.options.get(CONF_AUTO_HISTORY, entry.options.get("preserve_history", False))
            is True
        )

    def guard(
        self, sn: str, *, require_enabled: bool = True, respect_pause: bool = True
    ) -> Callable[[], bool]:
        co = self.co
        generation, ownership = co._generation, co._ownership.get(sn, 0)
        return lambda: (
            not co._stopping
            and co._generation == generation
            and co._ownership.get(sn, 0) == ownership
            and co.local_vehicle_available(sn)
            and (not require_enabled or self.enabled(sn))
            and (not respect_pause or sn not in self.paused)
        )

    async def async_seed(self, vehicles: tuple[str, ...], now: datetime) -> None:
        archive = self.co.statistics.archive
        self.snapshots = {
            sn: value
            for sn, value in self.snapshots.items()
            if sn in vehicles and self.co.local_vehicle_available(sn)
        }
        end = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).strftime("%Y%m")
        for sn in vehicles:
            if await archive.async_history_requested(sn):
                self.requested.add(sn)
            else:
                self.requested.discard(sn)
            if await archive.async_history_paused(sn):
                self.paused.add(sn)
                continue
            self.paused.discard(sn)
            if not self.enabled(sn):
                continue
            if not self.co.fresh(sn, "profile"):
                continue
            plan = await archive.async_history_plan(sn)
            if plan is None:
                plan = HistoryPlan.create(
                    secrets.token_hex(16), vehicle_key(sn), "200001", end, now.isoformat()
                )
                try:
                    await archive.async_set_history_plan(sn, plan, None, guard=self.guard(sn))
                except ArchiveError as err:
                    if err.kind is ArchiveFailure.BUSY:
                        continue
                    raise
            owns = self.guard(sn)
            await archive.async_backfill_detail_health(sn, plan.plan_id, guard=owns)
            if plan.state in {HistoryState.PRESERVE, HistoryState.VERIFY}:
                plan = await archive.async_finish_history_baseline(sn, plan, now, guard=owns)
            if owns():
                self.snapshots[sn] = (plan, await archive.async_history_health(sn, plan.plan_id))

    async def async_step(
        self, vehicles: tuple[str, ...], now: datetime, budget: int, *, incremental: bool = False
    ) -> bool:
        co, archive = self.co, self.co.statistics.archive
        for sn in vehicles:
            if not self.enabled(sn) or sn in self.paused or not co.fresh(sn, "profile"):
                continue
            owns = self.guard(sn)
            identity = None
            if incremental:
                runs = await archive.async_active_history_runs(sn)
                for run in runs:
                    if run.discovery_complete:
                        await archive.async_backfill_detail_health(sn, run.plan_id, guard=owns)
                        saved = await archive.async_finish_history_baseline(
                            sn, run, now, guard=owns
                        )
                        self.incremental_snapshots[sn] = (
                            saved,
                            await archive.async_history_health(sn, run.plan_id),
                        )
                    elif identity is None:
                        identity = run.plan_id
                if identity is None:
                    continue
            claimed = await archive.async_claim_history_page(
                sn, now, budget, guard=owns, plan_id=identity
            )
            if claimed is None:
                continue
            assert claimed.next_month is not None
            try:
                raw = await co.async_query_history_page(sn, claimed.next_month, claimed.next_page)
                await archive.async_accept_history_page(sn, claimed, raw, guard=owns)
                plan = (
                    await archive.async_history_run(sn, claimed.plan_id)
                    if incremental
                    else await archive.async_history_plan(sn)
                )
                if plan is not None and owns():
                    cache = self.incremental_snapshots if incremental else self.snapshots
                    cache[sn] = (
                        plan,
                        await archive.async_history_health(sn, plan.plan_id),
                    )
                await co.statistics.async_history_page_committed(sn, owns)
            except (NinebotError, ArchiveError, HomeAssistantError) as err:
                if isinstance(err, ArchiveError):
                    co.statistics.record_error(err)
                if owns() and co.statistics.writable:
                    reason = (
                        err.kind.value
                        if isinstance(err, NinebotError)
                        else "storage"
                        if isinstance(err, ArchiveError)
                        else "unavailable"
                    )
                    if reason not in {
                        "auth",
                        "connection",
                        "service",
                        "protocol",
                        "busy",
                        "storage",
                        "unavailable",
                    }:
                        reason = "unavailable"
                    retry = err.retry_after if isinstance(err, NinebotError) else None
                    await archive.async_wait_history_page(
                        claimed,
                        dt_util.utcnow(),
                        reason,
                        min(86400, retry_delay(claimed.attempts, retry)),
                        guard=owns,
                    )
            return True  # One shared budget reservation/wire operation at most.
        return False

    async def async_pause(self, sn: str, paused: bool) -> None:
        owns = self.guard(sn, require_enabled=False, respect_pause=False)
        await self.co.statistics.archive.async_set_history_paused(sn, paused, guard=owns)
        if paused:
            self.paused.add(sn)
        else:
            self.paused.discard(sn)
        self.co.statistics._notify_change(sn, owns)

    async def async_request(self, sn: str) -> None:
        """Persist explicit permission to finish manual work with automatic seeding off."""
        owns = self.guard(sn, require_enabled=False)
        await self.co.statistics.archive.async_request_history(sn, guard=owns)
        self.requested.add(sn)
