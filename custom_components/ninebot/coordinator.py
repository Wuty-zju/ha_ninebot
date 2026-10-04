"""Account polling with per-vehicle, per-group freshness and partial failure."""

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import adapters
from .backend import BackendResult, NinebotBackend, NinecliBackend
from .battery import battery_signature
from .capabilities import CONTROL_ACTIONS, ControlDecision, VehicleCapabilities, decide_control
from .client import NinecliClient
from .const import (
    BUSINESS_TIMEZONE,
    CONF_CONTROL_VEHICLES,
    CONF_CONTROLS,
    CONF_ESTIMATION,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DETAIL_INTERVAL,
    DOMAIN,
    VEHICLE_INTERVAL,
)
from .control_results import CommandOutcome, ControlResult, ControlResults, ReadbackOutcome
from .demand import Group, PollingDemand, polling_demand
from .exceptions import ErrorKind, NinebotAuthError, NinebotError
from .models import Freshness, VehicleSnapshot
from .raw import Endpoint, RawLimitError, RawRecord, RawStore, build_record
from .storage import ModelStorage

LOGGER = logging.getLogger(__name__)


class NinebotCoordinator(DataUpdateCoordinator[dict[str, VehicleSnapshot]]):
    """All update paths share a mutex; no one-car failure discards other cars."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: NinecliClient,
        *,
        models: ModelStorage | None = None,
        backend: NinebotBackend | None = None,
    ) -> None:
        self.client = client
        self.backend: NinebotBackend = backend or NinecliBackend(client)
        self.raw = RawStore()
        self.control_results = ControlResults()
        self.models = models
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
        self._mutex = asyncio.Lock()
        self._list_freshness = Freshness()
        self._next_attempt: dict[tuple[str, str], float] = {}
        self._failures: dict[tuple[str, str], int] = {}
        self._stopping = False
        self._authenticated = False
        self._control_pending = 0
        self._query_pending = 0
        self._active: set[asyncio.Task[Any]] = set()
        self._forced: dict[str, asyncio.Task[bool]] = {}
        self._shutdown_task: asyncio.Task[None] | None = None
        self._validity_cancel: Callable[[], None] | None = None

    @callback
    def _schedule_validity_check(self) -> None:
        """Notify local expiry/month changes without waiting for a cloud poll."""
        if self._validity_cancel:
            self._validity_cancel()
            self._validity_cancel = None
        if self._stopping or not self._authenticated:
            return
        now = dt_util.utcnow()
        local = now.astimezone(ZoneInfo(BUSINESS_TIMEZONE))
        next_month = datetime(
            local.year + (local.month == 12),
            local.month % 12 + 1,
            1,
            tzinfo=local.tzinfo,
        )
        deadlines = [next_month]
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
        if self.models and self.config_entry and self.config_entry.options.get(CONF_ESTIMATION):
            deadlines.append(
                (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            )

        @callback
        def notify(at: datetime) -> None:
            self._validity_cancel = None
            if self.models:
                for model in self.models.models.values():
                    model.rollover(at)
                self.models.schedule_save()
            self.async_update_listeners()
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
        self._next_attempt[key] = stamp + delay

    async def _list(self, now: datetime) -> None:
        stamp = now.timestamp()
        if not self._due("", "profile", stamp):
            return
        try:
            result = await self.backend.async_vehicles()
            found = adapters.profiles(result.payload)
            if not found and not result.vehicles_complete:
                raise NinebotError(ErrorKind.SERVICE)
            await self._capture(result)
        except NinebotAuthError:
            raise
        except NinebotError as err:
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
            self._attempt_finished("", "profile", stamp, VEHICLE_INTERVAL, False)
            if self._list_freshness.succeeded_at is None:
                raise UpdateFailed(err.kind.value) from err
            return
        sns = {profile.sn for profile in found}
        retained = dict(self.data)
        for sn, snapshot in self.data.items():
            if sn in sns:
                continue
            if result.vehicles_complete:
                self.raw.discard_vehicle(sn)
                retained[sn] = replace(snapshot, present=False)
            else:
                retained[sn] = replace(
                    snapshot,
                    profile_freshness=replace(
                        snapshot.profile_freshness, attempted_at=now, error=ErrorKind.SERVICE
                    ),
                )
        self.data = retained
        observed = Freshness(now, dt_util.utcnow())
        for profile in found:
            old = self.data.get(profile.sn)
            self.data[profile.sn] = (
                replace(old, profile=profile, profile_freshness=observed)
                if old and old.present
                else VehicleSnapshot(profile, profile_freshness=observed)
            )
            if old and not old.present:
                # Reappearing ownership is a new observation interval. Do not
                # revive cached readings or bridge energy across the absence.
                for group in ("status", "battery", "travel"):
                    self._next_attempt.pop((profile.sn, group), None)
                    self._failures.pop((profile.sn, group), None)
                self.raw.discard_vehicle(profile.sn)
                if self.models:
                    self.models.model(profile.sn).reset_baseline()
        finished = dt_util.utcnow()
        self._list_freshness = Freshness(
            now, finished, None if result.vehicles_complete else ErrorKind.SERVICE
        )
        self._authenticated = True
        self._attempt_finished("", "profile", finished.timestamp(), VEHICLE_INTERVAL, True)

    async def _group(
        self, sn: str, group: Group, *, force: bool = False, include_last_ride: bool = True
    ) -> None:
        now = dt_util.utcnow()
        stamp = now.timestamp()
        if not force and not self._due(sn, group, stamp):
            return
        snapshot = self.data[sn]
        success = False
        cached_success: datetime | None = None
        try:
            if group == "status":
                result = await self.backend.async_status(sn)
                status = adapters.status(result.payload, expected_sn=sn)
                await self._capture(result, sn)
                updated = replace(
                    snapshot, status=status, status_freshness=Freshness(now, dt_util.utcnow())
                )
            elif group == "battery":
                result = await self.backend.async_battery(sn)
                await self._capture(result, sn)
                battery = adapters.batteries(result.payload)
                updated = replace(
                    snapshot, battery=battery, battery_freshness=Freshness(now, dt_util.utcnow())
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
                        cached.payload(), Endpoint.TRAVEL, cached.received_at, month
                    )
                    cached_success = cached.received_at
                else:
                    result = await self.backend.async_travel_month(sn, month)
                    await self._capture(result, sn)
                travel = await self.hass.async_add_executor_job(
                    adapters.travel, result.payload, month
                )
                if include_last_ride and travel.last_ride is None:
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
                            )
                        else:
                            result = await self.backend.async_travel_month(sn, previous)
                            await self._capture(result, sn)
                        fallback = await self.hass.async_add_executor_job(
                            adapters.travel, result.payload, previous
                        )
                        travel = replace(travel, last_ride=fallback.last_ride)
                    except NinebotAuthError:
                        raise
                    except NinebotError:
                        # Optional last-ride fallback cannot invalidate current totals.
                        pass
                updated = replace(
                    snapshot,
                    travel=travel,
                    travel_freshness=Freshness(now, cached_success or dt_util.utcnow()),
                )
            self.data[sn] = updated
            success = True
        except NinebotAuthError:
            raise
        except NinebotError as err:
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
        self, result: BackendResult, sn: str = "", detail_id: str = ""
    ) -> RawRecord | None:
        """Raw policy failure cannot discard otherwise valid normalized data."""
        try:
            record = await self.hass.async_add_executor_job(
                build_record,
                result.endpoint,
                result.payload,
                result.received_at,
                result.query_month,
            )
        except RawLimitError:
            self.raw.rejected += 1
            return None
        if not self._stopping:
            scope = (result.query_month or "") if result.endpoint is Endpoint.TRAVEL else detail_id
            if self.raw.put(record, sn, scope):
                return record
        return None

    async def async_query_month(self, sn: str, month: str) -> RawRecord:
        """Explicit history query; never changes current-month state/events."""
        adapters.previous_month(month)
        return await self._query_record(sn, Endpoint.TRAVEL, month, month)

    async def async_query_detail(self, sn: str, detail_id: str, month: str) -> RawRecord:
        """The action layer must resolve this ID from this vehicle's month index."""
        adapters.previous_month(month)
        if not detail_id or len(detail_id) > 256:
            raise NinebotError(ErrorKind.PROTOCOL)
        return await self._query_record(sn, Endpoint.TRIP_DETAIL, detail_id, month)

    async def _query_record(self, sn: str, endpoint: Endpoint, scope: str, month: str) -> RawRecord:
        if self._query_pending >= 4:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="busy")
        self._query_pending += 1
        task = asyncio.current_task()
        if task:
            self._active.add(task)
        try:
            async with self._mutex:
                # Recheck ownership/load/authentication after the queue wait.
                if self._stopping or not self.fresh(sn, "profile"):
                    raise HomeAssistantError(
                        translation_domain=DOMAIN, translation_key="query_unavailable"
                    )
                now = dt_util.utcnow()
                cached = self.raw.get(endpoint, sn, scope, now=now, query_month=month)
                ttl = 900 if endpoint is Endpoint.TRIP_DETAIL else DETAIL_INTERVAL
                if cached and 0 <= (now - cached.received_at).total_seconds() < ttl:
                    return cached
                try:
                    if endpoint is Endpoint.TRAVEL:
                        result = await self.backend.async_travel_month(sn, month)
                    else:
                        result = replace(
                            await self.backend.async_trip_detail(sn, scope), query_month=month
                        )
                    record = await self._capture(result, sn, scope)
                    if record is None:
                        raise NinebotError(ErrorKind.PROTOCOL)
                    return record
                except NinebotAuthError as err:
                    raise self._manual_auth_failure() from err
        finally:
            self._query_pending -= 1
            if task:
                self._active.discard(task)

    def _sample_model(self, sn: str) -> None:
        """Sample after due BMS data, so startup cannot pretend a pack changed."""
        if not (
            self.models and self.config_entry and self.config_entry.options.get(CONF_ESTIMATION)
        ):
            return
        snapshot = self.data[sn]
        now = snapshot.status_freshness.succeeded_at
        if now is None or snapshot.status_freshness.error is not None:
            return
        model = self.models.model(sn)
        if not self.fresh(sn, "battery"):
            model.reset_baseline()
            self.models.schedule_save()
            return
        if model.sampled_at is not None and now.timestamp() <= model.sampled_at:
            return
        batteries = battery_signature(snapshot.battery)
        source = f"vehicle_soc:v2:{batteries}"
        legacy_source = "vehicle_soc:" + ",".join(
            sorted(b.key if b.identified else "unidentified" for b in snapshot.battery.batteries)
        )
        if model.source == legacy_source:
            # Upgrade the encoding, not the entity/model identity. Never bridge
            # the old ambiguous signature's interval or discard its totals.
            model.reset_baseline()
            model.source = source
        model.sample(snapshot.status.battery, now, source)
        self.models.schedule_save()

    def demand(self, sn: str) -> PollingDemand:
        return polling_demand(
            self.data[sn],
            self.async_contexts(),
            estimation=bool(
                self.models
                and self.config_entry
                and self.config_entry.options.get(CONF_ESTIMATION) is True
            ),
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
            async with self._mutex:
                if self._stopping:
                    return self.data
                now = dt_util.utcnow()
                if self.models:
                    for model in self.models.models.values():
                        model.rollover(now)
                await self._list(now)
                sns = [sn for sn, value in self.data.items() if value.present]
                demands = {sn: self.demand(sn) for sn in sns}
                # Status has priority, but every cycle also gives due details a turn.
                for sn in sns:
                    if "status" in demands[sn].groups:
                        await self._group(sn, "status")
                for sn in sns:
                    if "battery" in demands[sn].groups:
                        await self._group(sn, "battery")
                    if "travel" in demands[sn].groups:
                        await self._group(sn, "travel", include_last_ride=demands[sn].last_ride)
                    self._sample_model(sn)
                return dict(self.data)
        except NinebotAuthError as err:
            self._authenticated = False
            raise ConfigEntryAuthFailed("auth") from err
        finally:
            if task:
                self._active.discard(task)

    async def async_refresh_vehicle(self, sn: str) -> bool:
        """Coalesce active requests; report whether this status was attempted."""
        if self._stopping:
            return False
        if (existing := self._forced.get(sn)) is not None and not existing.done():
            return await asyncio.shield(existing)

        async def refresh() -> bool:
            async with self._mutex:
                if sn not in self.data or not self.data[sn].present:
                    return False
                try:
                    await self._group(sn, "status", force=True)
                except NinebotAuthError as err:
                    raise self._manual_auth_failure() from err
                self._sample_model(sn)
                self.async_set_updated_data(dict(self.data))
                self._schedule_validity_check()
                return True

        task = asyncio.create_task(refresh())
        self._forced[sn] = task
        try:
            return await task
        finally:
            if self._forced.get(sn) is task:
                self._forced.pop(sn)

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
        self._authenticated = False
        error = ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth")
        self.async_set_update_error(error)
        self._schedule_validity_check()
        if self.config_entry:
            self.config_entry.async_start_reauth(self.hass)
        return error

    async def async_control(self, sn: str, action: str) -> None:
        if self._control_pending >= 4:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="busy")
        self._control_pending += 1
        task = asyncio.current_task()
        if task:
            self._active.add(task)
        try:
            await self._control(sn, action)
        finally:
            self._control_pending -= 1
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
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="controls_disabled")
        result = None
        try:
            command_error = None
            async with self._mutex:
                # Permission/freshness may change while waiting in the queue.
                if not self.controls_enabled(sn, action):
                    raise HomeAssistantError(
                        translation_domain=DOMAIN, translation_key="controls_disabled"
                    )
                result = self.control_results.start(sn, action, dt_util.utcnow())
                try:
                    await self.backend.async_control(sn, action)
                    result.outcome = CommandOutcome.ACCEPTED
                except NinebotAuthError:
                    result.outcome = CommandOutcome.AUTH_REQUIRED
                    result.error = ErrorKind.AUTH
                    result.readback = ReadbackOutcome.SKIPPED
                    self._manual_auth_failure()
                    raise HomeAssistantError(
                        translation_domain=DOMAIN, translation_key="control_uncertain"
                    ) from None
                except NinebotError as err:
                    result.outcome = CommandOutcome.UNCERTAIN
                    result.error = err.kind
                    command_error = err
            refreshed = await self._control_readback(sn, result)
            if command_error is not None:
                # A successful GET does not turn an uncertain POST into success.
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="control_uncertain"
                ) from command_error
            if not refreshed:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="control_readback_failed"
                ) from None
        except asyncio.CancelledError:
            if result is not None:
                if result.outcome is CommandOutcome.PENDING:
                    result.outcome = CommandOutcome.CANCELLED
                    result.readback = ReadbackOutcome.SKIPPED
                elif result.readback is ReadbackOutcome.PENDING:
                    result.readback = ReadbackOutcome.CANCELLED
            raise
        finally:
            if result is not None:
                result.finished_at = dt_util.utcnow()

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
        await self.backend.async_close()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.raw.clear()
        self.control_results.clear()
        await self.async_shutdown()
