from __future__ import annotations

from collections.abc import Awaitable, Callable
import io
from typing import Any

from rich.console import Console

from openminion.cli.presentation.models import ChatMessage, MessageKind
from openminion.cli.presentation.telemetry import (
    render_telemetry_slash,
    render_trace_slash,
)
from ..overlays import TerminalOverlayPresenter
from ..status_line import TerminalStatusLine
from ..transcript import TerminalTranscript

PROMPT_SAFE_OUTPUT_SLASHES = frozenset(
    """
    / /agents /browser /compact /context /cost /delegate /details /editor /effort
    /export /goal /help /mcp /memory /model /normal /permissions /queue /quiet
    /readonly /new /resume /review /sessions /skills /status /statusline /tasks
    /telemetry /theme /tools /trace /undo /verbose
    """.split()
)


def handle_debug_output_slash(
    cmd: str,
    text: str,
    *,
    runtime: Any,
    console: Console,
    cost_renderer: Callable[..., None],
) -> bool:
    if cmd == "/cost":
        cost_renderer(runtime=runtime, console=console)
        return True
    if cmd == "/telemetry":
        renderer = render_telemetry_slash
    elif cmd == "/trace":
        renderer = render_trace_slash
    else:
        return False
    parts = text.split(maxsplit=1)
    console.print(renderer(parts[1] if len(parts) > 1 else "", runtime=runtime))
    return True


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
