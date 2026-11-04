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
from openminion.cli.presentation.tokens import render_tokens_slash
from ..overlays import TerminalOverlayPresenter
from ..status_line import TerminalStatusLine
from ..transcript import TerminalTranscript


PROMPT_SAFE_OUTPUT_SLASHES = frozenset(
    """
    /activate /agents /identity /browser /close /compact /context /context-review /copy /cost
    /delegate /details /diff /editor /effort /expand /export /goal /graph /handoff /help /init
    /message
    /invite /kick /mcp /memory /model /normal /participants /permissions /project /queue
    /quiet /readonly /new /overview /rename /resume /review /room /routing /sessions /skills /start /status
    /statusline /tasks /telemetry /theme /tokens /tools /trace /undo /verbose
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
        tokens = []
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
    return str(
        render_memory_context_review(
            build_memory_context_review(
                payload,
                canary_path=options["canary"],
                calibration_path=options["calibration"],
                artifacts_dir=options["artifacts_dir"],
            )
        )
    )


def render_identity_report(payload: dict[str, Any]) -> str:
    render = payload.get("render")
    render_facts = render if isinstance(render, dict) else payload
    lines = [
        f"Identity: {payload.get('display_name') or payload.get('agent_id')}",
        f"Source: {payload.get('source', 'unknown')}",
        f"Profile revision: {payload.get('profile_revision', 0)}",
        f"Profile version: {payload.get('profile_version', '')}",
        f"Render version: {payload.get('render_version', '')}",
    ]
    if source_path := str(payload.get("source_path", "") or "").strip():
        lines.insert(2, f"Source path: {source_path}")
    if purpose := str(render_facts.get("purpose", "") or "").strip():
        lines.append(f"Purpose: {purpose}")
    budget = render_facts.get("budget")
    if isinstance(budget, dict):
        lines.append(
            f"Render budget: {budget.get('used_tokens', 0)}/{budget.get('max_tokens', 0)} tokens"
        )
    included_fields = render_facts.get("included_fields")
    if isinstance(included_fields, list):
        lines.append(f"Included fields: {', '.join(included_fields) or 'none'}")
    omitted_fields = render_facts.get("omitted_fields")
    if isinstance(omitted_fields, list):
        lines.append(f"Omitted fields: {', '.join(omitted_fields) or 'none'}")
    warnings = render_facts.get("warnings")
    if isinstance(warnings, list):
        lines.extend(
            f"Render warning: {warning}"
            for warning in warnings
            if isinstance(warning, str) and warning
        )
    verification = payload.get("verification")
    if isinstance(verification, dict):
        lines.extend(
            (
                f"Verification: {'ok' if verification.get('ok') else 'failed'}",
                f"Source sync: {verification.get('source_status', 'unknown')}",
                f"Sidecars: {verification.get('sidecar_status', 'unknown')}",
            )
        )
        issues = verification.get("issues")
        if isinstance(issues, list):
            lines.extend(
                f"Issue: {message}"
                for issue in issues
                if isinstance(issue, dict)
                and isinstance(message := issue.get("message"), str)
                and message.strip()
            )
    if isinstance(validation := payload.get("validation"), dict):
        lines.append(f"Validation: {'ok' if validation.get('ok') else 'failed'}")
        errors = validation.get("errors")
        if isinstance(errors, list):
            lines.extend(
                f"Validation error: {error}"
                for error in errors
                if isinstance(error, str) and error.strip()
            )
        warnings = validation.get("warnings")
        if isinstance(warnings, list):
            lines.extend(
                f"Validation warning: {warning}"
                for warning in warnings
                if isinstance(warning, str) and warning.strip()
            )
    if rendered := str(payload.get("rendered_text", "") or "").strip():
        lines.extend(("", rendered))
    return "\n".join(lines)


def copy_latest_message(transcript: TerminalTranscript, console: Console) -> None:
    body = transcript.copy_last_copyable_message()
    if not body:
        console.print(
            Text(
                "(no message to copy)",
                style=token_rich_style(StyleToken.MUTED, italic=True),
            )
        )
        return
    message = (
        "(copied last message to clipboard)"
        if copy_to_clipboard(body)
        else "(no clipboard tool available)"
    )
    console.print(Text(message, style=token_rich_style(StyleToken.MUTED, italic=True)))


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
        parts = text.split(maxsplit=1)
        console.print(
            render_tokens_slash(
                parts[1] if len(parts) > 1 else "",
                runtime=runtime,
            )
        )
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
    approval_callback: Callable[..., Any] | None = None,
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
