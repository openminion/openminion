from __future__ import annotations

from collections.abc import Awaitable, Callable
import io
from typing import Any

from rich.console import Console

from openminion.cli.presentation.models import ChatMessage, MessageKind
from ..overlays import TerminalOverlayPresenter
from ..status_line import TerminalStatusLine
from ..transcript import TerminalTranscript

PROMPT_SAFE_OUTPUT_SLASHES = frozenset(
    """
    / /agents /browser /compact /context /cost /delegate /details /editor /effort
    /export /goal /help /mcp /memory /model /normal /permissions /queue /quiet
    /readonly /new /resume /review /sessions /skills /status /statusline /tasks
    /theme /tools /undo /verbose
    """.split()
)


async def handle_prompt_safe_output_slash(
    text: str,
    *,
    slash_handler: Callable[..., Awaitable[bool]],
    runtime: Any,
    console: Console,
    transcript: TerminalTranscript,
    overlay: TerminalOverlayPresenter,
    status_line: TerminalStatusLine,
    working_dir: str,
    approval_callback: Callable[[str, dict[str, Any], Any], Any] | None = None,
) -> bool:
    buffer = io.StringIO()
    should_exit = await slash_handler(
        text,
        runtime=runtime,
        console=Console(
            file=buffer,
            force_terminal=False,
            color_system=None,
            width=console.width,
        ),
        transcript=transcript,
        overlay=overlay,
        status_line=status_line,
        working_dir=working_dir,
        approval_callback=approval_callback,
    )
    body = buffer.getvalue().rstrip()
    if body:
        transcript.push_message(
            ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=body)
        )
    return should_exit
