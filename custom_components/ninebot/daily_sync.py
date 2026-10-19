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
from .parsing import previous_month
from .preservation import CONF_PRESERVE, retry_delay
from .ride_archive import ArchiveError

if TYPE_CHECKING:
    from .coordinator import NinebotCoordinator

MAX_PAGES = 256


@dataclass
class DailyCursor:
    total: int
    page: int = 1
    seen: set[str] = field(default_factory=set)
    attempts: int = 0
    retry_at: float = 0


class DailyStatisticsSync:
    def __init__(self, co: "NinebotCoordinator") -> None:
        self.co = co
        self.cursors: dict[tuple[str, str], DailyCursor] = {}
        self._last_vehicle: str | None = None

    async def async_step(self, now: datetime) -> None:
        co, store = self.co, self.co.statistics
        entry = co.config_entry
        if entry is None or not store.writable or entry.options.get(CONF_PRESERVE) is True:
            return  # The opted-in history worker already owns paginated acquisition.
        local = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE))
        current = local.strftime("%Y%m")
        months = (current, previous_month(current)) if local.day <= 2 else (current,)
        vehicles = sorted(
            sn for sn in co.data if co.local_vehicle_available(sn) and co.demand(sn).previous_month
        )
        self.cursors = {
            key: value
            for key, value in self.cursors.items()
            if key[0] in vehicles and key[1] in months
        }
        if self._last_vehicle in vehicles:
            index = vehicles.index(self._last_vehicle) + 1
            vehicles = vehicles[index:] + vehicles[:index]
        for sn in vehicles:
            if not co.fresh(sn, "profile"):
                continue
            for month in months:
                record = store.month(sn, month)
                if record is None or store.month_rides(sn, month)[1]:
                    self.cursors.pop((sn, month), None)
                    continue
                total = record.ride_count
                if total is None or not 0 < total <= MAX_PAGES * 20:
                    continue  # Missing/implausible totals cannot justify an open-ended scan.
                cursor = self.cursors.setdefault((sn, month), DailyCursor(total))
                if cursor.total != total:
                    cursor = self.cursors[(sn, month)] = DailyCursor(total)
                if cursor.retry_at > now.timestamp():
                    continue
                owns = co.preservation.months.guard(sn)
                if not await store.archive.async_background_budget(
                    sn, now, configured_budget(entry.options), guard=owns
                ):
                    return
                self._last_vehicle = sn
                try:
                    raw = await co.async_query_history_page(sn, month, cursor.page)
                    travel = await co.hass.async_add_executor_job(
                        adapters.travel, raw.payload(), month
                    )
                    summary = travel.summary
                    ids = {ride.ride_id for ride in travel.rides if ride.ride_id is not None}
                    if (
                        summary is None
                        or summary.ride_count != cursor.total
                        or len(ids) != len(travel.rides)
                        or not ids
                        or not ids - cursor.seen
                        or len(ids | cursor.seen) > cursor.total
                    ):
                        raise ValueError("Unstable daily list pagination")
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
