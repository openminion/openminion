"""Synchronous compatibility entrypoint for running coroutines."""

import asyncio
import concurrent.futures
from collections.abc import Awaitable, Coroutine
from typing import Any, TypeVar

_T = TypeVar("_T")


async def await_with_cancel(
    awaitable: Awaitable[_T], *, timeout_s: float, cancel_event: Any | None = None
) -> _T:
    """Await work with a timeout and an optional thread-safe cancellation event."""
    if cancel_event is None:
        return await asyncio.wait_for(awaitable, timeout=timeout_s)
    task = asyncio.ensure_future(awaitable)
    try:
        async with asyncio.timeout(timeout_s):
            while not task.done():
                if cancel_event.is_set():
                    raise RuntimeError("Turn cancelled.")
                await asyncio.sleep(0.05)
            return task.result()
    finally:
        if not task.done():
            task.cancel()


def run_async_compat(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run a coroutine from either a synchronous or already-async caller."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    def run_in_new_loop() -> _T:
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(run_in_new_loop).result()


__all__ = ["await_with_cancel", "run_async_compat"]
