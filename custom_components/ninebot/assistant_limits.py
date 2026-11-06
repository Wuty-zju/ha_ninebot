"""Bounded rolling read admission; no queue, timer, cloud quota or durable state."""

import asyncio
import math
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager

from homeassistant.core import HomeAssistant

DATA_KEY = "ninebot_assistant_read_limits"
WINDOW_SECONDS = 60
MAX_REQUESTS = 20
MAX_IN_FLIGHT = 2
MAX_USERS = 128


class ReadLimitExceeded(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("read_limit")


class ReadLimits:
    """One API-wide concurrency bound and independent authenticated-user windows."""

    def __init__(self) -> None:
        self.active = 0
        self.calls: dict[str, deque[float]] = {}

    def acquire(self, user_id: str, now: float) -> None:
        cutoff = now - WINDOW_SECONDS
        for key in tuple(self.calls):
            queue = self.calls[key]
            while queue and queue[0] <= cutoff:
                queue.popleft()
            if not queue:
                del self.calls[key]
        if self.active >= MAX_IN_FLIGHT:
            raise ReadLimitExceeded(1)
        queue = self.calls.get(user_id)
        if queue is None:
            if len(self.calls) >= MAX_USERS:
                raise ReadLimitExceeded(1)
            queue = self.calls[user_id] = deque()
        if len(queue) >= MAX_REQUESTS:
            raise ReadLimitExceeded(max(1, math.ceil(queue[0] + WINDOW_SECONDS - now)))
        queue.append(now)
        self.active += 1

    def release(self) -> None:
        if self.active < 1:
            raise RuntimeError("Unbalanced read admission")
        self.active -= 1


@contextmanager
def read_slot(hass: HomeAssistant, user_id: str) -> Iterator[None]:
    limits: ReadLimits = hass.data.setdefault(DATA_KEY, ReadLimits())
    limits.acquire(user_id, asyncio.get_running_loop().time())
    try:
        yield
    finally:
        limits.release()
