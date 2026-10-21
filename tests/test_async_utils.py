"""Owned work finishes before cancellation, using the shared HA test loop."""

import asyncio

import pytest

from custom_components.ninebot.async_utils import async_drain


@pytest.mark.parametrize("fail", [False, True])
async def test_repeated_cancellation_does_not_cancel_owned_work(fail):
    entered, release = asyncio.Event(), asyncio.Event()

    async def work():
        entered.set()
        await release.wait()
        if fail:
            raise OSError("synthetic failure")
        return 42

    owned = asyncio.create_task(work())
    caller = asyncio.create_task(async_drain(owned))
    await entered.wait()
    try:
        for _ in range(3):
            caller.cancel()
            await asyncio.sleep(0)
            assert not caller.done()
            assert not owned.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
    assert owned.done()
    assert not owned.cancelled()


async def test_success_failure_and_owned_cancellation_preserve_their_results():
    async def work():
        return 42

    assert await async_drain(asyncio.create_task(work())) == 42
    future = asyncio.get_running_loop().create_future()
    future.set_exception(ValueError("synthetic"))
    with pytest.raises(ValueError):
        await async_drain(future)
    future = asyncio.get_running_loop().create_future()
    future.cancel()
    with pytest.raises(asyncio.CancelledError):
        await async_drain(future)
