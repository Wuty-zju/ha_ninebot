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
from .exceptions import NinebotError
from .history_plan import HistoryPlan
from .preservation import retry_delay
from .ride_archive import ArchiveError, ArchiveFailure, vehicle_key

if TYPE_CHECKING:
    from .coordinator import NinebotCoordinator

CONF_AUTO_HISTORY = "auto_history_sync"


class HistoryBootstrap:
    def __init__(self, co: "NinebotCoordinator") -> None:
        self.co = co

    def enabled(self) -> bool:
        entry = self.co.config_entry
        if entry is None:
            return False
        return (
            entry.options.get(CONF_AUTO_HISTORY, entry.options.get("preserve_history", False))
            is True
        )

    def guard(self, sn: str) -> Callable[[], bool]:
        co = self.co
        generation, ownership = co._generation, co._ownership.get(sn, 0)
        return lambda: (
            not co._stopping
            and co._generation == generation
            and co._ownership.get(sn, 0) == ownership
            and co.local_vehicle_available(sn)
            and self.enabled()
        )

    async def async_seed(self, vehicles: tuple[str, ...], now: datetime) -> None:
        if not self.enabled():
            return
        archive = self.co.statistics.archive
        end = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).strftime("%Y%m")
        for sn in vehicles:
            if not self.co.fresh(sn, "profile"):
                continue
            if await archive.async_history_plan(sn) is not None:
                continue  # Reauth/reload cannot replace an existing checkpoint.
            plan = HistoryPlan.create(
                secrets.token_hex(16), vehicle_key(sn), "200001", end, now.isoformat()
            )
            try:
                await archive.async_set_history_plan(sn, plan, None, guard=self.guard(sn))
            except ArchiveError as err:
                if err.kind is not ArchiveFailure.BUSY:
                    raise

    async def async_step(self, vehicles: tuple[str, ...], now: datetime, budget: int) -> bool:
        if not self.enabled():
            return False
        co, archive = self.co, self.co.statistics.archive
        for sn in vehicles:
            if not co.fresh(sn, "profile"):
                continue
            owns = self.guard(sn)
            claimed = await archive.async_claim_history_page(sn, now, budget, guard=owns)
            if claimed is None:
                continue
            assert claimed.next_month is not None
            try:
                raw = await co.async_query_history_page(sn, claimed.next_month, claimed.next_page)
                await archive.async_accept_history_page(sn, claimed, raw, guard=owns)
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
