from __future__ import annotations

import asyncio
from collections.abc import Callable

from openminion.cli.presentation.models import ChatMessage, MessageKind
from openminion.cli.interactive.terminal.transcript import TerminalTranscript


async def emit_startup_notice(
    startup_notice: Callable[[], str],
    *,
    transcript: TerminalTranscript,
) -> None:
    try:
        notice = await asyncio.to_thread(startup_notice)
    except (OSError, RuntimeError, TypeError, ValueError):
        return
    notice = str(notice or "").strip()
    if not notice:
        return
    transcript.push_message(
        ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=notice)
    )


def schedule_startup_notice(
    startup_notice: Callable[[], str] | None,
    *,
    transcript: TerminalTranscript,
) -> asyncio.Task[None] | None:
    if startup_notice is None:
        return None
    return asyncio.create_task(
        emit_startup_notice(startup_notice, transcript=transcript)
    )


def cancel_startup_notice(task: asyncio.Task[None] | None) -> None:
    if task is not None and not task.done():
        task.cancel()


__all__ = [
    "cancel_startup_notice",
    "emit_startup_notice",
    "schedule_startup_notice",
]
