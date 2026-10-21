"""Owned tasks keep their resource until completion, including repeated cancellation."""

import asyncio
import unittest

from custom_components.ninebot.async_utils import async_drain


class DrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancellation_does_not_cancel_owned_work(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                entered, release = asyncio.Event(), asyncio.Event()

                async def work(entered=entered, release=release, fail=fail):
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
                        self.assertFalse(caller.done())
                        self.assertFalse(owned.done())
                finally:
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await caller
                self.assertTrue(owned.done())
                self.assertFalse(owned.cancelled())

    async def test_success_failure_and_owned_cancellation_preserve_their_results(self):
        async def work():
            return 42

        self.assertEqual(await async_drain(asyncio.create_task(work())), 42)
        future = asyncio.get_running_loop().create_future()
        future.set_exception(ValueError("synthetic"))
        with self.assertRaises(ValueError):
            await async_drain(future)
        future = asyncio.get_running_loop().create_future()
        future.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await async_drain(future)
