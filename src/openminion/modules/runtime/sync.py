"""Synchronous compatibility entrypoint for running coroutines."""

import asyncio
import concurrent.futures
from collections.abc import Coroutine
from typing import Any, TypeVar

_T = TypeVar("_T")


def run_async_compat(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run a coroutine from either a synchronous or already-async caller."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():

        def run_in_new_loop() -> _T:
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(run_in_new_loop).result()

    return asyncio.run(coro)


__all__ = ["run_async_compat"]
