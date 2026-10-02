from __future__ import annotations

import asyncio
import threading

import pytest

from openminion.cli.interactive.terminal.shell import _schedule_startup_notice


@pytest.mark.asyncio
async def test_startup_notice_runs_without_blocking_prompt_loop() -> None:
    release = threading.Event()
    started = threading.Event()

    def _slow_notice() -> str:
        started.set()
        release.wait(timeout=1)
        return "Update available"

    task = _schedule_startup_notice(_slow_notice)
    assert task is not None

    for _ in range(50):
        if started.is_set():
            break
        await asyncio.sleep(0.01)

    assert started.is_set()
    assert not task.done()

    release.set()
    assert await task == "Update available"


@pytest.mark.asyncio
async def test_empty_startup_notice_does_not_render() -> None:
    task = _schedule_startup_notice(lambda: "")
    assert task is not None

    assert await task == ""


@pytest.mark.asyncio
async def test_missing_startup_notice_does_not_schedule_task() -> None:
    assert _schedule_startup_notice(None) is None
