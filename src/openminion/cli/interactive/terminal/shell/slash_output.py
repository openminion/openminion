from __future__ import annotations

from collections.abc import Awaitable, Callable
import io
import shlex
from typing import Any

from rich.console import Console
from rich.text import Text

from openminion.cli.presentation import copy_to_clipboard
from openminion.cli.presentation.models import ChatMessage, MessageKind
from openminion.cli.presentation.markers import token_rich_style
from openminion.cli.presentation.styles import StyleToken
from openminion.cli.status.models import (
    build_memory_context_review,
    render_memory_context_review,
)
from openminion.cli.presentation.telemetry import (
    render_telemetry_slash,
    render_trace_slash,
)
from ..overlays import TerminalOverlayPresenter
from ..status_line import TerminalStatusLine
from ..transcript import TerminalTranscript

_MUTED_ITALIC_STYLE = f"italic {token_rich_style(StyleToken.MUTED)}"

PROMPT_SAFE_OUTPUT_SLASHES = frozenset(
    """
    / /agents /browser /compact /context /context-review /copy /cost /delegate /details /editor /effort
    /export /goal /graph /help /mcp /memory /model /normal /permissions /queue /quiet
    /readonly /new /overview /resume /review /sessions /skills /status /statusline /tasks
    /telemetry /theme /tokens /tools /trace /undo /verbose
    """.split()
)


def render_context_review(runtime: Any, args: str) -> str:
    options = {
        "session_id": str(getattr(runtime, "session_id", "") or ""),
        "canary": "",
        "calibration": "",
        "artifacts_dir": "",
    }
    try:
        tokens = shlex.split(args)
    except ValueError:
        tokens = ()
    for token in tokens:
        key, separator, value = token.partition("=")
        if not separator:
            continue
        if key in {"session", "session_id"}:
            options["session_id"] = value
        elif key in {"canary", "calibration"}:
            options[key] = value
        elif key in {"artifacts", "artifacts_dir"}:
            options["artifacts_dir"] = value

    payload = runtime.context_trace_payload(session_id=options["session_id"])
    return render_memory_context_review(
        build_memory_context_review(
            payload,
            canary_path=options["canary"],
            calibration_path=options["calibration"],
            artifacts_dir=options["artifacts_dir"],
        )
    )


def copy_latest_message(transcript: TerminalTranscript, console: Console) -> None:
    body = transcript.copy_last_copyable_message()
    if not body:
        console.print(Text("(no message to copy)", style=_MUTED_ITALIC_STYLE))
        return
    message = (
        "(copied last message to clipboard)"
        if copy_to_clipboard(body)
        else "(no clipboard tool available)"
    )
    console.print(Text(message, style=_MUTED_ITALIC_STYLE))


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
    if cmd == "/tokens":
        report = runtime.token_usage_report().strip()
        console.print(report or "(no durable token usage data)")
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
