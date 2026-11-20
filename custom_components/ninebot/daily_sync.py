"""Demand-driven month-list repair for daily sensors, without detail downloads.

One page per account/update, through the shared wire broker and durable hourly
budget. Only normalized facts enter the archive; raw pages/GPS require the
separate history feature. Completeness stays count-and-metric reconciled.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.util import dt as dt_util

from . import adapters
from .const import BUSINESS_TIMEZONE
from .exceptions import NinebotError
from .history_budget import configured_budget
from .models import TravelMonth
from .native_history import MOTOR_MONTH_CONTRACT, page_contract
from .parsing import previous_month
from .polling_policy import DAILY_REPAIR_INTERVAL, MONTH_CACHE_SECONDS
from .preservation import CONF_PRESERVE, retry_delay
from .raw import Endpoint, RawRecord
from .ride_archive import ArchiveError
from .statistics_store import timestamp

if TYPE_CHECKING:
    from .coordinator import NinebotCoordinator

MAX_PAGES = 256


def parse_page(record: RawRecord, month: str) -> TravelMonth:
    """Decode and normalize a captured page outside the event loop."""
    return adapters.travel(record.payload(), month)


@dataclass
class DailyCursor:
    total: int
    provider: str
    page: int = 1
    seen: set[str] = field(default_factory=set)
    attempts: int = 0
    retry_at: float = 0


class DailyStatisticsSync:
    def __init__(self, co: "NinebotCoordinator") -> None:
        self.co = co
        self.cursors: dict[tuple[str, str], DailyCursor] = {}
        self._last_vehicle: str | None = None
        self.next_due = 0.0

    async def async_step(self, now: datetime) -> None:
        co, store = self.co, self.co.statistics
        if co.monotonic() < self.next_due:
            return
        entry = co.config_entry
        if entry is None or not store.writable:
            return
        preserving = entry.options.get(CONF_PRESERVE) is True
        local = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE))
        current = local.strftime("%Y%m")
        previous = previous_month(current)
        vehicles = sorted(
            sn
            for sn in co.data
            if co.local_vehicle_available(sn)
            and co.background_ready(sn)
            and co.demand(sn).previous_month
        )
        self.cursors = {
            key: value
            for key, value in self.cursors.items()
            if key[0] in vehicles and key[1] in (current, previous)
        }
        if self._last_vehicle in vehicles:
            index = vehicles.index(self._last_vehicle) + 1
            vehicles = vehicles[index:] + vehicles[:index]
        for sn in vehicles:
            if not co.fresh(sn, "profile"):
                continue
            provider = page_contract(co.client, sn)
            motor = provider == MOTOR_MONTH_CONTRACT
            if preserving and (not motor or co.preservation.bootstrap.enabled(sn)):
                continue  # The history worker already owns pagination for this vehicle.
            months = (current, previous) if motor or local.day <= 2 else (current,)
            for month in months:
                record = store.month(sn, month)
                if record is None or store.month_rides(sn, month)[1]:
                    self.cursors.pop((sn, month), None)
                    continue
                total = record.ride_count
                if total is None or not 0 < total <= MAX_PAGES * 20:
                    continue  # Missing/implausible totals cannot justify an open-ended scan.
                cursor = self.cursors.setdefault((sn, month), DailyCursor(total, provider))
                if cursor.total != total or cursor.provider != provider:
                    cursor = self.cursors[(sn, month)] = DailyCursor(total, provider)
                if cursor.retry_at > now.timestamp():
                    continue
                self.next_due = co.monotonic() + DAILY_REPAIR_INTERVAL
                owns = co.preservation.months.guard(sn)
                cached = (
                    co.raw.get(Endpoint.TRAVEL, sn, month, now=now)
                    if motor and cursor.page == 1
                    else None
                )
                if cached and (
                    cached.endpoint_version != provider
                    or cached.received_at != timestamp(record.received_at)
                    or not 0 <= (now - cached.received_at).total_seconds() < MONTH_CACHE_SECONDS
                ):
                    cached = None
                if cached is None and not await store.archive.async_background_budget(
                    sn, now, configured_budget(entry.options), guard=owns
                ):
                    return
                self._last_vehicle = sn
                try:
                    raw = cached or await co.async_query_history_page(sn, month, cursor.page)
                    travel = await co.hass.async_add_executor_job(parse_page, raw, month)
                    summary = travel.summary
                    ids = {ride.ride_id for ride in travel.rides if ride.ride_id is not None}
                    if (
                        raw.endpoint_version != provider
                        or summary is None
                        or summary.ride_count != cursor.total
                        or len(ids) != len(travel.rides)
                        or not ids
                        or not ids - cursor.seen
                        or len(ids | cursor.seen) > cursor.total
                    ):
                        raise ValueError("Unstable daily list pagination")
                    if cached is None:
                        await store.async_record(
                            sn,
                            travel,
                            raw.received_at,
                            raw.backend_version,
                            guard=owns,
                            receipt_ordered=True,
                        )
                    if not owns() or not store.writable:
                        return  # A paused/failed write cannot advance the page cursor.
                    cursor.seen.update(ids)
                    cursor.attempts = 0
                    cursor.page += 1
                    if cursor.page > MAX_PAGES or len(cursor.seen) == cursor.total:
                        # If normalization/metrics still disagree, retry later;
                        # never label a partial or conflicting window complete.
                        cursor.page = 1
                        cursor.seen.clear()
                        cursor.retry_at = dt_util.utcnow().timestamp() + 3600
                except ConfigEntryAuthFailed:
                    raise
                except (ValueError, ArchiveError, HomeAssistantError, NinebotError) as err:
                    if isinstance(err, ArchiveError):
                        store.record_error(err)
                    cursor.attempts = min(10, cursor.attempts + 1)
                    cursor.retry_at = dt_util.utcnow().timestamp() + retry_delay(
                        cursor.attempts, err.retry_after if isinstance(err, NinebotError) else None
                    )
                    if isinstance(err, ValueError):
                        cursor.page = 1
                        cursor.seen.clear()
                return
