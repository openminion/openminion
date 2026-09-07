from __future__ import annotations

import asyncio
from collections.abc import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.shortcuts.prompt import is_dumb_terminal

from openminion.cli.presentation.models import ChatMessage, MessageKind
from openminion.cli.interactive.terminal.transcript import TerminalTranscript


async def emit_startup_notice(
    startup_notice: Callable[[], str],
    *,
    transcript: TerminalTranscript,
    prompt_session: PromptSession[str] | None = None,
) -> None:
    try:
        notice = await asyncio.to_thread(startup_notice)
    except (OSError, RuntimeError, TypeError, ValueError):
        return
    notice = str(notice or "").strip()
    if not notice:
        return
    if prompt_session is not None and not is_dumb_terminal():
        while not prompt_session.app.is_running:
            await asyncio.sleep(0)
    transcript.push_message(
        ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=notice)
    )


def schedule_startup_notice(
    startup_notice: Callable[[], str] | None,
    *,
    transcript: TerminalTranscript,
    prompt_session: PromptSession[str] | None = None,
) -> asyncio.Task[None] | None:
    if startup_notice is None:
        return None
    return asyncio.create_task(
        emit_startup_notice(
            startup_notice,
            transcript=transcript,
            prompt_session=prompt_session,
        )
    )


def cancel_startup_notice(task: asyncio.Task[None] | None) -> None:
    if task is not None and not task.done():
        task.cancel()


__all__ = [
    "cancel_startup_notice",
    "emit_startup_notice",
    "schedule_startup_notice",
]
