"""Enabled-only ride event subscriptions with durable-before-emit cursors."""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import NinebotCoordinator
from .ride_events import SEEN_LIMIT, InvalidRideCursor, RideCursor, discover_rides
from .ride_models import Ride

LOGGER = logging.getLogger(__name__)
MAX_EVENT_VEHICLES = 128
MAX_CURSOR_BYTES = 3 * 1024 * 1024


def read_cursor_file(path: Path, key: str) -> dict[str, Any] | None:
    """Preflight/acknowledge the actual file, not Store's in-memory write cache.

    HA Store.async_save logs some write failures without raising. A verified
    on-disk envelope is required before emitting an irreversible HA event.
    This also prevents auto-migration/renaming of an unsupported cursor file.
    """
    try:
        with path.open("rb") as stream:
            encoded = stream.read(MAX_CURSOR_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(encoded) > MAX_CURSOR_BYTES:
        raise InvalidRideCursor
    raw = json.loads(encoded)
    if (
        not isinstance(raw, dict)
        or type(raw.get("version")) is not int
        or raw["version"] != 1
        or type(raw.get("minor_version")) is not int
        or raw["minor_version"] != 1
        or raw.get("key") != key
        or not isinstance(raw.get("data"), dict)
    ):
        raise InvalidRideCursor
    return raw["data"]


class RideEventPipeline:
    """One event consumer per vehicle; event and cursor are not an HA transaction."""

    def __init__(self, hass: HomeAssistant, entry_id: str, co: NinebotCoordinator) -> None:
        self.hass = hass
        self.coordinator = co
        self._store = Store[dict[str, Any]](hass, 1, f"ninebot.{entry_id}.rides_v1", private=True)
        self._issue_id = f"ride_events_{entry_id}"
        self._cursors: dict[str, RideCursor] = {}
        self._callbacks: dict[str, Callable[[Ride, bool], None]] = {}
        self._processed: dict[str, datetime] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._loaded = False
        self._healthy = True
        self._closed = False
        self._unsub: Callable[[], None] | None = None

    @property
    def available(self) -> bool:
        return self._healthy and not self._closed

    def diagnostics(self) -> dict[str, bool | int]:
        """Counts and health only; never hashed IDs, ride timestamps or cursor data."""
        return {
            "available": self.available,
            "loaded": self._loaded,
            "subscriber_count": len(self._callbacks),
            "cursor_count": len(self._cursors),
            "seen_limit": SEEN_LIMIT,
            "vehicle_limit": MAX_EVENT_VEHICLES,
        }

    @callback
    def _storage_problem(self) -> None:
        self._healthy = False
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="ride_event_storage_invalid",
        )
        # Never log raw storage, paths, account or ride IDs.
        LOGGER.warning("Ride event storage unavailable; event emission is suspended")
        self.coordinator.async_update_listeners()

    async def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = await self.hass.async_add_executor_job(
                read_cursor_file, Path(self._store.path), self._store.key
            )
            if raw is None:
                return
            if (
                not isinstance(raw, dict)
                or type(raw.get("cursor_version")) is not int
                or raw["cursor_version"] != 1
                or not isinstance(raw.get("vehicles"), dict)
                or len(raw["vehicles"]) > MAX_EVENT_VEHICLES
            ):
                raise InvalidRideCursor
            for key, value in raw["vehicles"].items():
                if (
                    not isinstance(key, str)
                    or len(key) != 64
                    or any(char not in "0123456789abcdef" for char in key)
                ):
                    raise InvalidRideCursor
                self._cursors[key] = RideCursor.restore(value)
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        except (OSError, ValueError):
            self._storage_problem()

    @staticmethod
    def _vehicle_key(sn: str) -> str:
        return hashlib.sha256(sn.encode()).hexdigest()

    async def async_subscribe(
        self, sn: str, listener: Callable[[Ride, bool], None]
    ) -> Callable[[], None]:
        """Read storage only for an enabled entity; every subscription rebaselines."""
        async with self._lock:
            if sn in self._callbacks:
                raise ValueError("Duplicate ride event subscription")
            await self._load()
            if not self._closed:
                self._callbacks[sn] = listener
                await self._process_locked(sn, baseline=True)
        if self._unsub is None and self._callbacks:
            self._unsub = self.coordinator.async_add_listener(self._schedule)
        self._schedule()

        @callback
        def remove() -> None:
            self._callbacks.pop(sn, None)
            self._processed.pop(sn, None)
            if task := self._tasks.pop(sn, None):
                task.cancel()
            if not self._callbacks and self._unsub:
                self._unsub()
                self._unsub = None

        return remove

    @callback
    def _schedule(self) -> None:
        if not self.available:
            return
        for sn in self._callbacks:
            if sn not in self._tasks:
                self._tasks[sn] = self.hass.async_create_task(
                    self._process(sn), "Ninebot ride event observation", eager_start=False
                )

    async def _process(self, sn: str) -> None:
        try:
            async with self._lock:
                while await self._process_locked(sn):
                    pass  # Process a newer snapshot that arrived during a save.
        finally:
            if self._tasks.get(sn) is asyncio.current_task():
                self._tasks.pop(sn, None)

    async def _process_locked(self, sn: str, *, baseline: bool = False) -> bool:
        if not self.available or sn not in self._callbacks:
            return False
        snapshot = self.coordinator.data.get(sn)
        if (
            snapshot is None
            or snapshot.travel is None
            or not self.coordinator.fresh(sn, "travel")
            or snapshot.travel_freshness.error is not None
            or (received := snapshot.travel_freshness.succeeded_at) is None
            or (not baseline and self._processed.get(sn) == received)
        ):
            return False
        rides = snapshot.travel.rides
        # Month rollover fallback is a separate Ride source, never month totals.
        if last := snapshot.travel.last_ride:
            if last.ride is not None and last.ride.query_month != snapshot.travel.month:
                rides += (last.ride,)
        key = self._vehicle_key(sn)
        previous = self._cursors.get(key, RideCursor())
        now = dt_util.utcnow()
        result = discover_rides(previous, rides, now, baseline=baseline)
        if key not in self._cursors and len(self._cursors) >= MAX_EVENT_VEHICLES:
            # Evict an inactive vehicle cursor, never a current subscription.
            active = {self._vehicle_key(vehicle) for vehicle in self._callbacks}
            stale = next((stored for stored in self._cursors if stored not in active), None)
            if stale is None:
                self._storage_problem()
                return False
            self._cursors.pop(stale)
        candidate = {**self._cursors, key: result.cursor}
        committed = {
            "cursor_version": 1,
            "vehicles": {vehicle: cursor.dump() for vehicle, cursor in candidate.items()},
        }
        try:
            await self._store.async_save(committed)
            acknowledged = await self.hass.async_add_executor_job(
                read_cursor_file, Path(self._store.path), self._store.key
            )
            if acknowledged != committed:
                raise InvalidRideCursor
        except (OSError, ValueError):
            self._storage_problem()
            return False
        self._cursors = candidate
        self._processed[sn] = received
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        # Durable acknowledgement before EventEntity emission is at-most-once.
        # Cancellation/crash between the two may lose an event; no replay promise.
        if (
            self.available
            and sn in self._callbacks
            and self.coordinator.fresh(sn, "travel")
            and self.coordinator.fresh(sn, "profile")
        ):
            for ride in result.rides:
                late = bool(
                    previous.high_water_end
                    and ride.ended_at
                    and ride.ended_at < previous.high_water_end
                )
                self._callbacks[sn](ride, late)
        current = self.coordinator.data.get(sn)
        return bool(
            current
            and current.travel_freshness.succeeded_at != received
            and sn in self._callbacks
            and self.available
        )

    async def async_close(self) -> None:
        self._closed = True
        self._callbacks.clear()
        if self._unsub:
            self._unsub()
            self._unsub = None
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
