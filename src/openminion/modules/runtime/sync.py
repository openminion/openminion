"""Synchronous compatibility entrypoint for running coroutines."""

import asyncio
import concurrent.futures
from collections.abc import Awaitable, Coroutine
from contextvars import ContextVar, copy_context
from threading import Event
from time import monotonic
from typing import Any, TypeVar

_T = TypeVar("_T")


_turn_cancellations: ContextVar[tuple[Event, ...]] = ContextVar(
    "turn_cancellations", default=()
)

_turn_deadline: ContextVar[float | None] = ContextVar("turn_deadline", default=None)


def remaining_turn_timeout(timeout_s: float) -> float:
    """Limit blocking provider I/O to the inherited turn deadline."""
    raise_if_cancelled()
    deadline = _turn_deadline.get()
    if deadline is None:
        return timeout_s
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise asyncio.CancelledError("Turn deadline elapsed.")
    return min(timeout_s, remaining)


def is_turn_deadline_elapsed() -> bool:
    deadline = _turn_deadline.get()
    return deadline is not None and monotonic() >= deadline


def raise_if_cancelled() -> None:
    """Stop work inherited from a cancelled turn, including nested SDK calls."""
    if (
        any(event.is_set() for event in _turn_cancellations.get())
        or is_turn_deadline_elapsed()
    ):
        raise asyncio.CancelledError("Turn cancelled.")


async def _await_cancellable(
    awaitable: Awaitable[_T], cancel_event: Any | None = None
) -> _T:
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Turn cancelled.")
            raise_if_cancelled()
            if task.done():
                return task.result()
            await asyncio.wait({task}, timeout=0.05)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def await_with_cancel(
    awaitable: Awaitable[_T], *, timeout_s: float, cancel_event: Any | None = None
) -> _T:
    """Bound a turn and propagate its cancellation into synchronous workers."""
    stopped = Event()
    deadline = monotonic() + timeout_s
    parent_deadline = _turn_deadline.get()
    deadline_token = _turn_deadline.set(
        min(deadline, parent_deadline) if parent_deadline is not None else deadline
    )
    events = (stopped,) if cancel_event is None else (stopped, cancel_event)
    token = _turn_cancellations.set((*_turn_cancellations.get(), *events))
    try:
        async with asyncio.timeout(timeout_s):
            return await _await_cancellable(awaitable, cancel_event)
    except asyncio.CancelledError:
        if monotonic() >= deadline:
            raise TimeoutError("Turn deadline elapsed.") from None
        raise
    finally:
        stopped.set()
        _turn_cancellations.reset(token)
        _turn_deadline.reset(deadline_token)


def run_async_compat(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run a coroutine from either a synchronous or already-async caller."""
    if _turn_cancellations.get():
        coro = _await_cancellable(coro)
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
        return executor.submit(copy_context().run, run_in_new_loop).result()


__all__ = [
    "await_with_cancel",
    "is_turn_deadline_elapsed",
    "raise_if_cancelled",
    "remaining_turn_timeout",
    "run_async_compat",
]
