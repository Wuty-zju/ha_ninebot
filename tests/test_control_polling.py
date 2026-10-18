"""Control schedules on an injected clock, without real vehicle calls or sleeps."""

import asyncio

from custom_components.ninebot.control_polling import read_slots
from custom_components.ninebot.coordinator import CONFIRMATION_DELAYS, CONFIRMATION_TIMEOUT


async def test_complete_schedule_reaches_late_window_with_bounded_reads():
    clock = [100.0]
    waits = []

    async def advance(delay):
        waits.append(delay)
        clock[0] += delay
        await asyncio.sleep(0)

    slots = [
        offset
        async for offset in read_slots(
            100.0, CONFIRMATION_DELAYS, now=lambda: clock[0], sleep=advance
        )
    ]
    assert slots == [0, 1, 2, 3, 4, 6, 8, 9.9]
    assert CONFIRMATION_TIMEOUT == 10 and len(slots) == 8
    assert 9.8 < sum(waits) < CONFIRMATION_TIMEOUT


async def test_slow_reads_skip_expired_slots_without_catch_up_burst():
    clock = [0.0]
    issued = []

    async def advance(delay):
        clock[0] += delay

    async for offset in read_slots(0, CONFIRMATION_DELAYS, now=lambda: clock[0], sleep=advance):
        issued.append(offset)
        clock[0] += 2.5  # A slow response consumes more than two early slots.
    assert issued == [0, 3, 6, 9.9]
