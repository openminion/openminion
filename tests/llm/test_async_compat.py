from __future__ import annotations

import asyncio

from openminion.modules.llm.runtime.sync import run_async_compat


async def _value_after_yield(value: int) -> int:
    await asyncio.sleep(0)
    return value


def test_run_async_compat_without_running_loop() -> None:
    assert run_async_compat(_value_after_yield(3)) == 3


def test_run_async_compat_from_running_loop_uses_worker_loop() -> None:
    async def _run() -> int:
        return run_async_compat(_value_after_yield(7))

    assert asyncio.run(_run()) == 7
