"""Durable discovery and detail preservation under one account history budget."""

import asyncio
import hashlib
import json
import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .archive_codec import RIDE_VALUES, ride_data
from .exceptions import NinebotError
from .history_budget import CONF_BUDGET as CONF_BUDGET
from .history_budget import DEFAULT_BUDGET as DEFAULT_BUDGET
from .history_budget import configured_budget
from .history_progress import HistoryProgress, project_progress
from .query_broker import Priority
from .raw import Endpoint
from .ride_archive import ArchiveError
from .ride_models import Ride

if TYPE_CHECKING:
    from .coordinator import NinebotCoordinator

CONF_PRESERVE = "preserve_history"


def mutable_detail(ride: Ride, now: datetime) -> bool:
    """Local follow-up policy, not an upstream finality claim."""
    anchor = ride.ended_at or ride.started_at
    return anchor is not None and (now - anchor).total_seconds() < 86400


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
        from .background_audit import BackgroundAudit
        from .background_reconstruction import BackgroundReconstruction
        from .bootstrap import HistoryBootstrap
        from .incremental_sync import IncrementalSync
        from .month_sync import SparseMonthSync

        self.co = co
        self.months = SparseMonthSync(co)
        self.bootstrap = HistoryBootstrap(co, on_query=self._async_query_phase)
        self.audit = BackgroundAudit(co)
        self.reconstruction = BackgroundReconstruction(co)
        self.incremental = IncrementalSync(co)
        self._remove: Callable[[], None] | None = None
        self._task: asyncio.Task[None] | None = None
        self.status = "disabled"
        self.progress: dict[str, HistoryProgress] = {}
        self._progress_lock = asyncio.Lock()
        self._active_query: tuple[str, str] | None = None

    def start(self) -> None:
        entry = self.co.config_entry
        if entry is None or entry.options.get(CONF_PRESERVE, False) is not True:
            return
        self.status = "idle"
        self._remove = async_track_time_interval(
            self.co.hass,
            self.async_tick,
            timedelta(seconds=3600 / configured_budget(entry.options)),
        )

    async def async_tick(self, now: datetime) -> None:
        if self._task is not None or self.co._stopping:
            return
        self._task = asyncio.current_task()
        try:
            await self._step(now)
        except ArchiveError as err:
            self.co.statistics.record_error(err)
            self.status = "waiting"
        except (HomeAssistantError, NinebotError):
            self.status = "waiting"  # Ordinary failures do not generate repeated logs.
        finally:
            try:
                await self.async_refresh_progress(tuple(self.co.data), max(now, dt_util.utcnow()))
                task = asyncio.current_task()
                if not self.co._stopping and (task is None or not task.cancelling()):
                    await self.reconstruction.async_step(now)
                    await self.audit.async_step(now)
            except ArchiveError as err:
                self.co.statistics.record_error(err)
            finally:
                self._task = None

    async def _async_query_phase(self, sn: str, phase: str | None) -> None:
        if phase is not None:
            self._active_query = (sn, phase)
        elif self._active_query is not None and self._active_query[0] == sn:
            self._active_query = None
        await self.async_refresh_progress((sn,), dt_util.utcnow())

    async def async_refresh_progress(self, vehicles: tuple[str, ...], now: datetime) -> None:
        """Publish changed cached views after committed work, not from entity properties."""
        async with self._progress_lock:
            await self._refresh_progress(vehicles, now)

    async def _refresh_progress(self, vehicles: tuple[str, ...], now: datetime) -> None:
        co = self.co
        if not co.statistics.archive_ready:
            return
        for sn in vehicles:
            owns = self.bootstrap.guard(sn, require_enabled=False, respect_pause=False)
            if not owns():
                self.progress.pop(sn, None)
                self.audit.views.pop(sn, None)
                continue
            self.audit.publish(sn, await co.statistics.archive.async_history_audit(sn), owns)
            evidence = await co.statistics.archive.async_history_evidence(sn, guard=owns)
            value = project_progress(
                evidence,
                now,
                active_phase=self._active_query[1]
                if self._active_query and self._active_query[0] == sn
                else None,
                enabled=self.bootstrap.enabled()
                or (
                    co.config_entry is not None
                    and co.config_entry.options.get(CONF_PRESERVE) is True
                    and evidence.requested
                ),
                paused=evidence.paused,
                authenticated=co._authenticated,
                authentication_required=isinstance(
                    getattr(co, "last_exception", None), ConfigEntryAuthFailed
                ),
                storage_error=co.statistics.error.value if co.statistics.error else None,
            )
            if owns() and self.progress.get(sn) != value:
                self.progress[sn] = value
                co.statistics._notify_change(sn, owns)

    async def _step(self, now: datetime) -> None:
        co, archive = self.co, self.co.statistics.archive
        if not co.statistics.writable:
            self.status = "storage_paused"
            return
        vehicles, turn = await archive.async_preservation_order(
            tuple(sn for sn in co.data if co.local_vehicle_available(sn))
        )
        if not vehicles:
            return
        entry = co.config_entry
        if entry is None:
            return
        budget = configured_budget(entry.options)
        await self.bootstrap.async_seed(vehicles, now)
        vehicles = tuple(sn for sn in vehicles if sn not in self.bootstrap.paused)
        await self.incremental.async_seed(vehicles, now)
        if entry.options.get(CONF_PRESERVE) is True:
            await self.months.async_seed_recent(vehicles, now)
        if turn == "month" and await self._resume_months(vehicles, budget):
            return
        vehicles, _ = await archive.async_preservation_order(vehicles, kind="detail")
        generation = co._generation
        for sn in vehicles:
            if not co.fresh(sn, "profile"):
                continue
            ownership = co._ownership.get(sn, 0)

            def owns(vehicle: str = sn, epoch: int = ownership) -> bool:
                return (
                    not co._stopping
                    and co._generation == generation
                    and co._ownership.get(vehicle, 0) == epoch
                    and co.local_vehicle_available(vehicle)
                    and vehicle not in self.bootstrap.paused
                )

            now = max(now, dt_util.utcnow())
            task = await archive.async_preservation_claim(sn, now, budget, guard=owns)
            if task is None:
                continue
            self.status = "querying"
            try:
                await self._async_query_phase(sn, "preserving")
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
                if stored is None or stored.latest is None:
                    raise HomeAssistantError("Detail not retained")
                again = (
                    mutable_detail(ride, now)
                    and stored.latest.payload().get("is_show_simple_point") is not True
                )
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
            finally:
                await self._async_query_phase(sn, None)
            return  # At most one logical endpoint operation per tick/account budget.
        if not await self._resume_months(vehicles, budget):
            self.status = "idle"

    async def _resume_months(self, vehicles: tuple[str, ...], budget: int) -> bool:
        """Alternate bootstrap discovery, sparse repairs and explicit range jobs."""
        vehicles, _ = await self.co.statistics.archive.async_preservation_order(
            vehicles, kind="month"
        )
        families = (self.bootstrap.async_step, self.incremental.async_step, self._legacy_month_step)
        turn = await self.co.statistics.archive.async_history_family_turn()
        for offset in range(3):
            index = (turn + offset) % 3
            if await families[index](vehicles, dt_util.utcnow(), budget):
                await self.co.statistics.archive.async_set_history_family_turn((index + 1) % 3)
                self.status = (
                    "discovering_history"
                    if index == 0
                    else "incremental_history"
                    if index == 1
                    else "syncing_months"
                )
                return True
        return False

    async def _legacy_month_step(
        self, vehicles: tuple[str, ...], now: datetime, budget: int
    ) -> bool:
        if await self.co.statistics.archive.async_month_turn() == "sparse":
            for sn in vehicles:
                if await self.months.async_step(sn, budget, dt_util.utcnow()):
                    self.status = "syncing_months"
                    return True
        if await self._resume_range(vehicles, budget):
            return True
        for sn in vehicles:
            if await self.months.async_step(sn, budget, dt_util.utcnow()):
                self.status = "syncing_months"
                return True
        return False

    async def _resume_range(self, vehicles: tuple[str, ...], budget: int) -> bool:
        """Resume a previously explicit range without creating another one."""
        from .ride_archive import vehicle_key

        co = self.co
        job = await co.statistics.archive.async_sync_job()
        if job is None or job.terminal:
            return False
        from .archive_codec import stamp

        if job.retry_at and dt_util.utcnow() < stamp(job.retry_at):
            return False
        for sn in vehicles:
            if vehicle_key(sn) == job.vehicle and co.fresh(sn, "profile"):
                self.status = "syncing_months"
                # Same HistorySync lock/checkpoint as the Action, one missing month at most.
                await co.history_sync.async_advance(sn, job.job_id, budget=budget, max_missing=1)
                return True
        return False

    async def async_close(self) -> None:
        if self._remove is not None:
            self._remove()
            self._remove = None
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self.months.async_close()


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
    if retry_after is not None and math.isfinite(retry_after) and retry_after >= 0:
        delay = max(delay, retry_after)
    return delay
