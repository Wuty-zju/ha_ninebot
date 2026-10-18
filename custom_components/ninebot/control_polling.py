"""Bounded control-read schedules, without overdue catch-up requests."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable


async def read_slots(
    started: float,
    offsets: tuple[float, ...],
    *,
    now: Callable[[], float] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[float]:
    """Yield due offsets; slow responses consume time rather than queue reads.

    The caller owns the absolute timeout and checks ownership/authentication on
    every yielded slot. Clock/sleep injection lets offline tests use real
    schedules without waiting ten seconds or patching the HA event loop.
    """
    clock = now or asyncio.get_running_loop().time
    for offset in offsets:
        due = started + offset
        if offset and due < clock():
            continue
        await sleep(max(0, due - clock()))
        yield offset
