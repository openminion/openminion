from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import os
import sys
from typing import Any

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - POSIX-only terminal interrupt support.
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

from rich.console import Console
from rich.text import Text

from openminion.cli.presentation.markers import token_rich_style
from openminion.cli.presentation.models import ChatMessage, MessageKind
from openminion.cli.presentation.styles import StyleToken

from ..overlays import TerminalOverlayPresenter

_ESCAPE_BYTE = b"\x1b"


@dataclass(frozen=True)
class EscapeInterruptWatcher:
    stop: Callable[[], None]
    interrupted: Callable[[], bool]


async def confirm_terminal_exit(
    *, console: Console, overlay: TerminalOverlayPresenter
) -> bool:
    should_exit = await overlay.present_confirm_async("Exit the interactive CLI?")
    if not should_exit:
        console.print(
            Text(
                "(exit cancelled)",
                style=token_rich_style(StyleToken.MUTED, italic=True),
            )
        )
    return should_exit


def start_escape_interrupt_watcher(
    turn_task: asyncio.Task[Any],
    *,
    stdin: Any = None,
) -> EscapeInterruptWatcher | None:
    stream = stdin if stdin is not None else sys.stdin
    isatty = getattr(stream, "isatty", None)
    fileno = getattr(stream, "fileno", None)
    if termios is None or tty is None or not callable(isatty) or not isatty():
        return None
    if not callable(fileno):
        return None
    try:
        fd = int(fileno())
        previous_attrs = termios.tcgetattr(fd)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None

    interrupted = False
    loop = asyncio.get_running_loop()

    def _restore_terminal() -> None:
        try:
            loop.remove_reader(fd)
        except (OSError, RuntimeError, ValueError):
            pass
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, previous_attrs)
        except (OSError, RuntimeError, TypeError, ValueError):
            pass

    def _read_keypress() -> None:
        nonlocal interrupted
        try:
            data = os.read(fd, 1)
        except BlockingIOError:
            return
        except OSError:
            _restore_terminal()
            return
        if data == _ESCAPE_BYTE and not turn_task.done():
            interrupted = True
            turn_task.cancel()

    try:
        tty.setcbreak(fd)
        loop.add_reader(fd, _read_keypress)
    except (OSError, RuntimeError, ValueError, NotImplementedError):
        _restore_terminal()
        return None

    return EscapeInterruptWatcher(
        stop=_restore_terminal,
        interrupted=lambda: interrupted,
    )


async def run_interruptible_agent_turn(
    *,
    turn_runner: Callable[..., Coroutine[Any, Any, Any]],
    watcher_factory: Callable[[asyncio.Task[Any]], EscapeInterruptWatcher | None],
    text: str,
    runtime: Any,
    transcript: Any,
    status_line: Any,
    approval_callback: Callable[..., Any] | None = None,
    invalidate_prompt: Callable[[], None] | None = None,
) -> None:
    turn_task: asyncio.Task[Any] = asyncio.create_task(
        turn_runner(
            text=text,
            runtime=runtime,
            transcript=transcript,
            status_line=status_line,
            approval_callback=approval_callback,
            invalidate_prompt=invalidate_prompt,
        )
    )
    watcher = watcher_factory(turn_task)
    try:
        await turn_task
    except asyncio.CancelledError:
        if watcher is not None and watcher.interrupted():
            transcript.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body="Interrupted current turn.",
                )
            )
            return
        raise
    finally:
        if watcher is not None:
            watcher.stop()


__all__ = [
    "EscapeInterruptWatcher",
    "confirm_terminal_exit",
    "run_interruptible_agent_turn",
    "start_escape_interrupt_watcher",
]
