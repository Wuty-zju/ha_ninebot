"""Opt-in durable known-ID preservation; no implicit full-history scan."""

import asyncio
import hashlib
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .archive_codec import RIDE_VALUES, ride_data
from .exceptions import NinebotError
from .query_broker import Priority
from .raw import Endpoint
from .ride_archive import ArchiveError
from .ride_models import Ride

if TYPE_CHECKING:
    from .coordinator import NinebotCoordinator

CONF_PRESERVE = "preserve_history"
CONF_BUDGET = "history_requests_per_hour"
DEFAULT_BUDGET = 30


def fingerprint(ride: Ride) -> str:
    raw = ride_data(ride)
    content = {key: raw[key] for key in ("detail_id", *RIDE_VALUES)}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class PreservationTask:
    ride: Ride
    fingerprint: str
    attempts: int
    observations: int


class HistoryPreservation:
    """One bounded background operation at a time, independent of entities."""

    def __init__(self, co: "NinebotCoordinator") -> None:
        self.co = co
        self._remove: Callable[[], None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._vehicle = 0
        self.status = "disabled"

    def start(self) -> None:
        if self.co.config_entry.options.get(CONF_PRESERVE, False) is not True:
            return
        self.status = "idle"
        self._remove = async_track_time_interval(
            self.co.hass, self.async_tick, timedelta(seconds=60)
        )

    async def async_tick(self, now: datetime) -> None:
        if self._task is not None or self.co._stopping:
            return
        self._task = asyncio.current_task()
        try:
            await self._step(now)
        except (ArchiveError, HomeAssistantError, NinebotError):
            self.status = "waiting"  # Ordinary failures do not generate repeated logs.
        finally:
            self._task = None

    async def _step(self, now: datetime) -> None:
        co, archive = self.co, self.co.statistics.archive
        if not co.statistics.writable:
            self.status = "storage_paused"
            return
        vehicles = sorted(sn for sn in co.data if co.local_vehicle_available(sn))
        if not vehicles:
            return
        budget = co.config_entry.options.get(CONF_BUDGET, DEFAULT_BUDGET)
        if type(budget) is not int or not 1 <= budget <= 60:
            budget = DEFAULT_BUDGET
        generation = co._generation
        for index in range(len(vehicles)):
            sn = vehicles[(self._vehicle + index) % len(vehicles)]
            if not co.fresh(sn, "profile"):
                continue
            ownership = co._ownership.get(sn, 0)

            def owns(vehicle: str = sn, epoch: int = ownership) -> bool:
                return (
                    not co._stopping
                    and co._generation == generation
                    and co._ownership.get(vehicle, 0) == epoch
                    and co.local_vehicle_available(vehicle)
                )

            task = await archive.async_preservation_claim(sn, now, budget, guard=owns)
            if task is None:
                continue
            self._vehicle = (self._vehicle + index + 1) % len(vehicles)
            self.status = "querying"
            try:
                # Missing detail can reuse an unpersisted response after a storage interruption.
                cached = co.raw.get(
                    Endpoint.TRIP_DETAIL,
                    sn,
                    task.ride.detail_id or "",
                    now=now,
                    query_month=task.ride.query_month,
                )
                existing = await archive.async_private_detail(
                    sn, task.ride.ride_id or "", guard=owns
                )
                record = (
                    cached
                    if cached is not None and existing is None
                    else await co.async_query_detail(
                        sn,
                        task.ride.detail_id or "",
                        task.ride.query_month,
                        refresh=True,
                        priority=Priority.BACKGROUND,
                    )
                )
                from .services import detail_data

                ride = await co.hass.async_add_executor_job(detail_data, record, task.ride, 500)
                if not owns():
                    return
                await co.statistics.async_record_detail(
                    sn, ride, record.received_at, owns, raw=record
                )
                if not co.statistics.writable:
                    raise HomeAssistantError("Archive write paused")
                stored = await archive.async_private_detail(sn, task.ride.ride_id or "", guard=owns)
                if stored is None:
                    raise HomeAssistantError("Detail not retained")
                ended = ride.ended_at
                mutable = ended is not None and (now - ended).total_seconds() < 86400
                again = mutable and stored.latest.payload().get("is_show_simple_point") is not True
                await archive.async_preservation_finish(
                    sn,
                    task,
                    dt_util.utcnow(),
                    success=True,
                    revisit=again,
                    guard=owns,
                )
                self.status = "idle"
            except (NinebotError, HomeAssistantError, ArchiveError) as err:
                if owns():
                    retry = err.retry_after if isinstance(err, NinebotError) else None
                    reason = err.kind.value if isinstance(err, NinebotError) else "unavailable"
                    if reason not in {
                        "connection",
                        "service",
                        "protocol",
                        "busy",
                        "auth",
                        "closed",
                    }:
                        reason = "unavailable"
                    await archive.async_preservation_finish(
                        sn,
                        task,
                        dt_util.utcnow(),
                        success=False,
                        retry_after=retry,
                        reason=reason,
                        guard=owns,
                    )
                self.status = "waiting"
            return  # At most one logical endpoint operation per tick/account budget.
        self.status = "idle"

    async def async_close(self) -> None:
        if self._remove is not None:
            self._remove()
            self._remove = None
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


def task_state(value: str) -> dict[str, Any]:
    state = json.loads(value)
    if not isinstance(state, dict) or set(state) != {
        "fingerprint",
        "attempts",
        "observations",
        "next_attempt",
        "reason",
        "revision",
    }:
        raise ValueError("Invalid preservation state")
    if (
        not isinstance(state["fingerprint"], str)
        or len(state["fingerprint"]) != 64
        or any(c not in "0123456789abcdef" for c in state["fingerprint"])
        or type(state["attempts"]) is not int
        or not 0 <= state["attempts"] <= 255
        or type(state["observations"]) is not int
        or not 0 <= state["observations"] <= 2
        or type(state["next_attempt"]) not in (int, float)
        or not 0 <= state["next_attempt"] <= 4102444800
        or type(state["revision"]) is not int
        or state["revision"] < 1
        or state["reason"]
        not in (None, "connection", "service", "protocol", "busy", "auth", "closed", "unavailable")
    ):
        raise ValueError("Invalid preservation state values")
    return state


def retry_delay(attempts: int, retry_after: float | None) -> float:
    delay = min(86400, 60 * 2 ** min(attempts, 10)) * random.uniform(0.9, 1.1)
    return max(delay, retry_after or 0)
