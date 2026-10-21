"""Finish owned work before propagating cancellation of its caller."""

import asyncio


async def async_drain[T](future: asyncio.Future[T]) -> T:
    """Repeated cancellation must not release a still-running resource owner."""
    cancelled = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        if not future.cancelled():
            future.exception()
        raise asyncio.CancelledError
    return future.result()
