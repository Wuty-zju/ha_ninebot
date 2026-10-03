"""Account polling with per-vehicle, per-group freshness and partial failure."""

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Literal

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import adapters
from .client import NinecliClient
from .const import (
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
        self._next_attempt[key] = stamp + delay

    async def _list(self, now: datetime) -> None:
        stamp = now.timestamp()
        if not self._due("", "profile", stamp):
            return
        try:
            found = adapters.profiles(await self.client.async_list_vehicles())
        except NinebotAuthError:
            raise
        except NinebotError as err:
            self._list_freshness = replace(self._list_freshness, attempted_at=now, error=err.kind)
            self._attempt_finished("", "profile", stamp, VEHICLE_INTERVAL, False)
            if self._list_freshness.succeeded_at is None:
                raise UpdateFailed(err.kind.value) from err
            return
        sns = {profile.sn for profile in found}
        self.data = {sn: replace(snapshot, present=sn in sns) for sn, snapshot in self.data.items()}
        for profile in found:
            old = self.data.get(profile.sn)
            self.data[profile.sn] = (
                replace(old, profile=profile, present=True) if old else VehicleSnapshot(profile)
            )
        self._list_freshness = Freshness(now, now)
        self._authenticated = True
        self._attempt_finished("", "profile", stamp, VEHICLE_INTERVAL, True)

    async def _group(self, sn: str, group: Group, now: datetime, *, force: bool = False) -> None:
        stamp = now.timestamp()
        if not force and not self._due(sn, group, stamp):
            return
        snapshot = self.data[sn]
        success = False
        freshness = Freshness(now, now)
        try:
            if group == "status":
                status = adapters.status(await self.client.async_get_status(sn))
                updated = replace(snapshot, status=status, status_freshness=freshness)
                if (
                    self.models
                    and self.config_entry
                    and self.config_entry.options.get(CONF_ESTIMATION)
                ):
                    batteries = ",".join(
                        b.key if b.identified else "unidentified"
                        for b in snapshot.battery.batteries
                    )
                    self.models.model(sn).sample(status.battery, now, f"vehicle_soc:{batteries}")
                    self.models.schedule_save()
            elif group == "battery":
                battery = adapters.batteries(await self.client.async_get_battery(sn))
                updated = replace(snapshot, battery=battery, battery_freshness=freshness)
            else:
                month = adapters.month_at(now)
                travel = adapters.travel(await self.client.async_get_travel(sn, month), month)
                if travel.last_ride is None:
                    previous = adapters.previous_month(month)
                    try:
                        fallback = adapters.travel(
                            await self.client.async_get_travel(sn, previous), previous
                        )
                        travel = replace(travel, last_ride=fallback.last_ride)
                    except NinebotAuthError:
                        raise
                    except NinebotError:
                        # Optional last-ride fallback cannot invalidate current totals.
                        pass
                updated = replace(snapshot, travel=travel, travel_freshness=freshness)
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
            sn, group, stamp, self.interval if group == "status" else DETAIL_INTERVAL, success
        )

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
                    await self._group(sn, "status", now)
                for sn in sns:
                    await self._group(sn, "battery", now)
                    await self._group(sn, "travel", now)
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
                    await self._group(sn, "status", dt_util.utcnow(), force=True)
                except NinebotAuthError as err:
                    self._authenticated = False
                    raise ConfigEntryAuthFailed("auth") from err
                self.async_set_updated_data(dict(self.data))

        task = asyncio.create_task(refresh())
        self._forced[sn] = task
        try:
            await task
        finally:
            self._forced.pop(sn, None)

    def controls_enabled(self, sn: str) -> bool:
        """Unknown upstream capability needs an explicit per-vehicle opt-in."""
        return bool(
            self.config_entry
            and self.config_entry.options.get(CONF_CONTROLS)
            and sn in self.config_entry.options.get(CONF_CONTROL_VEHICLES, [])
        )

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
            or not self.controls_enabled(sn)
            or sn not in self.data
            or not self.data[sn].present
        ):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="controls_disabled")
        async with self._mutex:
            try:
                await self.client.async_control(sn, action)
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
        tasks = self._active | set(self._forced.values())
        current = asyncio.current_task()
        for task in tasks:
            if task is not current:
                task.cancel()
        await self.client.async_close()
        await asyncio.gather(
            *(task for task in tasks if task is not current), return_exceptions=True
        )
        await self.async_shutdown()
