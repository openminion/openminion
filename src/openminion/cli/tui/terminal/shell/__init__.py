from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.text import Text

from openminion.cli.chat.ui import PhaseStatusDisplay
from openminion.cli.status import format_token_usage_summary
from openminion.cli.tui.presentation.models import (
    ChatMessage,
    MessageKind,
)

from ..composer import TerminalComposer
from ..overlays import TerminalOverlayPresenter
from ..status_line import TerminalStatusLine
from ..transcript import TerminalTranscript
from .labels import _runtime_label
from .actions import (
    _copy_to_clipboard,
    _handle_slash,
    _open_dashboard_side_trip,
    _push_greeter,
    _run_shell_escape,
    _runtime_permission_mode,
    _cycle_permission_mode,
    _SLASH_COMMANDS,
)
from .renderers import (
    _render_cost_snapshot,
    _render_mcp_status,
    _render_model_status,
    _render_sessions_list,
    _render_status_block,
    _render_tools_list,
)

from openminion.cli.presentation.styles import StyleToken
from openminion.cli.tui.presentation.markers import token_rich_style
from openminion.cli.tui.presentation.visible_parity import (
    statusline_label,
)

__all__ = [
    "run_terminal_focus",
    "_discover_custom_commands_for",
    "_focus_history_path",
    "_handle_slash_input",
    "_run_one_shot_stdin",
    "_run_agent_turn",
    "_route_durable_activity_event",
    "_build_turn_progress_callback",
    "_finalize_turn_status_line",
    "_copy_to_clipboard",
    "_handle_slash",
    "_open_dashboard_side_trip",
    "_push_greeter",
    "_run_shell_escape",
    "_runtime_permission_mode",
    "_cycle_permission_mode",
    "_SLASH_COMMANDS",
    "_render_cost_snapshot",
    "_render_mcp_status",
    "_render_model_status",
    "_render_sessions_list",
    "_render_status_block",
    "_render_tools_list",
]


_ERR_STYLE = token_rich_style(StyleToken.ERROR)
_INFO_STYLE = token_rich_style(StyleToken.INFO)
_INFO_BOLD_STYLE = token_rich_style(StyleToken.INFO, bold=True)
_MUTED_STYLE = token_rich_style(StyleToken.MUTED)
_MUTED_ITALIC_STYLE = f"italic {_MUTED_STYLE}" if _MUTED_STYLE else "italic"
_SYSTEM_STYLE = token_rich_style(StyleToken.SYSTEM)


def _discover_custom_commands_for(*, runtime: Any, working_dir: str) -> dict[str, Any]:
    """Scan project and user-global dirs for custom slash commands."""
    from openminion.cli.tui.presentation.custom_commands import (
        discover_custom_commands,
    )

    project_dir = (
        Path(working_dir) / ".openminion" / "commands" if working_dir else None
    )
    user_dir: Path | None = None
    data_root = getattr(getattr(runtime, "api_runtime", runtime), "data_root", None)
    if data_root is not None:
        try:
            user_dir = Path(str(data_root)) / "commands"
        except (OSError, RuntimeError, TypeError, ValueError):
            user_dir = None
    try:
        return discover_custom_commands(project_dir=project_dir, user_dir=user_dir)
    except (OSError, RuntimeError, ValueError):
        return {}


def _focus_history_path(runtime: Any) -> str | None:
    api_runtime = getattr(runtime, "api_runtime", None)
    data_root = getattr(api_runtime, "data_root", None)
    raw = str(data_root or "").strip()
    if not raw:
        return None
    history_dir = Path(raw).expanduser().resolve(strict=False) / "cli"
    history_dir.mkdir(parents=True, exist_ok=True)
    return str(history_dir / "terminal_history")


def run_terminal_focus(
    runtime: Any,
    *,
    working_dir: str | None = None,
    agent: str | None = None,
    session: str | None = None,
    plain_spinner: bool = False,
    verbosity: str = "normal",
) -> int:
    """Synchronous entry point. Wraps the async loop."""
    return asyncio.run(
        _run_terminal_focus_async(
            runtime,
            working_dir=working_dir or str(Path.cwd().resolve(strict=False)),
            agent=agent,
            session=session,
            plain_spinner=plain_spinner,
            verbosity=verbosity,
        )
    )


def _build_ctrl_key_handlers(
    *, transcript: TerminalTranscript, console: Console
) -> tuple:
    """Build the clear and copy keybinding handlers."""

    def _handle_ctrl_l() -> None:
        transcript.clear_messages()

    def _handle_ctrl_o() -> None:
        body = transcript.copy_last_copyable_message()
        if not body:
            console.print(Text("(no message to copy)", style=_MUTED_ITALIC_STYLE))
            return
        ok = _copy_to_clipboard(body)
        if ok:
            console.print(
                Text("(copied last message to clipboard)", style=_MUTED_ITALIC_STYLE)
            )
        else:
            console.print(
                Text(
                    "(no clipboard tool available — install pbcopy/xclip/wl-copy/clip.exe)",
                    style=_MUTED_ITALIC_STYLE,
                )
            )

    return _handle_ctrl_l, _handle_ctrl_o


async def _handle_slash_input(
    text: str,
    *,
    runtime: Any,
    console: Console,
    transcript: TerminalTranscript,
    overlay: TerminalOverlayPresenter,
    status_line: TerminalStatusLine,
    working_dir: str,
    custom_commands: dict,
) -> bool:
    """Dispatch a slash command and return whether the shell should exit."""

    parts = text.split(maxsplit=1)
    cmd_name = parts[0]
    slash_arg = parts[1] if len(parts) > 1 else ""

    if cmd_name in _SLASH_COMMANDS:
        return await _handle_slash(
            text,
            runtime=runtime,
            console=console,
            transcript=transcript,
            overlay=overlay,
            status_line=status_line,
            working_dir=working_dir,
        )
    if cmd_name in custom_commands:
        from openminion.cli.tui.presentation.custom_commands import render_command

        rendered = render_command(
            custom_commands[cmd_name],
            arg_string=slash_arg,
            working_dir=Path(working_dir) if working_dir else None,
        )
        transcript.push_message(
            ChatMessage(kind=MessageKind.USER, sender="you", body=rendered),
            render=False,
        )
        await _run_agent_turn(
            text=rendered,
            runtime=runtime,
            transcript=transcript,
            status_line=status_line,
        )
        return False
    return await _handle_slash(
        text,
        runtime=runtime,
        console=console,
        transcript=transcript,
        overlay=overlay,
        status_line=status_line,
        working_dir=working_dir,
    )


async def _run_terminal_focus_async(
    runtime: Any,
    *,
    working_dir: str,
    agent: str | None,
    session: str | None,
    plain_spinner: bool = False,
    verbosity: str = "normal",
) -> int:
    console = Console()
    transcript = TerminalTranscript(
        console, plain_spinner=plain_spinner, verbosity=verbosity
    )
    status_line = TerminalStatusLine()

    if not sys.stdin.isatty():
        return await _run_one_shot_stdin(
            runtime=runtime,
            console=console,
            transcript=transcript,
            working_dir=working_dir,
        )

    handle_ctrl_l, handle_ctrl_o = _build_ctrl_key_handlers(
        transcript=transcript, console=console
    )

    custom_commands = _discover_custom_commands_for(
        runtime=runtime, working_dir=working_dir
    )

    def handle_shift_tab() -> None:
        _cycle_permission_mode(
            runtime=runtime,
            console=console,
            status_line=status_line,
            announce=False,
        )

    catalog = tuple(_SLASH_COMMANDS) + tuple(custom_commands.keys())
    composer = TerminalComposer(
        slash_commands=catalog,
        bottom_toolbar=status_line.bottom_toolbar,
        history_file=_focus_history_path(runtime),
        on_ctrl_l=handle_ctrl_l,
        on_ctrl_o=handle_ctrl_o,
        on_shift_tab=handle_shift_tab,
        working_dir=working_dir,
    )
    overlay = TerminalOverlayPresenter(console=console)

    _push_greeter(console, runtime=runtime, working_dir=working_dir)
    status_line.set_state(
        agent=str(getattr(runtime, "agent_id", "") or ""),
        cwd=working_dir,
        model=_runtime_label(runtime),
        permission_mode=_runtime_permission_mode(runtime),
        custom=statusline_label(runtime),
        state="idle",
    )

    while True:
        try:
            text = await composer.read_line()
        except (EOFError, KeyboardInterrupt):
            console.print(Text("(exit)", style=_MUTED_STYLE))
            return 0
        text = (text or "").strip()
        if not text:
            continue
        if text in ("/exit", "/quit"):
            return 0
        if text.startswith("/"):
            should_exit = await _handle_slash_input(
                text,
                runtime=runtime,
                console=console,
                transcript=transcript,
                overlay=overlay,
                status_line=status_line,
                working_dir=working_dir,
                custom_commands=custom_commands,
            )
            if should_exit:
                return 0
            continue
        if text.startswith("!"):
            await _run_shell_escape(
                command=text[1:].strip(),
                console=console,
                transcript=transcript,
                working_dir=working_dir,
            )
            continue
        transcript.push_message(
            ChatMessage(kind=MessageKind.USER, sender="you", body=text),
            render=False,
        )
        await _run_agent_turn(
            text=text,
            runtime=runtime,
            transcript=transcript,
            status_line=status_line,
        )


async def _run_one_shot_stdin(
    *,
    runtime: Any,
    console: Console,
    transcript: TerminalTranscript,
    working_dir: str,
) -> int:
    """FTF-08: read stdin to EOF, send as one user turn, exit."""
    text = sys.stdin.read().strip()
    if not text:
        console.print(
            Text(
                "openminion: empty stdin; nothing to ask. Either run "
                "interactively or pipe a prompt.",
                style=_ERR_STYLE,
            )
        )
        return 1
    transcript.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body=text))
    try:
        await _run_agent_turn(
            text=text,
            runtime=runtime,
            transcript=transcript,
            status_line=None,
        )
    except Exception as exc:
        console.print(Text(f"openminion: error — {exc}", style=_ERR_STYLE))
        return 1
    return 0


def _route_durable_activity_event(
    transcript: TerminalTranscript, payload: dict[str, Any]
) -> bool:
    """Route durable activity events to scrollback when recognized."""

    try:
        from openminion.cli.status.activity_ledger import (
            KIND_APPROVAL,
            KIND_BACKGROUND,
            KIND_BUDGET,
            KIND_ERROR,
            KIND_PLAN,
            activity_from_progress_payload,
        )

        event = activity_from_progress_payload(payload)
        if event is not None and event.kind in {
            KIND_PLAN,
            KIND_APPROVAL,
            KIND_BACKGROUND,
            KIND_BUDGET,
            KIND_ERROR,
        }:
            transcript.push_activity_event(event)
            return True
    except Exception:
        pass
    return False


def _build_turn_progress_callback(
    *, transcript: TerminalTranscript, phase_display, state: dict
):
    """Build the progress callback passed to ``runtime.send_message``."""

    def _handle_progress(payload: dict[str, Any]) -> None:
        kind = str(payload.get("kind", "") or "").strip() if payload else ""
        if kind == "tool_started":
            transcript.handle_tool_started(payload)
            return
        if kind == "tool_completed":
            transcript.handle_tool_completed(payload)
            return
        if payload and _route_durable_activity_event(transcript, payload):
            return
        if (
            state["phase_updates_enabled"]
            and phase_display.callback is not None
            and payload
        ):
            phase_display.callback(payload)

    return _handle_progress


def _finalize_turn_status_line(runtime: Any, status_line: TerminalStatusLine) -> None:
    """Refresh the footer with the latest usage summary after a turn ends."""

    usage_summary = ""
    snapshot_getter = getattr(runtime, "token_usage_snapshot", None)
    if callable(snapshot_getter):
        try:
            usage_summary = format_token_usage_summary(snapshot_getter())
        except (AttributeError, TypeError, ValueError):
            usage_summary = ""
    status_line.set_state(
        state="idle",
        usage_summary=usage_summary,
        permission_mode=_runtime_permission_mode(runtime),
        custom=statusline_label(runtime),
    )


async def _run_agent_turn(
    *,
    text: str,
    runtime: Any,
    transcript: TerminalTranscript,
    status_line: TerminalStatusLine | None,
) -> None:
    """Stream tokens through the transcript turn handle."""

    if status_line is not None:
        status_line.set_state(state="idle", elapsed_seconds=0.0)
    handle = transcript.begin_turn(role="assistant")
    reply = ""
    try:
        with PhaseStatusDisplay(enabled=True, animate=True) as phase_display:
            state = {"phase_updates_enabled": True}
            progress_callback = _build_turn_progress_callback(
                transcript=transcript, phase_display=phase_display, state=state
            )
            async for chunk in runtime.send_message(
                text, progress_callback=progress_callback
            ):
                chunk_str = str(chunk or "")
                if not chunk_str:
                    continue
                if state["phase_updates_enabled"]:
                    phase_display.clear()
                    state["phase_updates_enabled"] = False
                reply += chunk_str
                handle.append_token(chunk_str)
            handle.complete(final_text=reply)
    except Exception as exc:
        try:
            handle.complete(final_text=reply)
        except Exception:
            pass
        transcript.push_message(
            ChatMessage(kind=MessageKind.ERROR, sender="error", body=str(exc))
        )
    finally:
        if status_line is not None:
            _finalize_turn_status_line(runtime, status_line)
