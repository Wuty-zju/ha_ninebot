"""Account polling with per-vehicle, per-group freshness and partial failure."""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta
from functools import partial
from typing import Any, cast
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import adapters
from .archive_runtime import ArchiveStatistics
from .backend import BackendResult, NinebotBackend, NinecliBackend
from .capabilities import CONTROL_ACTIONS, ControlDecision, VehicleCapabilities, decide_control
from .client import NinecliClient
from .const import (
    BUSINESS_TIMEZONE,
    CONF_BUSINESS_UID,
    CONF_CONTROL_VEHICLES,
    CONF_CONTROLS,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DETAIL_INTERVAL,
    DOMAIN,
    VEHICLE_INTERVAL,
)
from .control_results import (
    CommandOutcome,
    Confirmation,
    ControlResult,
    ControlResults,
    ReadbackOutcome,
)
from .control_safety import LOCK_TARGETS
from .demand import Group, PollingDemand, polling_demand
from .exceptions import ErrorKind, NinebotAuthError, NinebotError
from .history import HistoryStore
from .history_sync import HistorySync
from .models import Freshness, TravelMonth, VehicleSnapshot
from .parsing import integer
from .query_broker import Priority, QueryBroker, QueryKey
from .raw import Endpoint, RawLimitError, RawRecord, RawStore, build_record
from .ride_archive import ArchiveError
from .ride_lifecycle import RideLifecycle
from .travel_observation import MonthObservation

LOGGER = logging.getLogger(__name__)
CONFIRMATION_DELAYS = (0, 2, 5)
CONFIRMATION_TIMEOUT = 10


def travel_record(record: RawRecord, month: str) -> TravelMonth:
    """Parse a bound raw owner off-loop, without capturing a mutable local."""
    return adapters.travel(record.payload(), month)


class NinebotCoordinator(DataUpdateCoordinator[dict[str, VehicleSnapshot]]):
    """Shared bounded reads, independent group commits and partial failure."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: NinecliClient,
        *,
        backend: NinebotBackend | None = None,
    ) -> None:
        self.client = client
        self.backend: NinebotBackend = backend or NinecliBackend(client)
        self.raw = RawStore()
        self.history = HistoryStore()
        self.history_sync = HistorySync(self)
        self.ride_lifecycles: dict[str, RideLifecycle] = {}
        self.statistics = ArchiveStatistics(
            hass, entry.entry_id, str(entry.data[CONF_BUSINESS_UID])
        )
        self.control_results = ControlResults()
        self.interval = max(
            30, min(3600, int(entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)))
        )
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=self.interval),
            always_update=True,
        )
        self.data = {}
        # Only mutual exclusion is shared between accounts, never observations.
        self._control_leases: set[str] = hass.data.setdefault(f"{DOMAIN}_control_leases", set())
        self.pending_controls: dict[str, str] = {}
        gate = hass.data.setdefault(f"{DOMAIN}_wire_gate", asyncio.Semaphore(2))
        self.broker = QueryBroker(
            gate, lambda: dt_util.utcnow(), on_auth_failure=self._invalidate_auth
        )
        self._generation = 0
        self._normalized: dict[tuple[Endpoint, str, str], tuple[bytes, Any]] = {}
        self._ownership: dict[str, int] = {}
        self._barriers: dict[str, int] = {}
        self._committed: dict[tuple[str, str], int] = {}
        self._groups: dict[QueryKey, asyncio.Task[None]] = {}
        self._queries: dict[QueryKey, asyncio.Task[RawRecord]] = {}
        self._refreshes: dict[QueryKey, asyncio.Task[bool]] = {}
        self._normalizations: dict[QueryKey, asyncio.Task[Any]] = {}
        self._shared_waiters: dict[asyncio.Task[Any], int] = {}
        self._list_freshness = Freshness()
        self._next_attempt: dict[tuple[str, str], float] = {}
        self._retry_after_until: dict[tuple[str, str], float] = {}
        self._failures: dict[tuple[str, str], int] = {}
        self._stopping = False
        self._authenticated = False
        self._control_pending = 0
        self._query_pending = 0
        self._active: set[asyncio.Task[Any]] = set()
        self._forced: dict[str, asyncio.Task[bool]] = {}
        self._shutdown_task: asyncio.Task[None] | None = None
        self._validity_cancel: Callable[[], None] | None = None

    def _invalidate_auth(self) -> None:
        self._authenticated = False
        self._generation += 1

    def local_vehicle_available(self, sn: str) -> bool:
        """Local historical reads remain separate from current cloud freshness."""
        return bool(not self._stopping and (snapshot := self.data.get(sn)) and snapshot.present)

    def _key(self, sn: str, endpoint: Endpoint, scope: str = "", barrier: int = 0) -> QueryKey:
        return QueryKey(
            self._generation, sn, endpoint.value, scope, barrier, self._ownership.get(sn, 0)
        )

    def _owns(self, key: QueryKey) -> bool:
        return (
            not self._stopping
            and key.generation == self._generation
            and key.ownership == self._ownership.get(key.vehicle, 0)
            and (
                not key.vehicle
                or (
                    self._authenticated
                    and key.vehicle in self.data
                    and self.data[key.vehicle].present
                )
            )
        )

    def _current_read(self, key: QueryKey, revision: int) -> bool:
        """The source owner and receipt must still win at every commit boundary."""
        if not self._owns(key):
            return False
        stored = self.raw.get(Endpoint(key.endpoint), key.vehicle, key.scope, now=dt_util.utcnow())
        return not revision or not stored or stored.request_revision <= revision

    async def _read(
        self, key: QueryKey, operation: Callable[[], Awaitable[BackendResult]], priority: Priority
    ) -> BackendResult:
        receipt = await self.broker.async_read(key, operation, lambda: self._owns(key), priority)
        return replace(
            receipt.value,
            started_at=receipt.started_at,
            received_at=receipt.received_at,
            request_revision=receipt.revision,
        )

    async def _normalize[T](
        self,
        record: RawRecord | None,
        sn: str,
        scope: str,
        operation: Callable[[], T],
    ) -> T:
        if record is None:
            return await self.hass.async_add_executor_job(operation)
        key = (record.endpoint, sn, scope)
        # A normalized view cannot outlive or outgrow its bounded raw owner.
        for old_key in tuple(self._normalized):
            if self.raw.get(*old_key, now=dt_util.utcnow()) is None:
                self._normalized.pop(old_key)
        if (
            (existing := self._normalized.get(key))
            and record.content_fingerprint
            and existing[0] == record.content_fingerprint
        ):
            return cast(T, existing[1])
        parse_key = self._key(sn, record.endpoint, f"{scope}:{record.content_fingerprint.hex()}")
        normalized = await self._shared(
            self._normalizations,
            parse_key,
            lambda: self.hass.async_add_executor_job(operation),
        )
        if (
            not self._stopping
            and self.raw.get(*key, now=dt_util.utcnow()) is record
            and len(self._normalized) < 128
        ):
            self._normalized[key] = (record.content_fingerprint, normalized)
        return normalized

    async def _shared[T](
        self,
        tasks: dict[QueryKey, asyncio.Task[T]],
        key: QueryKey,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        task = tasks.get(key)
        if task is None or task.done():

            async def run() -> T:
                return await operation()

            task = asyncio.create_task(run())
            tasks[key] = task
            self._active.add(task)
            if tasks is self._refreshes:
                self._forced[key.vehicle] = cast(asyncio.Task[bool], task)

            def finished(done: asyncio.Task[T]) -> None:
                self._active.discard(done)
                if self._forced.get(key.vehicle) is done:
                    self._forced.pop(key.vehicle)
                if tasks.get(key) is done:
                    tasks.pop(key)
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(finished)
        self._shared_waiters[task] = self._shared_waiters.get(task, 0) + 1
        try:
            return await asyncio.shield(task)
        finally:
            left = self._shared_waiters[task] - 1
            if left:
                self._shared_waiters[task] = left
            else:
                self._shared_waiters.pop(task)
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    @callback
    def _schedule_validity_check(self) -> None:
        """Notify local expiry/day/month changes without waiting for a cloud poll."""
        if self._validity_cancel:
            self._validity_cancel()
            self._validity_cancel = None
        if self._stopping:
            return
        now = dt_util.utcnow()
        local = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE))
        next_month = datetime(
            local.year + (local.month == 12),
            local.month % 12 + 1,
            1,
            tzinfo=local.tzinfo,
        )
        next_day = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        deadlines = [next_month, next_day]
        fresh = [(self._list_freshness, 3 * VEHICLE_INTERVAL)]
        for snapshot in self.data.values():
            if snapshot.present:
                fresh.extend(
                    [
                        (snapshot.status_freshness, max(3 * self.interval, 180)),
                        (snapshot.battery_freshness, 3 * DETAIL_INTERVAL),
                        (snapshot.travel_freshness, 3 * DETAIL_INTERVAL),
                        (snapshot.profile_freshness, 3 * VEHICLE_INTERVAL),
                    ]
                )
        for freshness, ttl in fresh:
            if freshness.succeeded_at is not None:
                expiry = freshness.succeeded_at + timedelta(seconds=ttl, milliseconds=1)
                if expiry > now:
                    deadlines.append(expiry)

        @callback
        def notify(at: datetime) -> None:
            self._validity_cancel = None
            self.async_update_listeners()
            if self.statistics.projection_needs_refresh(at):

                async def refresh_local() -> None:
                    await self.statistics.async_refresh_projection()
                    if not self._stopping:
                        self.async_update_listeners()

                task = self.hass.async_create_task(refresh_local())
                self._active.add(task)
                task.add_done_callback(self._active.discard)
            self._schedule_validity_check()

        self._validity_cancel = async_track_point_in_utc_time(self.hass, notify, min(deadlines))

    @callback
    def _async_refresh_finished(self) -> None:
        self._schedule_validity_check()

    def fresh(self, sn: str, group: str) -> bool:
        snapshot = self.data.get(sn)
        if snapshot is None or not snapshot.present or self._stopping or not self._authenticated:
            return False
        now = dt_util.utcnow()
        if group == "profile":
            return snapshot.profile_freshness.valid(now, 3 * VEHICLE_INTERVAL)
        if group == "status":
            return snapshot.status_freshness.valid(now, max(3 * self.interval, 180))
        if group == "battery":
            return snapshot.battery_freshness.valid(now, 3 * DETAIL_INTERVAL)
        if group == "travel":
            return (
                snapshot.travel is not None
                and snapshot.travel.month == adapters.month_at(now)
                and snapshot.travel_freshness.valid(now, 3 * DETAIL_INTERVAL)
            )
        return False

    def _due(self, sn: str, group: str, stamp: float) -> bool:
        return stamp >= self._next_attempt.get((sn, group), 0)

    def _attempt_finished(
        self, sn: str, group: str, stamp: float, interval: int, success: bool
    ) -> None:
        key = (sn, group)
        failures = 0 if success else self._failures.get(key, 0) + 1
        self._failures[key] = failures
        # Keep retries bounded and never stack catch-up cycles.
        delay = interval if success else min(interval, 30 * 2 ** min(failures - 1, 6))
        if not success:
            delay = min(interval, delay * random.uniform(0.8, 1.2))
        if success:
            self._retry_after_until.pop(key, None)
        self._next_attempt[key] = max(self._retry_after_until.get(key, 0), stamp + delay)

    async def _list(self, now: datetime) -> None:
        stamp = now.timestamp()
        if not self._due("", "profile", stamp):
            return
        try:
            key = self._key("", Endpoint.VEHICLES)
            result = await self._read(key, self.backend.async_vehicles, Priority.INTERACTIVE)
            found = await self.hass.async_add_executor_job(adapters.profiles, result.payload)
            if not found and not result.vehicles_complete:
                raise NinebotError(ErrorKind.SERVICE)
            await self._capture(result, guard=lambda: self._owns(key))
            if not self._current_read(key, result.request_revision) or (
                result.request_revision < self._committed.get(("", "profile"), 0)
            ):
                return
        except NinebotAuthError:
            raise
        except NinebotError as err:
            if not self._owns(key) or (
                err.request_revision
                and err.request_revision < self._committed.get(("", "profile"), 0)
            ):
                return
            self._list_freshness = replace(self._list_freshness, attempted_at=now, error=err.kind)
            self.data = {
                sn: replace(
                    snapshot,
                    profile_freshness=replace(
                        snapshot.profile_freshness, attempted_at=now, error=err.kind
                    ),
                )
                for sn, snapshot in self.data.items()
            }
            if err.retry_after:
                self._retry_after_until[("", "profile")] = (
                    dt_util.utcnow().timestamp() + err.retry_after
                )
            self._attempt_finished("", "profile", stamp, VEHICLE_INTERVAL, False)
            if self._list_freshness.succeeded_at is None:
                raise UpdateFailed(err.kind.value, retry_after=err.retry_after) from err
            return
        sns = {profile.sn for profile in found}
        retained = dict(self.data)
        for sn, snapshot in self.data.items():
            if sn in sns:
                continue
            if result.vehicles_complete:
                if not snapshot.present:
                    continue
                self._ownership[sn] = self._ownership.get(sn, 0) + 1
                self.raw.discard_vehicle(sn)
                for cache_key in tuple(self._normalized):
                    if cache_key[1] == sn:
                        self._normalized.pop(cache_key)
                self.ride_lifecycles.pop(sn, None)
                retained[sn] = replace(snapshot, present=False)
            else:
                retained[sn] = replace(
                    snapshot,
                    profile_freshness=replace(
                        snapshot.profile_freshness, attempted_at=now, error=ErrorKind.SERVICE
                    ),
                )
        self.data = retained
        observed = Freshness(result.started_at or now, result.received_at)
        for profile in found:
            old = self.data.get(profile.sn)
            self.data[profile.sn] = (
                replace(old, profile=profile, profile_freshness=observed)
                if old and old.present
                else VehicleSnapshot(profile, profile_freshness=observed)
            )
            if old and not old.present:
                self._ownership[profile.sn] = self._ownership.get(profile.sn, 0) + 1
                # Reappearing ownership is a new observation interval. Do not
                # revive cached readings or bridge energy across the absence.
                for group in ("status", "battery", "travel"):
                    self._next_attempt.pop((profile.sn, group), None)
                    self._failures.pop((profile.sn, group), None)
                self.raw.discard_vehicle(profile.sn)
        finished = result.received_at
        self._list_freshness = Freshness(
            now, finished, None if result.vehicles_complete else ErrorKind.SERVICE
        )
        self._authenticated = True
        self._committed[("", "profile")] = result.request_revision
        await self.statistics.async_record_profiles(
            tuple(found),
            finished,
            result.vehicles_complete,
            lambda: (
                self._current_read(key, result.request_revision)
                and self._committed.get(("", "profile")) == result.request_revision
            ),
        )
        if self._current_read(key, result.request_revision) and (
            self._committed.get(("", "profile")) == result.request_revision
        ):
            self._attempt_finished("", "profile", finished.timestamp(), VEHICLE_INTERVAL, True)

    async def _group(
        self,
        sn: str,
        group: Group,
        *,
        force: bool = False,
        include_last_ride: bool = True,
        include_previous_month: bool = False,
        single_attempt: bool = False,
    ) -> None:
        endpoint = {
            "status": Endpoint.STATUS,
            "battery": Endpoint.BATTERY,
            "travel": Endpoint.TRAVEL,
        }[group]
        scope = (
            adapters.month_at(dt_util.utcnow())
            if group == "travel"
            else "confirmation"
            if single_attempt
            else ""
        )
        key = self._key(sn, endpoint, scope, self._barriers.get(sn, 0) if group == "status" else 0)
        await self._shared(
            self._groups,
            key,
            lambda: self._group_update(
                sn, group, key, force, include_last_ride, include_previous_month, single_attempt
            ),
        )

    async def _group_update(
        self,
        sn: str,
        group: Group,
        key: QueryKey,
        force: bool,
        include_last_ride: bool,
        include_previous_month: bool,
        single_attempt: bool,
    ) -> None:
        now = dt_util.utcnow()
        stamp = now.timestamp()
        if not force and not self._due(sn, group, stamp):
            return
        if not self._owns(key):
            return
        snapshot = self.data[sn]
        success = False
        cached_success: datetime | None = None
        request_revision = 0
        try:
            if group == "status":
                result = await self._read(
                    key,
                    partial(
                        self.backend.async_status_once
                        if single_attempt
                        else self.backend.async_status,
                        sn,
                    ),
                    Priority.STATUS,
                )
                request_revision = result.request_revision
                # Validate explicit identity before retaining any new raw data.
                status = await self.hass.async_add_executor_job(
                    partial(adapters.status, result.payload, expected_sn=sn)
                )
                await self._capture(result, sn, guard=lambda: self._owns(key))
                updated = replace(
                    snapshot,
                    status=status,
                    status_freshness=Freshness(result.started_at or now, result.received_at),
                )
            elif group == "battery":
                result = await self._read(
                    key, partial(self.backend.async_battery, sn), Priority.BACKGROUND
                )
                request_revision = result.request_revision
                record = await self._capture(result, sn, guard=lambda: self._owns(key))
                battery = await self._normalize(
                    record, sn, "", partial(adapters.batteries, result.payload)
                )
                updated = replace(
                    snapshot,
                    battery=battery,
                    battery_freshness=Freshness(result.started_at or now, result.received_at),
                )
            else:
                month = adapters.month_at(now)
                cached = self.raw.get(Endpoint.TRAVEL, sn, month, now=now)
                previous_success = snapshot.travel_freshness.succeeded_at
                if (
                    cached
                    and not force
                    and 0 <= (now - cached.received_at).total_seconds() < DETAIL_INTERVAL
                    and (previous_success is None or cached.received_at > previous_success)
                ):
                    # An explicit current-month action already fetched newer data.
                    # Reuse it without extending its actual success timestamp.
                    result = BackendResult(
                        cached.payload(),
                        Endpoint.TRAVEL,
                        cached.received_at,
                        month,
                        backend_version=cached.backend_version,
                        request_revision=cached.request_revision,
                    )
                    cached_success = cached.received_at
                else:
                    result = await self._read(
                        key,
                        partial(self.backend.async_travel_month, sn, month),
                        Priority.BACKGROUND,
                    )
                    await self._capture(result, sn, guard=lambda: self._owns(key))
                record = self.raw.get(Endpoint.TRAVEL, sn, month, now=dt_util.utcnow())
                request_revision = result.request_revision
                if record and record.request_revision > request_revision and request_revision:
                    return
                travel = await self._normalize(
                    record,
                    sn,
                    month,
                    partial(travel_record, record, month)
                    if record
                    else partial(adapters.travel, result.payload, month),
                )
                applied_revision = result.request_revision
                travel_version = result.backend_version
                current_started = result.started_at or now
                # A slower adjacent-month query must not renew the actual
                # receipt of this month's payload or count as its fresh sample.
                cached_success = result.received_at
                if (include_last_ride and travel.last_ride is None) or (
                    include_previous_month and now.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).day <= 2
                ):
                    previous = adapters.previous_month(month)
                    try:
                        fallback_record = self.raw.get(Endpoint.TRAVEL, sn, previous, now=now)
                        if fallback_record and (
                            0
                            <= (now - fallback_record.received_at).total_seconds()
                            < DETAIL_INTERVAL
                        ):
                            result = BackendResult(
                                fallback_record.payload(),
                                Endpoint.TRAVEL,
                                fallback_record.received_at,
                                previous,
                                backend_version=fallback_record.backend_version,
                                request_revision=fallback_record.request_revision,
                            )
                        else:
                            result = await self._read(
                                self._key(sn, Endpoint.TRAVEL, previous),
                                partial(self.backend.async_travel_month, sn, previous),
                                Priority.BACKGROUND,
                            )
                            await self._capture(result, sn, guard=lambda: self._owns(key))
                        fallback_record = self.raw.get(
                            Endpoint.TRAVEL, sn, previous, now=dt_util.utcnow()
                        )
                        fallback = await self._normalize(
                            fallback_record,
                            sn,
                            previous,
                            partial(travel_record, fallback_record, previous)
                            if fallback_record
                            else partial(adapters.travel, result.payload, previous),
                        )
                        previous_key = replace(key, scope=previous)
                        previous_revision = (
                            fallback_record.request_revision
                            if fallback_record
                            else result.request_revision
                        )
                        if not self._current_read(key, applied_revision):
                            return
                        await self.statistics.async_record(
                            sn,
                            fallback,
                            fallback_record.received_at if fallback_record else result.received_at,
                            result.backend_version,
                            guard=lambda: self._current_read(previous_key, previous_revision),
                            receipt_ordered=True,
                        )
                        if include_last_ride and travel.last_ride is None:
                            travel = replace(travel, last_ride=fallback.last_ride)
                    except NinebotAuthError:
                        raise
                    except NinebotError:
                        # Optional last-ride fallback cannot invalidate current totals.
                        pass
                updated = replace(
                    snapshot,
                    travel=travel,
                    travel_freshness=Freshness(current_started, cached_success or dt_util.utcnow()),
                )
                observed = updated.travel_freshness.succeeded_at
                assert observed is not None

                def current_month() -> bool:
                    return self._current_read(key, applied_revision) and (
                        month == adapters.month_at(dt_util.utcnow())
                    )

                if not current_month():
                    return
                await self.statistics.async_record(
                    sn, travel, observed, travel_version, guard=current_month, receipt_ordered=True
                )
                if not current_month():
                    return
                lifecycle = self.ride_lifecycles.setdefault(sn, RideLifecycle())
                lifecycle.observe(travel.rides, observed)
                if (
                    travel.last_ride
                    and (last := travel.last_ride.ride)
                    and last.query_month != travel.month
                    and (record := self.raw.get(Endpoint.TRAVEL, sn, last.query_month, now=now))
                ):
                    # Reusing a previous-month cache is not a new observation.
                    lifecycle.observe((last,), record.received_at)
            if not self._owns(key):
                return
            current = self.data[sn]
            if (
                group == "travel"
                and updated.travel
                and updated.travel.month != adapters.month_at(dt_util.utcnow())
            ):
                return
            applied_revision = applied_revision if group == "travel" else result.request_revision
            revision_key = (sn, group)
            stored = self.raw.get(Endpoint(group), sn, key.scope, now=dt_util.utcnow())
            if stored and applied_revision and stored.request_revision > applied_revision:
                return
            if applied_revision and applied_revision < self._committed.get(revision_key, 0):
                return
            if group == "status":
                self.data[sn] = replace(
                    current, status=updated.status, status_freshness=updated.status_freshness
                )
            elif group == "battery":
                self.data[sn] = replace(
                    current, battery=updated.battery, battery_freshness=updated.battery_freshness
                )
            else:
                self.data[sn] = replace(
                    current, travel=updated.travel, travel_freshness=updated.travel_freshness
                )
            self._committed[revision_key] = max(
                applied_revision, self._committed.get(revision_key, 0)
            )
            success = True
        except NinebotAuthError:
            raise
        except NinebotError as err:
            if not self._owns(key):
                return
            if max(request_revision, err.request_revision) and max(
                request_revision, err.request_revision
            ) < self._committed.get((sn, group), 0):
                return
            snapshot = self.data[sn]
            if err.retry_after:
                self._retry_after_until[(sn, group)] = max(
                    self._retry_after_until.get((sn, group), 0),
                    dt_util.utcnow().timestamp() + err.retry_after,
                )
            if group == "status":
                failed = replace(snapshot.status_freshness, attempted_at=now, error=err.kind)
                self.data[sn] = replace(snapshot, status_freshness=failed)
            elif group == "battery":
                failed = replace(snapshot.battery_freshness, attempted_at=now, error=err.kind)
                self.data[sn] = replace(snapshot, battery_freshness=failed)
            else:
                failed = replace(snapshot.travel_freshness, attempted_at=now, error=err.kind)
                self.data[sn] = replace(snapshot, travel_freshness=failed)
        self._attempt_finished(
            sn,
            group,
            (cached_success if success and cached_success else dt_util.utcnow()).timestamp(),
            self.interval if group == "status" else DETAIL_INTERVAL,
            success,
        )

    async def _capture(
        self,
        result: BackendResult,
        sn: str = "",
        detail_id: str = "",
        *,
        guard: Callable[[], bool] | None = None,
    ) -> RawRecord | None:
        """Raw policy failure cannot discard otherwise valid normalized data."""
        try:
            record = await self.hass.async_add_executor_job(
                partial(
                    build_record,
                    result.endpoint,
                    result.payload,
                    result.received_at,
                    result.query_month,
                    backend_version=result.backend_version,
                    endpoint_version=result.endpoint_version,
                    schema_salt=self.raw.schema_salt,
                ),
            )
        except RawLimitError as err:
            self.raw.reject(err.reason)
            return None
        record = replace(record, request_revision=result.request_revision)
        if not self._stopping and (guard is None or guard()):
            scope = (result.query_month or "") if result.endpoint is Endpoint.TRAVEL else detail_id
            existing = self.raw.get(
                result.endpoint, sn, scope, now=dt_util.utcnow(), query_month=result.query_month
            )
            if existing and (
                existing.received_at > record.received_at
                or (existing.request_revision > record.request_revision > 0)
            ):
                return None  # A slower parser cannot restore an older response.
            if (
                existing
                and existing.received_at == record.received_at
                and existing.encoded == record.encoded
                and existing.request_revision >= record.request_revision
            ):
                return existing
            if self.raw.put(record, sn, scope):
                return record
        return None

    async def async_month_observation(self, sn: str, month: str) -> MonthObservation:
        """Closed history needs no cloud TTL; current observations keep their age."""
        adapters.previous_month(month)
        if not self.local_vehicle_available(sn):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="query_unavailable")
        generation, ownership = self._generation, self._ownership.get(sn, 0)
        now = dt_util.utcnow()
        try:
            cached = await self.statistics.async_cached_month(sn, month)
        except ArchiveError as err:
            self.statistics.record_error(err)
            cached = None
        if (
            generation != self._generation
            or ownership != self._ownership.get(sn, 0)
            or not self.local_vehicle_available(sn)
        ):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="query_unavailable")
        if cached is not None:
            age = (now - cached.received_at).total_seconds()
            current = month == adapters.month_at(now)
            if 0 <= age and (
                not current or age <= DETAIL_INTERVAL or not self.fresh(sn, "profile")
            ):
                return MonthObservation(
                    cached.travel,
                    cached.received_at,
                    cached.backend_version,
                    "ride_archive",
                    current and age > DETAIL_INTERVAL,
                )
        record = await self.async_query_month(sn, month)
        travel = await self._normalize(record, sn, month, partial(travel_record, record, month))
        return MonthObservation(travel, record.received_at, record.backend_version, "runtime_query")

    async def async_query_month(
        self, sn: str, month: str, *, priority: Priority = Priority.INTERACTIVE
    ) -> RawRecord:
        """Explicit history query; never changes current-month state/events."""
        adapters.previous_month(month)
        return await self._query_record(sn, Endpoint.TRAVEL, month, month, priority=priority)

    async def async_query_detail(self, sn: str, detail_id: str, month: str) -> RawRecord:
        """The action layer must resolve this ID from this vehicle's month index."""
        adapters.previous_month(month)
        if not detail_id or len(detail_id) > 256:
            raise NinebotError(ErrorKind.PROTOCOL)
        return await self._query_record(sn, Endpoint.TRIP_DETAIL, detail_id, month)

    async def _query_record(
        self,
        sn: str,
        endpoint: Endpoint,
        scope: str,
        month: str,
        *,
        priority: Priority = Priority.INTERACTIVE,
    ) -> RawRecord:
        if self._query_pending >= 4:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="busy")
        self._query_pending += 1
        task = asyncio.current_task()
        if task:
            self._active.add(task)
        key = self._key(sn, endpoint, f"{month}:{scope}")
        try:
            return await self._shared(
                self._queries,
                key,
                lambda: self._query_record_update(sn, endpoint, scope, month, key, priority),
            )
        finally:
            self._query_pending -= 1
            if task:
                self._active.discard(task)

    async def _query_record_update(
        self,
        sn: str,
        endpoint: Endpoint,
        scope: str,
        month: str,
        request_key: QueryKey,
        priority: Priority = Priority.INTERACTIVE,
    ) -> RawRecord:
        key = self._key(sn, endpoint, month if endpoint is Endpoint.TRAVEL else f"{month}:{scope}")
        if not self._owns(request_key) or not self._owns(key) or not self.fresh(sn, "profile"):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="query_unavailable")
        now = dt_util.utcnow()
        cached = self.raw.get(endpoint, sn, scope, now=now, query_month=month)
        ttl = 900 if endpoint is Endpoint.TRIP_DETAIL else DETAIL_INTERVAL
        if cached and 0 <= (now - cached.received_at).total_seconds() < ttl:
            return cached
        try:
            operation = (
                partial(self.backend.async_travel_month, sn, month)
                if endpoint is Endpoint.TRAVEL
                else partial(self.backend.async_trip_detail, sn, scope)
            )

            async def query() -> BackendResult:
                # A historical consumer may spend time in the account queue.
                # Polling can still use last-known ownership independently.
                if not self.fresh(sn, "profile"):
                    raise NinebotError(ErrorKind.CLOSED)
                return await operation()

            result = replace(await self._read(key, query, priority), query_month=month)
            if not self.fresh(sn, "profile"):
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="query_unavailable"
                )
            record = await self._capture(result, sn, scope, guard=lambda: self._owns(key))
            if record is None and self._owns(key):
                record = self.raw.get(endpoint, sn, scope, now=dt_util.utcnow(), query_month=month)
            if record is None:
                raise NinebotError(ErrorKind.PROTOCOL)
            if endpoint is Endpoint.TRAVEL:
                normalized = await self._normalize(
                    record, sn, month, partial(travel_record, record, month)
                )
                if not self._owns(key):
                    raise HomeAssistantError(
                        translation_domain=DOMAIN, translation_key="query_unavailable"
                    )
                await self.statistics.async_record(
                    sn,
                    normalized,
                    record.received_at,
                    record.backend_version,
                    guard=lambda: self._current_read(key, record.request_revision),
                    receipt_ordered=True,
                )
            if not self._owns(key) or not self.fresh(sn, "profile"):
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="query_unavailable"
                )
            return record
        except NinebotAuthError as err:
            raise self._manual_auth_failure() from err
        except NinebotError as err:
            if err.kind is ErrorKind.CLOSED:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="query_unavailable"
                ) from None
            raise

    def demand(self, sn: str) -> PollingDemand:
        return polling_demand(
            self.data[sn],
            self.async_contexts(),
            now=dt_util.utcnow(),
        )

    def discovery_diagnostics(self) -> dict[str, str | bool | None]:
        """Batch quality is separate from each positively observed identity."""
        freshness = self._list_freshness
        return {
            "complete": freshness.error is None if freshness.attempted_at else None,
            "attempted_at": freshness.attempted_at.isoformat() if freshness.attempted_at else None,
            "succeeded_at": freshness.succeeded_at.isoformat() if freshness.succeeded_at else None,
            "error": freshness.error.value if freshness.error else None,
        }

    async def _async_update_data(self) -> dict[str, VehicleSnapshot]:
        task = asyncio.current_task()
        if task:
            self._active.add(task)
        try:
            if self._stopping:
                return self.data
            await self._list(dt_util.utcnow())
            sns = [sn for sn, value in self.data.items() if value.present]
            demands = {sn: self.demand(sn) for sn in sns}
            # Small admission windows bound memory even for large account fleets.
            # Each status round is followed by lower priority consumers; the
            # broker also bounds bypass when interactive work arrives meanwhile.
            for start in range(0, len(sns), 8):
                window = sns[start : start + 8]
                await asyncio.gather(
                    *(self._group(sn, "status") for sn in window if "status" in demands[sn].groups)
                )
                for group in ("battery", "travel"):
                    await asyncio.gather(
                        *(
                            self._group(
                                sn,
                                group,
                                include_last_ride=demands[sn].last_ride,
                                include_previous_month=demands[sn].previous_month,
                            )
                            for sn in window
                            if group in demands[sn].groups
                        )
                    )
            return dict(self.data)
        except NinebotAuthError as err:
            self._invalidate_auth()
            raise ConfigEntryAuthFailed("auth") from err
        finally:
            if task:
                self._active.discard(task)

    async def async_refresh_vehicle(self, sn: str) -> bool:
        """Coalesce manual consumers, without reusing a pre-command request."""
        if self._stopping:
            return False
        key = self._key(sn, Endpoint.STATUS, barrier=self._barriers.get(sn, 0))

        async def refresh() -> bool:
            if sn not in self.data or not self.data[sn].present:
                return False
            try:
                await self._group(sn, "status", force=True)
            except NinebotAuthError as err:
                raise self._manual_auth_failure() from err
            self.async_set_updated_data(dict(self.data))
            self._schedule_validity_check()
            return self._owns(key)

        return await self._shared(self._refreshes, key, refresh)

    def control_decision(self, sn: str, action: str) -> ControlDecision:
        """Transport support alone does not grant a cloud/hardware permission."""
        snapshot = self.data.get(sn)
        options = self.config_entry.options if self.config_entry else {}
        return decide_control(
            action,
            snapshot.status.capabilities if snapshot else VehicleCapabilities(),
            (
                ("runtime_stopped", not self._stopping),
                ("authentication_required", self._authenticated),
                ("consent_missing", options.get(CONF_CONTROLS) is True),
                ("vehicle_not_allowlisted", sn in options.get(CONF_CONTROL_VEHICLES, [])),
                ("vehicle_not_present", snapshot is not None and snapshot.present),
                ("profile_stale", self.fresh(sn, "profile")),
                ("status_stale", self.fresh(sn, "status")),
                (
                    "profile_query_failed",
                    snapshot is not None and snapshot.profile_freshness.error is None,
                ),
                (
                    "status_query_failed",
                    snapshot is not None and snapshot.status_freshness.error is None,
                ),
                ("transport_unsupported", action in self.backend.control_actions),
                (
                    "parking_unverified",
                    action != "engine/stop"
                    or (
                        snapshot is not None
                        and snapshot.status.safety.permits_lock(dt_util.utcnow())
                    ),
                ),
            ),
        )

    def controls_enabled(self, sn: str, action: str | None = None) -> bool:
        """Availability and the pre/post-queue execution guard use one policy."""
        return any(
            self.control_decision(sn, candidate).allowed
            for candidate in ((action,) if action else CONTROL_ACTIONS)
        )

    def _manual_auth_failure(self) -> ConfigEntryAuthFailed:
        """Manual I/O bypasses the coordinator's automatic reauth handler."""
        self._invalidate_auth()
        error = ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth")
        self.async_set_update_error(error)
        self._schedule_validity_check()
        if self.config_entry:
            self.config_entry.async_start_reauth(self.hass)
        return error

    async def async_control(self, sn: str, action: str) -> None:
        if self._control_pending >= 4 or sn in self._control_leases:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="busy")
        self._control_pending += 1
        self._control_leases.add(sn)
        task = asyncio.current_task()
        if task:
            self._active.add(task)
        try:
            await self._control(sn, action)
        finally:
            self.pending_controls.pop(sn, None)
            self._control_leases.discard(sn)
            self._control_pending -= 1
            self.async_update_listeners()
            if task:
                self._active.discard(task)

    async def _control(self, sn: str, action: str) -> None:
        """Send once, reconcile status, and retain only safe outcome metadata."""
        if (
            self._stopping
            or not self.config_entry
            or not self.controls_enabled(sn, action)
            or sn not in self.data
            or not self.data[sn].present
        ):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="parking_unverified"
                if "parking_unverified" in self.control_decision(sn, action).blockers
                else "controls_disabled",
            )
        result = None
        try:
            command_error = None
            owner = self._key(sn, Endpoint.STATUS)
            result = self.control_results.start(sn, action, dt_util.utcnow())
            target = LOCK_TARGETS.get(action)
            if target:
                result.target_locked = target[1]
                result.observed_locked = self.lock_state(sn, target[0])
                stamp = self.data[sn].status_freshness.succeeded_at
                if (
                    stamp is not None
                    and 0 <= (dt_util.utcnow() - stamp).total_seconds() <= 5
                    and result.observed_locked is target[1]
                ):
                    result.outcome = CommandOutcome.ALREADY_IN_TARGET
                    result.confirmation = Confirmation.ALREADY_IN_TARGET
                    return
                self.pending_controls[sn] = action
                result.confirmation = Confirmation.PENDING
                self.async_update_listeners()
            command_revision = 0
            try:
                receipt = await self.broker.async_command(
                    sn,
                    partial(self.backend.async_control, sn, action),
                    lambda: self._owns(owner) and self.controls_enabled(sn, action),
                )
                command_revision = receipt.revision
                result.outcome = CommandOutcome.ACCEPTED
            except NinebotAuthError:
                result.outcome = CommandOutcome.AUTH_REQUIRED
                result.error = ErrorKind.AUTH
                result.readback = ReadbackOutcome.SKIPPED
                result.confirmation = Confirmation.UNKNOWN if target else Confirmation.NOT_REQUESTED
                self._manual_auth_failure()
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="control_uncertain"
                ) from None
            except NinebotError as err:
                result.outcome = CommandOutcome.UNCERTAIN
                result.error = err.kind
                command_revision = err.request_revision
                command_error = err
            finally:
                self._barriers[sn] = self._barriers.get(sn, 0) + 1
            if command_error is not None and not command_revision:
                # Broker admission/guard/cooldown rejected before the operation.
                # No wire invocation occurred, so do not reconcile or imply it did.
                result.outcome = CommandOutcome.REJECTED
                result.readback = ReadbackOutcome.SKIPPED
                result.confirmation = Confirmation.NOT_REQUESTED
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="busy"
                    if command_error.kind is ErrorKind.BUSY
                    else "parking_unverified"
                    if "parking_unverified" in self.control_decision(sn, action).blockers
                    else "controls_disabled"
                    if command_error.kind is ErrorKind.CLOSED
                    else "control_not_sent",
                ) from None
            if target:
                refreshed = await self._confirm_lock(sn, result, target[0], owner, command_revision)
            else:
                refreshed = await self._control_readback(sn, result)
            if command_error is not None:
                # A successful GET does not turn an uncertain POST into success.
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="control_uncertain"
                ) from command_error
            if not refreshed:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="control_target_not_observed"
                    if result.confirmation is Confirmation.TARGET_NOT_OBSERVED
                    else "control_readback_failed",
                ) from None
        except asyncio.CancelledError:
            if result is not None:
                if result.outcome is CommandOutcome.PENDING:
                    result.outcome = CommandOutcome.CANCELLED
                    result.readback = ReadbackOutcome.SKIPPED
                elif result.readback is ReadbackOutcome.PENDING:
                    result.readback = ReadbackOutcome.CANCELLED
                if result.confirmation is Confirmation.PENDING:
                    result.confirmation = Confirmation.CANCELLED
            raise
        finally:
            if result is not None:
                result.finished_at = dt_util.utcnow()

    def lock_state(self, sn: str, key: str) -> bool | None:
        snapshot = self.data.get(sn)
        if snapshot is None:
            return None
        if key == "vehicle_lock":
            return snapshot.status.locked
        if key != "seat_lock":
            return None
        value = integer(snapshot.status.observations.get("barrel_lock_status"), 0, 1)
        return value == 0 if value is not None else None

    async def _confirm_lock(
        self,
        sn: str,
        result: ControlResult,
        key: str,
        owner: QueryKey,
        command_revision: int,
    ) -> bool:
        """Three single-attempt reads, one absolute deadline, no optimistic state."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        result.readback = ReadbackOutcome.PENDING
        try:
            async with asyncio.timeout_at(started + CONFIRMATION_TIMEOUT):
                for offset in CONFIRMATION_DELAYS:
                    if not self._owns(owner):
                        result.readback = ReadbackOutcome.SKIPPED
                        break
                    await asyncio.sleep(max(0, started + offset - loop.time()))
                    if not self._owns(owner):
                        result.readback = ReadbackOutcome.SKIPPED
                        break
                    # Every attempt has its own barrier; regular polling cannot
                    # supply an earlier or auto-retried receipt to this budget.
                    self._barriers[sn] = self._barriers.get(sn, 0) + 1
                    previous = self._committed.get((sn, "status"), 0)
                    result.read_attempts += 1
                    await self._group(sn, "status", force=True, single_attempt=True)
                    self.async_set_updated_data(dict(self.data))
                    self._schedule_validity_check()
                    if not self._owns(owner):
                        result.readback = ReadbackOutcome.SKIPPED
                        break
                    snapshot = self.data[sn]
                    result.readback_error = snapshot.status_freshness.error
                    result.readback = (
                        ReadbackOutcome.FAILED
                        if result.readback_error
                        else ReadbackOutcome.REFRESHED
                    )
                    if not result.readback_error and self._committed.get((sn, "status"), 0) > max(
                        previous, command_revision
                    ):
                        result.observed_locked = self.lock_state(sn, key)
                        if result.observed_locked is result.target_locked:
                            result.confirmation = (
                                Confirmation.OBSERVED_TARGET_COMMAND_UNCERTAIN
                                if result.outcome is CommandOutcome.UNCERTAIN
                                else Confirmation.CONFIRMED
                            )
                            return True
                    elif result.readback_error in {
                        ErrorKind.AUTH,
                        ErrorKind.CLOSED,
                        ErrorKind.BUSY,
                    }:
                        break
                    if self.broker.cooling_down:
                        break
        except NinebotAuthError:
            self._manual_auth_failure()
            result.readback = ReadbackOutcome.FAILED
            result.readback_error = ErrorKind.AUTH
        except TimeoutError:
            result.readback = ReadbackOutcome.FAILED
            result.readback_error = ErrorKind.CONNECTION
        result.confirmation = (
            Confirmation.TARGET_NOT_OBSERVED
            if result.readback is ReadbackOutcome.REFRESHED and result.observed_locked is not None
            else Confirmation.UNKNOWN
        )
        return False

    async def _control_readback(self, sn: str, result: ControlResult) -> bool:
        """One status reconciliation; never resend the command or mask its error."""
        snapshot = self.data.get(sn)
        if self._stopping or not self._authenticated or snapshot is None or not snapshot.present:
            result.readback = ReadbackOutcome.SKIPPED
            return False
        result.readback = ReadbackOutcome.PENDING
        try:
            attempted = await self.async_refresh_vehicle(sn)
        except ConfigEntryAuthFailed:
            result.readback = ReadbackOutcome.FAILED
            result.readback_error = ErrorKind.AUTH
            return False
        except NinebotError as err:
            result.readback = ReadbackOutcome.FAILED
            result.readback_error = err.kind
            return False
        snapshot = self.data.get(sn)
        if not attempted or self._stopping or snapshot is None or not snapshot.present:
            result.readback = ReadbackOutcome.SKIPPED
            return False
        result.readback_error = snapshot.status_freshness.error
        result.readback = (
            ReadbackOutcome.FAILED if result.readback_error else ReadbackOutcome.REFRESHED
        )
        return result.readback is ReadbackOutcome.REFRESHED

    async def async_close(self) -> None:
        self._stopping = True
        self._generation += 1
        if self._validity_cancel:
            self._validity_cancel()
            self._validity_cancel = None
        if self._shutdown_task is None:
            current = asyncio.current_task()
            tasks = (self._active | set(self._forced.values())) - {current}
            for task in tasks:
                task.cancel()
            self._shutdown_task = asyncio.create_task(self._shutdown(tasks))
        try:
            await asyncio.shield(self._shutdown_task)
        except asyncio.CancelledError:
            await self._shutdown_task
            raise

    async def _shutdown(self, tasks: set[asyncio.Task[Any]]) -> None:
        """Finish cleanup before propagating cancellation of the unload caller."""
        await self.history_sync.async_close()
        await self.broker.async_close()
        await self.backend.async_close()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.statistics.async_save()
        await self.statistics.async_close()
        self.raw.clear()
        self._normalized.clear()
        self.history.clear()
        self.ride_lifecycles.clear()
        self.control_results.clear()
        await self.async_shutdown()
