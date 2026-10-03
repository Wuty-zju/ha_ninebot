"""Account polling with per-vehicle, per-group freshness and partial failure."""

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import adapters
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
from .exceptions import NinebotAuthError, NinebotError
from .models import Freshness, VehicleSnapshot
from .raw import Endpoint, RawLimitError, RawStore, build_record
from .storage import ModelStorage

LOGGER = logging.getLogger(__name__)
type Group = Literal["status", "battery", "travel"]


class NinebotCoordinator(DataUpdateCoordinator[dict[str, VehicleSnapshot]]):
    """All update paths share a mutex; no one-car failure discards other cars."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: NinecliClient,
        *,
        models: ModelStorage | None = None,
    ) -> None:
        self.client = client
        self.raw = RawStore()
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
        self._active: set[asyncio.Task[Any]] = set()
        self._forced: dict[str, asyncio.Task[None]] = {}
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
            return self._list_freshness.valid(now, 3 * VEHICLE_INTERVAL)
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
            raw = await self.client.async_list_vehicles()
            await self._capture(Endpoint.VEHICLES, raw)
            found = adapters.profiles(raw)
        except NinebotAuthError:
            raise
        except NinebotError as err:
            self._list_freshness = replace(self._list_freshness, attempted_at=now, error=err.kind)
            self._attempt_finished("", "profile", stamp, VEHICLE_INTERVAL, False)
            if self._list_freshness.succeeded_at is None:
                raise UpdateFailed(err.kind.value) from err
            return
        sns = {profile.sn for profile in found}
        self.data = {
            sn: snapshot if sn in sns else replace(snapshot, present=False)
            for sn, snapshot in self.data.items()
        }
        for profile in found:
            old = self.data.get(profile.sn)
            self.data[profile.sn] = (
                replace(old, profile=profile) if old and old.present else VehicleSnapshot(profile)
            )
            if old and not old.present:
                # Reappearing ownership is a new observation interval. Do not
                # revive cached readings or bridge energy across the absence.
                for group in ("status", "battery", "travel"):
                    self._next_attempt.pop((profile.sn, group), None)
                    self._failures.pop((profile.sn, group), None)
                if self.models:
                    self.models.model(profile.sn).reset_baseline()
        finished = dt_util.utcnow()
        self._list_freshness = Freshness(now, finished)
        self._authenticated = True
        self._attempt_finished("", "profile", finished.timestamp(), VEHICLE_INTERVAL, True)

    async def _group(self, sn: str, group: Group, *, force: bool = False) -> None:
        now = dt_util.utcnow()
        stamp = now.timestamp()
        if not force and not self._due(sn, group, stamp):
            return
        snapshot = self.data[sn]
        success = False
        try:
            if group == "status":
                raw = await self.client.async_get_status(sn)
                await self._capture(Endpoint.STATUS, raw, sn)
                status = adapters.status(raw)
                updated = replace(
                    snapshot, status=status, status_freshness=Freshness(now, dt_util.utcnow())
                )
            elif group == "battery":
                raw = await self.client.async_get_battery(sn)
                await self._capture(Endpoint.BATTERY, raw, sn)
                battery = adapters.batteries(raw)
                updated = replace(
                    snapshot, battery=battery, battery_freshness=Freshness(now, dt_util.utcnow())
                )
            else:
                month = adapters.month_at(now)
                raw = await self.client.async_get_travel(sn, month)
                await self._capture(Endpoint.TRAVEL, raw, sn, month)
                travel = adapters.travel(raw, month)
                if travel.last_ride is None:
                    previous = adapters.previous_month(month)
                    try:
                        raw = await self.client.async_get_travel(sn, previous)
                        await self._capture(Endpoint.TRAVEL, raw, sn, previous)
                        fallback = adapters.travel(raw, previous)
                        travel = replace(travel, last_ride=fallback.last_ride)
                    except NinebotAuthError:
                        raise
                    except NinebotError:
                        # Optional last-ride fallback cannot invalidate current totals.
                        pass
                updated = replace(
                    snapshot, travel=travel, travel_freshness=Freshness(now, dt_util.utcnow())
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
            dt_util.utcnow().timestamp(),
            self.interval if group == "status" else DETAIL_INTERVAL,
            success,
        )

    async def _capture(
        self, endpoint: Endpoint, payload: object, sn: str = "", month: str | None = None
    ) -> None:
        """Raw policy failure cannot discard otherwise valid normalized data."""
        try:
            record = await self.hass.async_add_executor_job(
                build_record, endpoint, payload, dt_util.utcnow(), month
            )
        except RawLimitError:
            self.raw.rejected += 1
            return
        if not self._stopping:
            self.raw.put(record, sn, month or "")

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
        batteries = ",".join(
            sorted(b.key if b.identified else "unidentified" for b in snapshot.battery.batteries)
        )
        model.sample(snapshot.status.battery, now, f"vehicle_soc:{batteries}")
        self.models.schedule_save()

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
                # Status has priority, but every cycle also gives due details a turn.
                for sn in sns:
                    await self._group(sn, "status")
                for sn in sns:
                    await self._group(sn, "battery")
                    await self._group(sn, "travel")
                    self._sample_model(sn)
                return dict(self.data)
        except NinebotAuthError as err:
            self._authenticated = False
            raise ConfigEntryAuthFailed("auth") from err
        finally:
            if task:
                self._active.discard(task)

    async def async_refresh_vehicle(self, sn: str) -> None:
        """Coalesce simultaneous manual requests, force exactly this status."""
        if self._stopping:
            return
        if existing := self._forced.get(sn):
            await asyncio.shield(existing)
            return

        async def refresh() -> None:
            async with self._mutex:
                if sn not in self.data or not self.data[sn].present:
                    return
                try:
                    await self._group(sn, "status", force=True)
                except NinebotAuthError as err:
                    raise self._manual_auth_failure() from err
                self._sample_model(sn)
                self.async_set_updated_data(dict(self.data))
                self._schedule_validity_check()

        task = asyncio.create_task(refresh())
        self._forced[sn] = task
        try:
            await task
        finally:
            self._forced.pop(sn, None)

    def controls_enabled(self, sn: str, action: str | None = None) -> bool:
        """User consent AND fresh proven support/permission/semantics."""
        snapshot = self.data.get(sn)
        return bool(
            self.config_entry
            and self.config_entry.options.get(CONF_CONTROLS)
            and sn in self.config_entry.options.get(CONF_CONTROL_VEHICLES, [])
            and snapshot
            and self.fresh(sn, "profile")
            and self.fresh(sn, "status")
            and snapshot.status_freshness.error is None
            and self._list_freshness.error is None
            and any(
                snapshot.status.capabilities.allows(candidate)
                for candidate in (
                    (action,) if action else ("bell", "buck", "engine/start", "engine/stop")
                )
            )
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
        """Experimental controls need explicit consent and a present vehicle."""
        if (
            self._stopping
            or not self.config_entry
            or not self.controls_enabled(sn, action)
            or sn not in self.data
            or not self.data[sn].present
        ):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="controls_disabled")
        async with self._mutex:
            # Permission/freshness may change while a request waits in the queue.
            if not self.controls_enabled(sn, action):
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="controls_disabled"
                )
            try:
                await self.client.async_control(sn, action)
            except NinebotAuthError:
                self._manual_auth_failure()
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="control_uncertain"
                ) from None
            except NinebotError as err:
                # No automatic retry, even if the action's outcome is uncertain.
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="control_uncertain"
                ) from err
        try:
            await self.async_refresh_vehicle(sn)
            error = self.data[sn].status_freshness.error
            if error is not None:
                raise NinebotError(error)
        except (ConfigEntryAuthFailed, NinebotError):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="control_readback_failed"
            ) from None

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
        await self.client.async_close()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.raw.clear()
        await self.async_shutdown()
