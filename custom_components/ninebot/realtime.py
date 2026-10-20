"""One demand-driven status/recovery timer, independent of slow history rounds."""

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .exceptions import NinebotAuthError

if TYPE_CHECKING:
    from .coordinator import NinebotCoordinator


class RealtimePolling:
    def __init__(self, co: "NinebotCoordinator") -> None:
        self.co = co
        self.running = False
        self.cancel: Callable[[], None] | None = None
        self.task: asyncio.Task[None] | None = None

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

    @callback
    def schedule(self) -> None:
        if self.cancel:
            self.cancel()
            self.cancel = None
        co = self.co
        if not self.running or co._stopping or not co._authenticated or self.task:
            return
        now = dt_util.utcnow().timestamp()
        deadlines = [
            co._next_attempt.get((sn, "status"), now)
            for sn in co.data
            if co.status_pollable(sn) and "status" in co.demand(sn).groups
        ]
        if co._list_freshness.error is not None:
            deadlines.append(co._next_attempt.get(("", "profile"), now))
        if deadlines:
            delay = max(1, min(deadlines) - now, co.broker.retry_delay)
            self.cancel = async_call_later(co.hass, delay, self._wake)

    @callback
    def _wake(self, _now: object) -> None:
        self.cancel = None
        if not self.running or self.co._stopping or not self.co._authenticated or self.task:
            return
        self.task = self.co.hass.async_create_task(self._run())
        self.co._active.add(self.task)
        self.task.add_done_callback(self.co._active.discard)

    async def _run(self) -> None:
        co = self.co
        try:
            # Priority applies to queued work. Never preempt an active native
            # token/cache transaction or replay a physical control command.
            await co.async_poll_status()
            if co._list_freshness.error is not None:
                await co._list(dt_util.utcnow())
                await co.async_poll_status()
        except NinebotAuthError:
            co._manual_auth_failure()
        except UpdateFailed:
            pass  # Discovery already retained its typed failure and retry deadline.
        finally:
            self.task = None
            if not co._stopping:
                co.async_update_listeners()
                co._schedule_validity_check()
            self.schedule()
