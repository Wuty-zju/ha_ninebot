"""HA lifecycle bridge: SQLite facts, read-only legacy source, memory views."""

import asyncio
import hashlib
from collections.abc import Callable
from copy import copy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .archive_statistics import ArchiveStatisticsView, StatisticsReader
from .backup import register_archive, unregister_archive
from .const import BUSINESS_TIMEZONE, DOMAIN
from .models import TravelMonth, VehicleProfile
from .parsing import previous_month
from .ride_archive import ArchiveError, ArchiveFailure, ArchiveMonth, RideArchive
from .ride_models import Ride
from .statistics_store import StoredMonth, StoredRide, TravelStatisticsStore


def archive_path(hass: HomeAssistant, entry_id: str) -> Path:
    """Never put account identifiers in a filename or share an entry directory."""
    entry_scope = hashlib.sha256(entry_id.encode()).hexdigest()
    return Path(hass.config.path(".storage", DOMAIN, "archives", entry_scope, "rides.sqlite3"))


class ArchiveStatistics(TravelStatisticsStore):
    """The old API is a current/previous-month projection, not a second writer."""

    def __init__(self, hass: HomeAssistant, entry_id: str, business_uid: str) -> None:
        super().__init__(hass, entry_id)
        self.archive = RideArchive(
            archive_path(hass, entry_id), hashlib.sha256(business_uid.encode()).hexdigest()
        )
        self.archive_ready = False
        self.error: ArchiveFailure | None = None
        self._archive_issue = f"ride_archive_{entry_id}"
        self._archive_lock = asyncio.Lock()
        self._vehicles: set[str] = set()
        self._projection = ArchiveStatisticsView()
        self._projection_month = ""
        self.persistence_enabled = False
        register_archive(hass, self.archive)

    async def async_load(self) -> None:
        await super().async_load()
        # No future call may write or trim the original migration source.
        self.persistence_enabled = False
        try:
            await self.archive.async_open()
            self.archive_ready = True
            if self.available:
                try:
                    await self.archive.async_import_legacy(self.months, self.rides)
                except ArchiveError as err:
                    # Full/read-only archives still provide existing history.
                    # The original source remains intact for a later retry.
                    self.record_error(err)
            self.available = True
            profiles = await self.archive.async_profiles()
            self._vehicles.update(profile.sn for profile in profiles)
            await self.async_refresh_projection()
            self.restored = self.restored or bool(profiles or self.months or self.rides)
        except ArchiveError as err:
            self.record_error(err, opening=True)

    def record_error(self, err: ArchiveError, *, opening: bool = False) -> None:
        self.error = err.kind
        if err.kind in {ArchiveFailure.BUSY, ArchiveFailure.CLOSED} or (
            err.kind is ArchiveFailure.OWNER and not opening
        ):
            return  # Lifecycle/queue/ownership is not an actionable storage Repair.
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._archive_issue,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=(
                "ride_archive_capacity"
                if err.kind is ArchiveFailure.CAPACITY
                else "ride_archive_io"
                if err.kind is ArchiveFailure.STORAGE
                else "ride_archive_invalid"
            ),
        )

    def _archive_success(self) -> None:
        # A read succeeding cannot clear a full/read-only write failure.
        if not self.archive.write_paused:
            self.error = None
            ir.async_delete_issue(self.hass, DOMAIN, self._archive_issue)

    def projection_needs_refresh(self, now: datetime) -> bool:
        return self.archive_ready and self._projection_month != now.astimezone(
            ZoneInfo(BUSINESS_TIMEZONE)
        ).strftime("%Y%m")

    @property
    def writable(self) -> bool:
        return (
            self.archive_ready
            and not self.archive.write_paused
            and not self.archive.backup_paused
            and self.error is None
        )

    @property
    def source_mode(self) -> str:
        return "ride_archive" if self.archive_ready else "bounded_statistics_ledger"

    def month_rides(self, sn: str, month: str) -> tuple[tuple[StoredRide, ...], bool]:
        if self.archive_ready:
            return self._projection.month_rides(sn, month)
        return super().month_rides(sn, month)

    async def async_view(self, sn: str, months: tuple[str, ...]) -> StatisticsReader:
        if not self.archive_ready:
            view = copy(self)
            view.months = {key: dict(values) for key, values in self.months.items()}
            view.rides = {key: dict(values) for key, values in self.rides.items()}
            return view
        records = await self.archive.async_months(sn, months)
        return await self.hass.async_add_executor_job(
            ArchiveStatisticsView.from_months, sn, records
        )

    async def _project(
        self,
        guard: Callable[[], bool] | None = None,
        *,
        now: datetime | None = None,
        vehicle: str | None = None,
    ) -> None:
        local = (now or dt_util.utcnow()).astimezone(ZoneInfo(BUSINESS_TIMEZONE))
        month = local.strftime("%Y%m")
        vehicles: tuple[str, ...]
        if vehicle is not None and self._projection_month == month:
            records = dict(self._projection.records)
            selections = dict(self._projection.selections)
            vehicles = (vehicle,)
        else:
            records, selections = {}, {}
            vehicles = tuple(sorted(self._vehicles))
        for sn in vehicles:
            view = await self.async_view(sn, (previous_month(month), month))
            assert isinstance(view, ArchiveStatisticsView)
            records.update(view.records)
            selections.update(view.selections)
        if guard is not None and not guard():
            return
        projection = ArchiveStatisticsView(records=records, selections=selections)
        months: dict[str, dict[str, StoredMonth]] = {}
        rides: dict[str, dict[str, StoredRide]] = {}
        for (key, query_month), record in records.items():
            months.setdefault(key, {})[query_month] = record
            for ride in selections[(key, query_month)][0]:
                rides.setdefault(key, {})[ride.ride_id] = ride
        self._projection = projection
        self._projection_month = month
        self.months, self.rides = months, rides

    async def async_refresh_projection(self) -> None:
        if not self.archive_ready:
            return
        async with self._archive_lock:
            try:
                await self._project()
            except ArchiveError as err:
                self.record_error(err)

    async def async_record(
        self,
        sn: str,
        travel: TravelMonth,
        received: datetime,
        backend_version: str | None = None,
        *,
        guard: Callable[[], bool] | None = None,
        receipt_ordered: bool = False,
    ) -> None:
        if not self.archive_ready:
            return
        async with self._archive_lock:
            if guard is not None and not guard():
                return
            try:
                await self.archive.async_record_month(
                    sn, travel, received, backend_version, guard=guard, ordered=receipt_ordered
                )
                if guard is not None and not guard():
                    return
                self._vehicles.add(sn)
                await self._project(guard, vehicle=sn)
                self._archive_success()
            except ArchiveError as err:
                self.record_error(err)

    async def async_record_profiles(
        self,
        profiles: tuple[VehicleProfile, ...],
        received: datetime,
        complete: bool,
        guard: Callable[[], bool],
    ) -> None:
        if not self.archive_ready:
            return
        async with self._archive_lock:
            if not guard():
                return
            try:
                await self.archive.async_record_profiles(
                    profiles, received, complete=complete, guard=guard, ordered=True
                )
                if not guard():
                    return
                if complete:
                    self._vehicles = {profile.sn for profile in profiles}
                else:
                    self._vehicles.update(profile.sn for profile in profiles)
                await self._project(guard)
                self._archive_success()
            except ArchiveError as err:
                self.record_error(err)

    async def async_cached_profiles(self) -> tuple[VehicleProfile, ...]:
        if not self.archive_ready:
            return ()
        try:
            return await self.archive.async_profiles()
        except ArchiveError as err:
            self.record_error(err)
            return ()

    async def async_cached_month(self, sn: str, month: str) -> ArchiveMonth | None:
        if not self.archive_ready:
            return None
        return await self.archive.async_month(sn, month)

    async def async_record_detail(
        self, sn: str, ride: Ride, received: datetime, guard: Callable[[], bool]
    ) -> None:
        if not self.archive_ready:
            return
        async with self._archive_lock:
            if not guard():
                return
            try:
                await self.archive.async_record_detail(
                    sn, ride, received, guard=guard, ordered=True
                )
                if not guard():
                    return
                await self._project(guard, vehicle=sn)
                self._archive_success()
            except ArchiveError as err:
                self.record_error(err)

    async def async_save(self) -> None:
        """Transactions already persist; the legacy Store must never write again."""

    async def async_close(self) -> None:
        try:
            await self.archive.async_close()
        finally:
            unregister_archive(self.hass, self.archive)
