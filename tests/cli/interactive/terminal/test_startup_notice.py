from __future__ import annotations

import asyncio
import threading

import pytest

from openminion.cli.presentation.models import MessageKind
from openminion.cli.interactive.terminal.shell import _schedule_startup_notice


class _TranscriptDouble:
    def __init__(self) -> None:
        self.messages = []

    def push_message(self, message) -> None:
        self.messages.append(message)


@pytest.mark.asyncio
async def test_startup_notice_runs_without_blocking_prompt_loop() -> None:
    release = threading.Event()
    started = threading.Event()
    transcript = _TranscriptDouble()

    def _slow_notice() -> str:
        started.set()
        release.wait(timeout=1)
        return "Update available"

    task = _schedule_startup_notice(_slow_notice, transcript=transcript)
    assert task is not None

    for _ in range(50):
        if started.is_set():
            break
        await asyncio.sleep(0.01)

    assert started.is_set()
    assert transcript.messages == []
    assert not task.done()

    release.set()
    await task

    assert len(transcript.messages) == 1
    assert transcript.messages[0].kind == MessageKind.SYSTEM
    assert transcript.messages[0].body == "Update available"


@pytest.mark.asyncio
async def test_empty_startup_notice_does_not_render() -> None:
    transcript = _TranscriptDouble()
    task = _schedule_startup_notice(lambda: "", transcript=transcript)
    assert task is not None

    await task

    assert transcript.messages == []


@pytest.mark.asyncio
async def test_missing_startup_notice_does_not_schedule_task() -> None:
    transcript = _TranscriptDouble()

    assert _schedule_startup_notice(None, transcript=transcript) is None
