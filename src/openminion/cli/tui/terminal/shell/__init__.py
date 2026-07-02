from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import os
import sys
from pathlib import Path
from typing import Any

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - POSIX-only terminal interrupt support.
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

from rich.console import Console
from rich.text import Text

from openminion.base.config.env import resolve_environment_config
from openminion.cli.status import format_token_usage_summary
from openminion.cli.status.tool_calls import format_tool_args_preview
from openminion.cli.tui.presentation.models import (
    ChatMessage,
    MessageKind,
)
from openminion.modules.telemetry.trace.phase_timing import mark_active_chat_first_text

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
from openminion.cli.tui.presentation.slash_commands import slash_help_rows
from openminion.cli.tui.presentation.visible_parity import (
    statusline_label,
)

__all__ = [
    "run_terminal_focus",
    "_discover_custom_commands_for",
    "_focus_history_path",
    "_confirm_terminal_exit",
    "_handle_slash_input",
    "_run_one_shot_stdin",
    "_run_agent_turn",
    "_run_interruptible_agent_turn",
    "_build_terminal_approval_callback",
    "_route_durable_activity_event",
    "_normalize_progress_kind",
    "_build_turn_progress_callback",
    "_finalize_turn_status_line",
    "_start_escape_interrupt_watcher",
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
    "_show_response_time_enabled",
    "_emit_startup_notice",
    "_schedule_startup_notice",
]


_ERR_STYLE = token_rich_style(StyleToken.ERROR)
_INFO_STYLE = token_rich_style(StyleToken.INFO)
_INFO_BOLD_STYLE = token_rich_style(StyleToken.INFO, bold=True)
_MUTED_STYLE = token_rich_style(StyleToken.MUTED)
_MUTED_ITALIC_STYLE = f"italic {_MUTED_STYLE}" if _MUTED_STYLE else "italic"
_SYSTEM_STYLE = token_rich_style(StyleToken.SYSTEM)
_ESCAPE_BYTE = b"\x1b"


@dataclass(frozen=True)
class _EscapeInterruptWatcher:
    stop: Callable[[], None]
    interrupted: Callable[[], bool]


async def _confirm_terminal_exit(
    *, console: Console, overlay: TerminalOverlayPresenter
) -> bool:
    should_exit = await overlay.present_confirm_async("Exit focus mode?")
    if not should_exit:
        console.print(Text("(exit cancelled)", style=_MUTED_ITALIC_STYLE))
    return should_exit


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


def _show_response_time_enabled(env: Any | None = None) -> bool:
    return resolve_environment_config(env=env).openminion_show_response_time


async def _emit_startup_notice(
    startup_notice: Callable[[], str],
    *,
    transcript: TerminalTranscript,
) -> None:
    try:
        notice = await asyncio.to_thread(startup_notice)
    except Exception:
        return
    notice = str(notice or "").strip()
    if not notice:
        return
    transcript.push_message(
        ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=notice)
    )


def _schedule_startup_notice(
    startup_notice: Callable[[], str] | None,
    *,
    transcript: TerminalTranscript,
) -> asyncio.Task[None] | None:
    if startup_notice is None:
        return None
    return asyncio.create_task(
        _emit_startup_notice(startup_notice, transcript=transcript)
    )


def _cancel_startup_notice(task: asyncio.Task[None] | None) -> None:
    if task is not None and not task.done():
        task.cancel()


def _start_escape_interrupt_watcher(
    turn_task: asyncio.Task[None],
    *,
    stdin: Any = None,
) -> _EscapeInterruptWatcher | None:
    """Watch the terminal for Escape while a turn is running."""

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

    return _EscapeInterruptWatcher(
        stop=_restore_terminal,
        interrupted=lambda: interrupted,
    )


def run_terminal_focus(
    runtime: Any,
    *,
    working_dir: str | None = None,
    agent: str | None = None,
    session: str | None = None,
    plain_spinner: bool = False,
    verbosity: str = "normal",
    startup_notice: Callable[[], str] | None = None,
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
            startup_notice=startup_notice,
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
        await _run_interruptible_agent_turn(
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
    startup_notice: Callable[[], str] | None = None,
) -> int:
    console = Console()
    transcript = TerminalTranscript(
        console,
        plain_spinner=plain_spinner,
        verbosity=verbosity,
        show_response_time=_show_response_time_enabled(),
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

    catalog = {
        name: description
        for name, description in slash_help_rows(terminal_only=True)
        if name in _SLASH_COMMANDS
    }
    catalog.update({name: "custom command" for name in custom_commands})
    composer = TerminalComposer(
        slash_commands=catalog,
        bottom_toolbar=status_line.bottom_toolbar,
        history_file=_focus_history_path(runtime),
        on_ctrl_l=handle_ctrl_l,
        on_ctrl_o=handle_ctrl_o,
        on_shift_tab=handle_shift_tab,
        working_dir=working_dir,
    )
    overlay = TerminalOverlayPresenter(
        console=console,
        prompt_session=composer.prompt_session,
    )

    _push_greeter(console, runtime=runtime, working_dir=working_dir)
    startup_notice_task = _schedule_startup_notice(
        startup_notice,
        transcript=transcript,
    )
    status_line.set_state(
        agent=str(getattr(runtime, "agent_id", "") or ""),
        cwd=working_dir,
        model=_runtime_label(runtime),
        permission_mode=_runtime_permission_mode(runtime),
        custom=statusline_label(runtime),
        state="idle",
    )

    try:
        while True:
            try:
                text = await composer.read_line()
            except EOFError:
                console.print(Text("(exit)", style=_MUTED_STYLE))
                return 0
            except KeyboardInterrupt:
                if await _confirm_terminal_exit(console=console, overlay=overlay):
                    console.print(Text("(exit)", style=_MUTED_STYLE))
                    return 0
                continue
            text = (text or "").strip()
            if not text:
                continue
            if text in ("/exit", "/quit"):
                return 0
            try:
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
                await _run_interruptible_agent_turn(
                    text=text,
                    runtime=runtime,
                    transcript=transcript,
                    status_line=status_line,
                )
            except KeyboardInterrupt:
                if await _confirm_terminal_exit(console=console, overlay=overlay):
                    console.print(Text("(exit)", style=_MUTED_STYLE))
                    return 0
    finally:
        _cancel_startup_notice(startup_notice_task)


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


def _normalize_progress_kind(payload: dict[str, Any] | None) -> str:
    """Normalize equivalent runtime progress event names for TUI routing."""

    if not payload:
        return ""
    aliases = {
        "tool_start": "tool_started",
        "tool_started": "tool_started",
        "tool_call_start": "tool_started",
        "tool_call_started": "tool_started",
        "tool_complete": "tool_completed",
        "tool_completed": "tool_completed",
        "tool_finish": "tool_completed",
        "tool_finished": "tool_completed",
        "tool_call_complete": "tool_completed",
        "tool_call_completed": "tool_completed",
    }
    for key in ("kind", "source_event", "source_event_type", "event_type"):
        raw = payload.get(key)
        normalized = str(raw or "").strip().lower().replace(".", "_").replace("-", "_")
        if normalized in aliases:
            return aliases[normalized]
    return ""


def _build_turn_progress_callback(
    *,
    transcript: TerminalTranscript,
    handle: Any | None = None,
    status_controller: Any | None = None,
    status_line: TerminalStatusLine | None = None,
):
    """Build the progress callback passed to ``runtime.send_message``."""
    def _handle_progress(payload: dict[str, Any]) -> None:
        kind = _normalize_progress_kind(payload)
        if kind == "tool_started":
            transcript.handle_tool_started(payload)
            return
        if kind == "tool_completed":
            transcript.handle_tool_completed(payload)
            return
        if payload and _route_durable_activity_event(transcript, payload):
            return
        if payload and handle is not None and status_controller is not None:
            try:
                view = status_controller.update(payload)
            except Exception:
                view = None
            if view is None:
                return
            label = str(getattr(view, "primary_text", "") or "")
            setter = getattr(handle, "set_status_label", None)
            if callable(setter):
                setter(label)

    return _handle_progress


def _format_terminal_approval_prompt(tool_name: str, args: dict[str, Any]) -> str:
    name = str(tool_name or "tool").strip() or "tool"
    args_preview = format_tool_args_preview(name, dict(args or {}))
    call_line = f"{name}({args_preview})" if args_preview else f"{name}()"
    return f"Approval required: {call_line}"


def _build_terminal_approval_callback(
    *,
    overlay: TerminalOverlayPresenter,
    session_grants: set[str],
) -> Callable[[str, dict[str, Any], Any], Any]:
    async def _approval_callback(
        tool_name: str,
        args: dict[str, Any],
        call_id: Any,
    ) -> bool:
        del call_id
        normalized = str(tool_name or "").strip()
        if normalized and normalized in session_grants:
            return True
        prompt = _format_terminal_approval_prompt(normalized, dict(args or {}))
        decision = await overlay._present_approval_async(prompt)
        if decision == "always" and normalized:
            session_grants.add(normalized)
            return True
        return decision == "allow"

    return _approval_callback


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


async def _run_interruptible_agent_turn(
    *,
    text: str,
    runtime: Any,
    transcript: TerminalTranscript,
    status_line: TerminalStatusLine | None,
) -> None:
    """Run one agent turn and let Escape cancel it in terminal focus."""

    turn_task = asyncio.create_task(
        _run_agent_turn(
            text=text,
            runtime=runtime,
            transcript=transcript,
            status_line=status_line,
        )
    )
    watcher = _start_escape_interrupt_watcher(turn_task)
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
    handle = transcript.begin_turn(
        role="assistant",
        footer_provider=status_line.live_turn_footer if status_line is not None else None,
    )
    reply = ""
    from openminion.cli.status import PhaseStatusController

    status_controller = PhaseStatusController(fallback_label="Working...")
    status_controller.start_turn()
    initial_status = status_controller.view_model_for(None)
    setter = getattr(handle, "set_status_label", None)
    initial_label = str(initial_status.primary_text or status_controller.fallback_label)
    if callable(setter):
        setter(initial_label)
    try:
        progress_callback = _build_turn_progress_callback(
            transcript=transcript,
            handle=handle,
            status_controller=status_controller,
            status_line=status_line,
        )
        async for chunk in runtime.send_message(
            text,
            progress_callback=progress_callback,
        ):
            chunk_str = str(chunk or "")
            if not chunk_str:
                continue
            reply += chunk_str
            mark_active_chat_first_text()
            handle.append_token(chunk_str)
        handle.complete(final_text=reply)
    except asyncio.CancelledError:
        try:
            handle.complete(final_text=reply)
        except Exception:
            pass
        raise
    except Exception as exc:
        try:
            handle.complete(final_text=reply)
        except Exception:
            pass
        transcript.push_message(
            ChatMessage(kind=MessageKind.ERROR, sender="error", body=str(exc))
        )
    finally:
        status_controller.end_turn()
        if status_line is not None:
            _finalize_turn_status_line(runtime, status_line)
