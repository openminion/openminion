import asyncio
import shlex
import subprocess
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.text import Text

from openminion.cli.interactive.tool_exposure import tool_exposure_command
from openminion.cli.presentation.models import (
    ChatMessage,
    MessageKind,
    ToolEvent,
)
from openminion.cli.presentation.styles import StyleToken
from openminion.cli.presentation.markers import token_rich_style as _style
from openminion.cli.presentation.theme import handle_theme
from openminion.cli.presentation.theme_roots import resolve_theme_data_root
from openminion.cli.presentation.detail_modes import resolve_details_mode
from .delegation import run_slash_delegate
from .model_setup import handle_model_setup
from openminion.cli.presentation.slash_commands import (
    format_slash_help,
    terminal_slash_commands,
    unknown_slash_command_message,
)
from openminion.cli.presentation.visible_parity import (
    handle_effort_command,
    handle_statusline_command,
    handle_undo_command,
    render_context_report,
    render_memory_report,
    render_skills_report,
    render_tasks_report,
    statusline_label,
)
from openminion.cli.presentation.browser import render_browser_command
from openminion.cli.presentation.graph import render_graph_command

from ..overlays import TerminalOverlayPresenter
from ..status_line import TerminalStatusLine
from ..transcript import TerminalTranscript
from .renderers import (
    _render_cost_snapshot,
    _render_mcp_status,
    _render_model_command,
    _render_sessions_list,
    _render_status_block,
    _render_tools_list,
    _switch_theme_variant,
)
from .project import run_init_command, run_slash_goal, run_slash_project
from .sessions import (
    close_current_session,
    handle_room_slash,
    resume_session,
    start_new_session,
)
from .slash_output import (
    copy_latest_message,
    handle_debug_output_slash,
    render_context_review,
)

_SLASH_COMMANDS = terminal_slash_commands()
_VISIBLE_PARITY_SLASHES = frozenset(
    {
        "/browser",
        "/context",
        "/context-review",
        "/effort",
        "/goal",
        "/graph",
        "/memory",
        "/overview",
        "/skills",
        "/statusline",
        "/tasks",
        "/undo",
    }
)
_ROOM_SLASHES = frozenset(
    {
        "/activate",
        "/handoff",
        "/invite",
        "/kick",
        "/message",
        "/participants",
        "/room",
        "/routing",
        "/start",
    }
)
_FIGLET_FONT = "small"
_FIGLET_TEXT = "OpenMinion"


def _error_style() -> str:
    return str(_style(StyleToken.ERROR))


def _info_bold_style() -> str:
    return str(_style(StyleToken.INFO, bold=True))


def _muted_style(*, italic: bool = False) -> str:
    return str(_style(StyleToken.MUTED, italic=italic))


def _system_style() -> str:
    return str(_style(StyleToken.SYSTEM))


def _slash_arg(text: str) -> str:
    parts = text.split(maxsplit=1)
    return parts[1] if len(parts) > 1 else ""


def _render_openminion_figlet() -> Text:
    try:
        from pyfiglet import Figlet
    except ImportError:
        return Text(_FIGLET_TEXT, style=_info_bold_style())

    rendered = Figlet(font=_FIGLET_FONT, width=72).renderText(_FIGLET_TEXT).rstrip()
    if not rendered:
        return Text(_FIGLET_TEXT, style=_info_bold_style())
    return Text(rendered, style=_info_bold_style())


def _handle_slash_expand(
    text: str, *, transcript: TerminalTranscript, console: Console
) -> None:
    parts = text.split(maxsplit=1)
    index = 1
    if len(parts) > 1:
        try:
            index = int(parts[1].strip())
        except ValueError:
            console.print(
                Text(
                    f"(expected a number after /expand, got {parts[1]!r})",
                    style=_error_style(),
                )
            )
            return
    transcript.expand_block(index)


def _handle_slash_theme(text: str, *, runtime: Any, console: Console) -> None:
    parts = text.split(maxsplit=2)
    if len(parts) > 1 and parts[1].strip().lower() == "variant":
        arg = parts[2].strip().lower() if len(parts) >= 3 else ""
        _switch_theme_variant(arg, console=console)
        return
    handle_theme(
        line=text,
        data_root=resolve_theme_data_root(runtime),
        output=lambda message: console.print(Text.from_ansi(message)),
    )


def _handle_slash_model(text: str, *, runtime: Any, console: Console) -> None:
    _render_model_command(_slash_arg(text), runtime=runtime, console=console)


def _runtime_permission_mode(runtime: Any) -> str:
    return str(getattr(runtime, "permission_mode", "default") or "default").strip()


def _set_permission_mode(
    mode: str,
    *,
    runtime: Any,
    status_line: TerminalStatusLine | None,
) -> str:
    setter = getattr(runtime, "set_permission_mode", None)
    if not callable(setter):
        raise RuntimeError("runtime does not expose set_permission_mode")
    new_mode = str(setter(mode) or "default").strip() or "default"
    if status_line is not None:
        status_line.set_state(permission_mode=new_mode)
    return new_mode


def _cycle_permission_mode(
    *,
    runtime: Any,
    console: Console,
    status_line: TerminalStatusLine | None,
    announce: bool = True,
) -> str:
    cycler = getattr(runtime, "cycle_permission_mode", None)
    if not callable(cycler):
        raise RuntimeError("runtime does not expose cycle_permission_mode")
    new_mode = str(cycler() or "default").strip() or "default"
    if status_line is not None:
        status_line.set_state(permission_mode=new_mode)
    if announce:
        console.print(
            Text(
                f"(permissions: {new_mode} — Shift+Tab cycles modes)",
                style=_muted_style(italic=True),
            )
        )
    return new_mode


def _handle_slash_permissions(
    text: str,
    *,
    runtime: Any,
    console: Console,
    status_line: TerminalStatusLine | None,
) -> None:
    arg = _slash_arg(text).strip().lower()
    if not arg:
        mode = _runtime_permission_mode(runtime)
        overrides = getattr(runtime, "permission_overrides", {})
        override_text = ""
        if isinstance(overrides, dict) and overrides:
            pairs = ", ".join(
                f"{tool}={mode}" for tool, mode in sorted(overrides.items())
            )
            override_text = f"; overrides: {pairs}"
        console.print(
            Text(
                f"(permissions: {mode}{override_text}; use `/permissions default|readonly|bypass`, `/permissions <tool> <ask|auto|bypass|readonly|default>`, or Shift+Tab)",
                style=_muted_style(italic=True),
            )
        )
        return
    if arg == "cycle":
        try:
            _cycle_permission_mode(
                runtime=runtime,
                console=console,
                status_line=status_line,
            )
        except RuntimeError as exc:
            console.print(
                Text(
                    f"(/permissions: {exc})",
                    style=_muted_style(),
                )
            )
        return
    arg_parts = arg.split()
    if len(arg_parts) == 2:
        tool_name, tool_mode = arg_parts
        setter = getattr(runtime, "set_permission_override", None)
        if not callable(setter):
            console.print(
                Text(
                    "(/permissions: runtime does not expose set_permission_override)",
                    style=_error_style(),
                )
            )
            return
        try:
            mode = str(setter(tool_name, tool_mode) or "default")
        except ValueError as exc:
            console.print(
                Text(
                    f"(/permissions: {exc})",
                    style=_error_style(),
                )
            )
            return
        if mode == "default":
            message = f"(permissions: cleared override for {tool_name})"
        else:
            message = f"(permissions: {tool_name} → {mode} — session-scoped)"
        console.print(Text(message, style=_muted_style(italic=True)))
        return
    try:
        mode = _set_permission_mode(arg, runtime=runtime, status_line=status_line)
    except (RuntimeError, ValueError) as exc:
        console.print(
            Text(
                f"(/permissions: {exc})",
                style=_error_style(),
            )
        )
        return
    console.print(
        Text(
            _permission_mode_message(mode),
            style=_muted_style(italic=True),
        )
    )


def _permission_mode_message(mode: str) -> str:
    if str(mode or "").strip().lower() == "bypass":
        return (
            "(permissions: bypass — full access for this session; "
            "use `/permissions` in the interactive CLI for the safer chooser)"
        )
    return f"(permissions: {mode} — session-scoped)"


def _handle_slash_agents(text: str, *, runtime: Any, console: Console) -> None:
    arg = _slash_arg(text).strip()
    lister = getattr(runtime, "list_agents", None)
    if not callable(lister):
        console.print(
            Text(
                "(/agents: runtime does not expose list_agents)",
                style=_error_style(),
            )
        )
        return
    try:
        agents = list(lister() or [])
    except Exception as exc:
        console.print(Text(f"(/agents: {exc})", style=_error_style()))
        return
    rows = []
    for item in agents:
        agent_id = str(getattr(item, "id", "") or "").strip()
        label = str(getattr(item, "label", "") or agent_id).strip()
        active = bool(getattr(item, "active", False))
        if arg and arg not in {agent_id, label}:
            continue
        rows.append((agent_id, label, active))
    if not rows:
        console.print(
            Text(
                "(/agents: none found)",
                style=_muted_style(italic=True),
            )
        )
        return
    for agent_id, label, active in rows:
        marker = "◆" if active else " "
        suffix = f" — {label}" if label and label != agent_id else ""
        console.print(
            Text(
                f"{marker} {agent_id}{suffix}",
                style=_system_style(),
            )
        )


def _handle_slash_diff(
    text: str,
    *,
    transcript: TerminalTranscript,
    console: Console,
    working_dir: str,
) -> None:
    from openminion.cli.presentation.git.diff import render_git_diff

    args = _slash_arg(text).strip()
    try:
        result = render_git_diff(working_dir, args)
    except ValueError as exc:
        console.print(Text(f"(/diff: {exc})", style=_error_style()))
        return
    if not result.has_diff:
        style = _error_style() if result.exit_code else _muted_style(italic=True)
        console.print(Text(result.message, style=style))
        return
    label = " ".join(result.command[1:])
    event = ToolEvent(
        tool_name="Edit",
        args={"cmd": f"git {label}".strip()},
        content=result.output,
        full_content=result.output,
        duration_ms=result.duration_ms,
        exit_code=0,
    )
    transcript.push_message(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool",
            body="",
            tool_event=event,
        )
    )


def _handle_slash_review(
    text: str,
    *,
    console: Console,
    working_dir: str,
) -> None:
    from openminion.cli.presentation.review import run_review_workflow

    args = _slash_arg(text).strip()
    result = run_review_workflow(working_dir, args)
    token = StyleToken.ERROR if result.action_result is None else StyleToken.SYSTEM
    style = _style(token)
    console.print(Text(result.body, style=style))


def _handle_slash_readonly(
    text: str,
    *,
    runtime: Any,
    console: Console,
    status_line: TerminalStatusLine | None = None,
) -> None:
    arg = _slash_arg(text).strip().lower()
    setter = getattr(runtime, "set_read_only_mode", None)
    if not callable(setter):
        console.print(
            Text(
                "(/readonly: runtime does not expose set_read_only_mode)",
                style=_muted_style(),
            )
        )
        return
    current = bool(getattr(runtime, "read_only_mode", False))
    if arg == "on":
        new_state = setter(True)
    elif arg == "off":
        new_state = setter(False)
    elif arg in ("", "toggle"):
        new_state = setter(not current)
    else:
        console.print(
            Text(
                f"(/readonly: unknown arg {arg!r}; use `/readonly on|off|toggle` or bare `/readonly`)",
                style=_error_style(),
            )
        )
        return
    label = "ON" if new_state else "OFF"
    if status_line is not None:
        status_line.set_state(permission_mode=_runtime_permission_mode(runtime))
    hint = (
        "write tools (Edit/Write/Bash) will be blocked at the runtime tier (FPC-11b)"
        if new_state
        else "all tools allowed (default)"
    )
    console.print(
        Text(
            f"(read-only mode: {label} — {hint})",
            style=_muted_style(italic=True),
        )
    )


def _handle_slash_compact(*, runtime: Any, console: Console) -> None:
    compacter = getattr(runtime, "compact_history", None)
    if not callable(compacter):
        console.print(
            Text(
                "(/compact: runtime does not expose compact_history)",
                style=_muted_style(),
            )
        )
        return
    try:
        result = compacter()
    except Exception as exc:
        console.print(
            Text(
                f"(/compact: error — {exc})",
                style=_error_style(),
            )
        )
        return
    if not isinstance(result, dict):
        console.print(
            Text(
                f"(/compact: unexpected result shape: {type(result).__name__})",
                style=_error_style(),
            )
        )
        return
    if result.get("reason") == "no_session":
        console.print(
            Text(
                "(/compact: no active session)",
                style=_muted_style(italic=True),
            )
        )
        return
    count = int(result.get("compacted_count", 0) or 0)
    if count == 0:
        console.print(
            Text(
                "(/compact: nothing to compact — recent messages are below the keep threshold)",
                style=_muted_style(italic=True),
            )
        )
        return
    suffix = ""
    token_total = result.get("session_total_tokens")
    if token_total is not None:
        suffix = f" · session tokens {token_total}"
    noun = "turn" if count == 1 else "turns"
    console.print(
        Text(
            f"(/compact: compacted {count} {noun}{suffix})",
            style=_muted_style(italic=True),
        )
    )


def _handle_slash_verbosity(
    cmd: str, *, transcript: TerminalTranscript, console: Console
) -> None:
    new_level = cmd[1:]  # strip the leading slash
    transcript.set_verbosity(new_level)
    if new_level == "quiet":
        hint = "tool blocks hidden until /normal or /verbose"
    elif new_level == "verbose":
        hint = "tool blocks show full output until /normal or /quiet"
    else:  # normal
        hint = "tool blocks truncated to 6 lines, /expand for full"
    console.print(
        Text(
            f"(verbosity: {new_level} — {hint})",
            style=_muted_style(italic=True),
        )
    )


def _handle_slash_details(
    text: str, *, transcript: TerminalTranscript, console: Console
) -> None:
    arg = _slash_arg(text)
    new_level, message = resolve_details_mode(transcript._verbosity, arg)
    transcript.set_verbosity(new_level)
    console.print(
        Text(
            f"(details: {message})",
            style=_muted_style(italic=True),
        )
    )


def _print_slash_help(console: Console) -> None:
    console.print(
        Text(
            format_slash_help(width=console.width),
            style=_system_style(),
        )
    )


def _print_unknown_slash_notice(cmd: str, console: Console) -> None:
    console.print(
        Text(
            unknown_slash_command_message(
                cmd,
                available_commands=_SLASH_COMMANDS,
            ),
            style=_error_style(),
        )
    )


def _handle_visible_parity_slash(
    cmd: str,
    text: str,
    *,
    runtime: Any,
    console: Console,
    status_line: TerminalStatusLine,
    working_dir: str,
) -> None:
    arg = _slash_arg(text)
    if cmd == "/context":
        console.print(
            Text(
                render_context_report(runtime),
                style=_system_style(),
            )
        )
    elif cmd == "/context-review":
        console.print(
            Text(
                render_context_review(runtime, arg),
                style=_system_style(),
            )
        )
    elif cmd == "/overview":
        from openminion.cli.status.overview import (
            build_operations_overview,
            render_operations_overview,
        )

        snapshot = build_operations_overview(runtime, working_dir=working_dir)
        console.print(
            Text(
                render_operations_overview(snapshot),
                style=_system_style(),
            )
        )
    elif cmd == "/memory":
        console.print(
            Text(
                render_memory_report(runtime),
                style=_system_style(),
            )
        )
    elif cmd == "/graph":
        console.print(Text(render_graph_command(arg), style=_system_style()))
    elif cmd == "/skills":
        console.print(
            Text(
                render_skills_report(runtime, arg),
                style=_system_style(),
            )
        )
    elif cmd == "/browser":
        console.print(
            Text(
                render_browser_command(arg, working_dir=working_dir),
                style=_system_style(),
            )
        )
    elif cmd == "/tasks":
        console.print(
            Text(
                render_tasks_report(runtime, arg),
                style=_system_style(),
            )
        )
    elif cmd == "/effort":
        console.print(
            Text(
                handle_effort_command(runtime, arg),
                style=_muted_style(italic=True),
            )
        )
    elif cmd == "/statusline":
        console.print(
            Text(
                handle_statusline_command(runtime, arg),
                style=_muted_style(italic=True),
            )
        )
        status_line.set_state(custom=statusline_label(runtime))
    elif cmd == "/undo":
        console.print(
            Text(
                handle_undo_command(runtime, arg, working_dir=working_dir),
                style=_muted_style(italic=True),
            )
        )
    elif cmd == "/goal":
        run_slash_goal(
            text,
            runtime=runtime,
            console=console,
            status_line=status_line,
        )


def _render_tools_command(runtime: Any, console: Console, text: str) -> None:
    if text.strip() == "/tools":
        _render_tools_list(runtime=runtime, console=console)
    else:
        console.print(tool_exposure_command(runtime, text))


async def _handle_session_slash(
    cmd: str,
    text: str,
    *,
    runtime: Any,
    console: Console,
    transcript: TerminalTranscript,
    overlay: TerminalOverlayPresenter,
    working_dir: str,
) -> bool:
    if cmd == "/clear":
        transcript.clear_messages()
    elif cmd == "/init":
        run_init_command(
            runtime=runtime,
            console=console,
            overlay=overlay,
            working_dir=working_dir,
        )
    elif cmd == "/new":
        start_new_session(runtime=runtime, console=console, transcript=transcript)
    elif cmd == "/close":
        close_current_session(runtime=runtime, console=console, transcript=transcript)
    elif cmd == "/diff":
        _handle_slash_diff(
            text,
            transcript=transcript,
            console=console,
            working_dir=working_dir,
        )
    elif cmd == "/review":
        _handle_slash_review(text, console=console, working_dir=working_dir)
    elif cmd == "/expand":
        _handle_slash_expand(text, transcript=transcript, console=console)
    elif cmd == "/sessions":
        _render_sessions_list(runtime=runtime, console=console)
    elif cmd == "/resume":
        resume_session(
            runtime=runtime,
            console=console,
            transcript=transcript,
            overlay=overlay,
        )
    elif cmd == "/status":
        _render_status_block(runtime=runtime, console=console, working_dir=working_dir)
    elif cmd == "/copy":
        copy_latest_message(transcript, console)
    else:
        return False
    return True


async def _handle_slash(
    text: str,
    *,
    runtime: Any,
    console: Console,
    transcript: TerminalTranscript,
    overlay: TerminalOverlayPresenter,
    status_line: TerminalStatusLine,
    working_dir: str,
    approval_callback: Callable[[str, dict[str, Any], Any], Any] | None = None,
) -> bool:
    cmd = text.split(maxsplit=1)[0]

    if cmd in ("/exit", "/quit"):
        return True
    if cmd in ("/", "/help"):
        _print_slash_help(console)
        return False
    if await _handle_session_slash(
        cmd,
        text,
        runtime=runtime,
        console=console,
        transcript=transcript,
        overlay=overlay,
        working_dir=working_dir,
    ):
        return False
    if cmd == "/delegate":
        await run_slash_delegate(text, runtime, console, approval_callback, transcript)
        return False
    if cmd == "/project":
        await run_slash_project(
            text,
            runtime=runtime,
            console=console,
            approval_callback=approval_callback,
        )
        return False
    if cmd in _VISIBLE_PARITY_SLASHES:
        _handle_visible_parity_slash(
            cmd,
            text,
            runtime=runtime,
            console=console,
            status_line=status_line,
            working_dir=working_dir,
        )
        return False
    if cmd in ("/tools", "/mcp", "/theme", "/model"):
        await _handle_tool_view_slash(cmd, text, runtime, console, overlay)
        return False
    if handle_debug_output_slash(
        cmd, text, runtime=runtime, console=console, cost_renderer=_render_cost_snapshot
    ):
        return False
    if cmd == "/agents":
        _handle_slash_agents(text, runtime=runtime, console=console)
        return False
    if cmd in _ROOM_SLASHES:
        await handle_room_slash(
            cmd,
            _slash_arg(text),
            runtime=runtime,
            console=console,
            transcript=transcript,
            overlay=overlay,
            approval_callback=approval_callback,
        )
        return False
    if cmd == "/readonly":
        _handle_slash_readonly(
            text,
            runtime=runtime,
            console=console,
            status_line=status_line,
        )
        return False
    if cmd == "/permissions":
        _handle_slash_permissions(
            text,
            runtime=runtime,
            console=console,
            status_line=status_line,
        )
        return False
    if cmd == "/compact":
        _handle_slash_compact(runtime=runtime, console=console)
        return False
    if _handle_shell_preference_slash(
        cmd,
        text,
        runtime=runtime,
        console=console,
        transcript=transcript,
    ):
        return False
    _print_unknown_slash_notice(cmd, console)
    return False


async def _handle_tool_view_slash(
    cmd: str,
    text: str,
    runtime: Any,
    console: Console,
    overlay: TerminalOverlayPresenter,
) -> bool:
    if cmd == "/tools":
        _render_tools_command(runtime, console, text)
    elif cmd == "/mcp":
        _render_mcp_status(runtime=runtime, console=console)
    elif cmd == "/theme":
        _handle_slash_theme(text, runtime=runtime, console=console)
    elif cmd == "/model":
        if _slash_arg(text).strip() == "setup":
            await handle_model_setup(runtime=runtime, console=console, overlay=overlay)
        else:
            _handle_slash_model(text, runtime=runtime, console=console)
    else:
        return False
    return True


def _handle_shell_preference_slash(
    cmd: str,
    text: str,
    *,
    runtime: Any,
    console: Console,
    transcript: TerminalTranscript,
) -> bool:
    if cmd == "/queue":
        console.print(
            Text(
                "(/queue is handled by the interactive input loop; use it from the CLI prompt)",
                style=_muted_style(italic=True),
            )
        )
    elif cmd in ("/quiet", "/verbose", "/normal"):
        _handle_slash_verbosity(cmd, transcript=transcript, console=console)
    elif cmd == "/details":
        _handle_slash_details(text, transcript=transcript, console=console)
    elif cmd in ("/export", "/editor"):
        if cmd == "/export":
            session_id = str(getattr(runtime, "session_id", "") or "").strip()
            command = (
                f"openminion export transcript --session-id {session_id} --format md"
                if session_id
                else "openminion export transcript --session-id <session-id> --format md"
            )
            message = (
                f"(export: run `{command}` from a regular terminal; "
                "add `--output transcript.md` to write a file)"
            )
        else:
            message = (
                "(editor: use /editor from the idle prompt for a new external-editor draft, or "
                "Ctrl-X Ctrl-E to edit the live draft)"
            )
        console.print(Text(message, style=_muted_style(italic=True)))
    else:
        return False
    return True


async def _run_shell_escape(
    *,
    command: str,
    console: Console,
    transcript: TerminalTranscript,
    working_dir: str,
) -> None:
    if not command:
        return
    transcript.push_message(
        ChatMessage(kind=MessageKind.USER, sender="you", body=f"!{command}")
    )
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        transcript.push_message(
            ChatMessage(
                kind=MessageKind.ERROR,
                sender="error",
                body=f"Could not parse `!{command}`: {exc}",
            )
        )
        return
    if not argv:
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=working_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        transcript.push_message(
            ChatMessage(
                kind=MessageKind.ERROR,
                sender="error",
                body=f"Command not found: {exc.filename or argv[0]}",
            )
        )
        return
    except Exception as exc:
        transcript.push_message(
            ChatMessage(
                kind=MessageKind.ERROR,
                sender="error",
                body=f"Could not run `!{command}`: {exc}",
            )
        )
        return
    stdout_b, stderr_b = await proc.communicate()
    stdout = (stdout_b or b"").decode("utf-8", errors="replace")
    stderr = (stderr_b or b"").decode("utf-8", errors="replace")
    combined = stdout
    if stderr:
        combined = (combined + ("\n" if combined else "") + stderr).rstrip()
    event = ToolEvent(
        tool_name="bash",
        args={"cmd": command},
        content=combined or "(no output)",
        full_content=combined,
        duration_ms=0,
        exit_code=int(proc.returncode or 0),
    )
    transcript.push_message(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="bash",
            body="",
            tool_event=event,
            tool_result=combined,
        )
    )
