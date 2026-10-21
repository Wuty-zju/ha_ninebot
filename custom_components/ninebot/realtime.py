"""One bounded deadline dispatcher; slow groups cannot hold the account timer."""

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .demand import Group
from .exceptions import NinebotAuthError
from .ride_archive import ArchiveError

if TYPE_CHECKING:
    from .coordinator import NinebotCoordinator

LOGGER = logging.getLogger(__name__)
MAX_DISPATCHED = 8


class RealtimePolling:
    def __init__(self, co: "NinebotCoordinator") -> None:
        self.co = co
        self.running = False
        self.cancel: Callable[[], None] | None = None
        self.task: asyncio.Task[None] | None = None
        self.jobs: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._status_streak = 0
        self._unexpected: set[type[Exception]] = set()

    @callback
    def start(self) -> None:
        self.running = True
        self.schedule()

    @callback
    def stop(self) -> None:
        self.running = False
        if self.cancel:
            self.cancel()
            self.cancel = None

    def _busy(self, sn: str, group: str) -> bool:
        return (
            (sn, group) in self.jobs
            or (group == "profile" and bool(self.co._discovery))
            or any(key.vehicle == sn and key.endpoint == group for key in self.co._groups)
        )

    def _deadlines(self) -> list[tuple[float, str, str]]:
        co, now = self.co, self.co.monotonic()
        keys = [("", "profile")]
        for sn in co.data:
            if not co.status_pollable(sn):
                continue
            keys.extend(
                (sn, group)
                for group in co.demand(sn).groups
                if group == "status" or (co.background_ready(sn) and sn not in co.pending_controls)
            )
        deadlines = []
        for sn, group in keys:
            if self._busy(sn, group):
                continue
            deadline = co.group_deadline(sn, group, now)
            if deadline > now and co._due(sn, group, now):
                deadline = now  # Business-day rollover, without bypassing backoff.
            deadlines.append((deadline, sn, group))
        return deadlines

    @callback
    def schedule(self) -> None:
        if self.cancel:
            self.cancel()
            self.cancel = None
        co = self.co
        if (
            not self.running
            or co._stopping
            or not co._authenticated
            or self.task
            or len(self.jobs) >= MAX_DISPATCHED
        ):
            return
        deadlines = self._deadlines()
        if deadlines:
            delay = max(
                1,
                min(row[0] for row in deadlines) - co.monotonic(),
                co.broker.retry_delay,
                co.periodic_delay(),
            )
            self.cancel = async_call_later(co.hass, delay, self._wake)

    @callback
    def _wake(self, _now: object) -> None:
        # A manual/early wake must consume the scheduled handle too.
        if self.cancel:
            self.cancel()
            self.cancel = None
        if not self.running or self.co._stopping or not self.co._authenticated or self.task:
            return
        self.task = self.co.hass.async_create_task(self._run())
        self.co._active.add(self.task)
        self.task.add_done_callback(self.co._active.discard)

    @callback
    def dispatch(self) -> None:
        co = self.co
        if co._stopping or not co._authenticated or co.broker.cooling_down:
            return
        due = [row for row in self._deadlines() if row[0] <= co.monotonic()]
        while due and len(self.jobs) < MAX_DISPATCHED:
            if not co.admit_periodic():
                break
            status = [row for row in due if row[2] == "status"]
            other = [row for row in due if row[2] != "status"]
            pool = other if other and self._status_streak >= 3 else status or other
            row = min(pool, key=lambda item: (item[0], item[1], item[2]))
            due.remove(row)
            _, sn, group = row
            self._status_streak = self._status_streak + 1 if group == "status" else 0
            key = (sn, group)
            task = co.hass.async_create_task(self._execute(sn, group))
            self.jobs[key] = task
            co._active.add(task)
            task.add_done_callback(co._active.discard)
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        self.schedule()

    async def _run(self) -> None:
        try:
            self.dispatch()
        finally:
            self.task = None
            self.schedule()

    async def _execute(self, sn: str, group: str) -> None:
        co = self.co
        try:
            if co._stopping or not co._authenticated:
                return
            if group == "profile":
                await co._list(dt_util.utcnow())
            else:
                demand = co.demand(sn)
                if group not in demand.groups or (group != "status" and sn in co.pending_controls):
                    return
                await co._group(
                    sn,
                    cast(Group, group),
                    include_last_ride=demand.last_ride,
                    include_previous_month=demand.previous_month,
                )
        except NinebotAuthError:
            co._manual_auth_failure()
        except UpdateFailed:
            pass  # Discovery already retained its typed failure and retry deadline.
        except ArchiveError as err:
            co.statistics.record_error(err)
            co._attempt_finished(sn, group, co.monotonic(), co.poll_interval(sn, group), False)
        except Exception as err:
            if type(err) not in self._unexpected:
                self._unexpected.add(type(err))
                LOGGER.error("Polling task failed (%s)", type(err).__name__)
            co._attempt_finished(sn, group, co.monotonic(), co.poll_interval(sn, group), False)
        finally:
            self.jobs.pop((sn, group), None)
            if not co._stopping:
                if group == "profile":
                    co.async_update_listeners()
                co._schedule_validity_check()
            self.schedule()
