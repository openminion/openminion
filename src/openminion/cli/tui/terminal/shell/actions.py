from __future__ import annotations

import asyncio
import shlex
import subprocess
from typing import Any

from rich.console import Console
from rich.text import Text

from openminion.cli.tui.project_context import (
    find_project_context_target_root,
    write_init_template,
)
from openminion.cli.tui.presentation.models import (
    ChatMessage,
    MessageKind,
    ToolEvent,
)
from openminion.cli.presentation.styles import StyleToken
from openminion.cli.tui.presentation.markers import token_rich_style
from openminion.cli.tui.presentation.detail_modes import resolve_details_mode
from .labels import _runtime_label
from openminion.cli.tui.presentation.slash_commands import (
    slash_help_rows,
    terminal_slash_commands,
)
from openminion.cli.tui.presentation.visible_parity import (
    handle_effort_command,
    handle_statusline_command,
    handle_undo_command,
    render_context_report,
    render_memory_report,
    render_skills_report,
    statusline_label,
)

from ..overlays import TerminalOverlayPresenter
from ..status_line import TerminalStatusLine
from ..transcript import TerminalTranscript
from .renderers import (
    _render_cost_snapshot,
    _render_mcp_status,
    _render_model_status,
    _render_sessions_list,
    _render_status_block,
    _render_theme_status,
    _render_tools_list,
    _switch_theme,
    _switch_theme_variant,
)

_ERR_STYLE = token_rich_style(StyleToken.ERROR)
_INFO_STYLE = token_rich_style(StyleToken.INFO)
_INFO_BOLD_STYLE = token_rich_style(StyleToken.INFO, bold=True)
_MUTED_STYLE = token_rich_style(StyleToken.MUTED)
_MUTED_ITALIC_STYLE = f"italic {_MUTED_STYLE}" if _MUTED_STYLE else "italic"
_SYSTEM_STYLE = token_rich_style(StyleToken.SYSTEM)

_SLASH_COMMANDS = terminal_slash_commands()
_FIGLET_FONT = "small"
_FIGLET_TEXT = "OpenMinion"


def _render_openminion_figlet() -> Text:
    try:
        from pyfiglet import Figlet
    except ImportError:
        return Text(_FIGLET_TEXT, style=_INFO_BOLD_STYLE)

    rendered = Figlet(font=_FIGLET_FONT, width=72).renderText(_FIGLET_TEXT).rstrip()
    if not rendered:
        return Text(_FIGLET_TEXT, style=_INFO_BOLD_STYLE)
    return Text(rendered, style=_INFO_BOLD_STYLE)


def _handle_slash_expand(
    text: str, *, transcript: TerminalTranscript, console: Console
) -> None:
    """FTR-05: re-render a truncated tool block in full."""

    parts = text.split(maxsplit=1)
    index = 1
    if len(parts) > 1:
        try:
            index = int(parts[1].strip())
        except ValueError:
            console.print(
                Text(
                    f"(expected a number after /expand, got {parts[1]!r})",
                    style=_ERR_STYLE,
                )
            )
            return
    transcript.expand_block(index)


def _handle_slash_theme(text: str, *, console: Console) -> None:
    """FVP-09: theme + variant switching dispatch."""

    parts = text.split(maxsplit=2)
    if len(parts) == 1:
        _render_theme_status(console=console)
    elif parts[1].strip().lower() == "variant":
        arg = parts[2].strip().lower() if len(parts) >= 3 else ""
        _switch_theme_variant(arg, console=console)
    else:
        _switch_theme(parts[1].strip(), console=console)


def _handle_slash_model(text: str, *, runtime: Any, console: Console) -> None:
    """FPC-04: show or switch the active provider/model. Session-scoped."""

    parts = text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg:
        _render_model_status(runtime=runtime, console=console)
        return
    try:
        provider, model = runtime.switch_model(arg)
    except ValueError as exc:
        console.print(Text(f"(/model: {exc})", style=_ERR_STYLE))
        return
    label = f"{provider}/{model}" if model else provider or "(default)"
    console.print(
        Text(
            f"(model: switched to {label} — session-scoped)",
            style=_MUTED_ITALIC_STYLE,
        )
    )


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
                style=_MUTED_ITALIC_STYLE,
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
    """Show or set the session-scoped permission mode."""

    parts = text.split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""
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
                style=_MUTED_ITALIC_STYLE,
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
            console.print(Text(f"(/permissions: {exc})", style=_MUTED_STYLE))
        return
    arg_parts = arg.split()
    if len(arg_parts) == 2:
        tool_name, tool_mode = arg_parts
        setter = getattr(runtime, "set_permission_override", None)
        if not callable(setter):
            console.print(
                Text(
                    "(/permissions: runtime does not expose set_permission_override)",
                    style=_ERR_STYLE,
                )
            )
            return
        try:
            mode = str(setter(tool_name, tool_mode) or "default")
        except ValueError as exc:
            console.print(Text(f"(/permissions: {exc})", style=_ERR_STYLE))
            return
        if mode == "default":
            message = f"(permissions: cleared override for {tool_name})"
        else:
            message = f"(permissions: {tool_name} → {mode} — session-scoped)"
        console.print(Text(message, style=_MUTED_ITALIC_STYLE))
        return
    try:
        mode = _set_permission_mode(arg, runtime=runtime, status_line=status_line)
    except (RuntimeError, ValueError) as exc:
        console.print(Text(f"(/permissions: {exc})", style=_ERR_STYLE))
        return
    console.print(
        Text(
            _permission_mode_message(mode),
            style=_MUTED_ITALIC_STYLE,
        )
    )


def _permission_mode_message(mode: str) -> str:
    if str(mode or "").strip().lower() == "bypass":
        return (
            "(permissions: bypass — full access for this session; "
            "use `/permissions` in Focus for the safer chooser)"
        )
    return f"(permissions: {mode} — session-scoped)"


def _handle_slash_agents(text: str, *, runtime: Any, console: Console) -> None:
    """List configured agents or show one agent id."""

    parts = text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    lister = getattr(runtime, "list_agents", None)
    if not callable(lister):
        console.print(
            Text("(/agents: runtime does not expose list_agents)", style=_ERR_STYLE)
        )
        return
    try:
        agents = list(lister() or [])
    except Exception as exc:
        console.print(Text(f"(/agents: {exc})", style=_ERR_STYLE))
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
        console.print(Text("(/agents: none found)", style=_MUTED_ITALIC_STYLE))
        return
    for agent_id, label, active in rows:
        marker = "◆" if active else " "
        suffix = f" — {label}" if label and label != agent_id else ""
        console.print(Text(f"{marker} {agent_id}{suffix}", style=_SYSTEM_STYLE))


def _handle_slash_diff(
    text: str,
    *,
    transcript: TerminalTranscript,
    console: Console,
    working_dir: str,
) -> None:
    """Render workspace git diff through the terminal tool-block path."""

    from openminion.cli.tui.presentation.git.diff import render_git_diff

    parts = text.split(maxsplit=1)
    args = parts[1].strip() if len(parts) > 1 else ""
    try:
        result = render_git_diff(working_dir, args)
    except ValueError as exc:
        console.print(Text(f"(/diff: {exc})", style=_ERR_STYLE))
        return
    if not result.has_diff:
        style = _ERR_STYLE if result.exit_code else _MUTED_ITALIC_STYLE
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
    transcript.handle_tool_completed(event)


def _handle_slash_readonly(
    text: str,
    *,
    runtime: Any,
    console: Console,
    status_line: TerminalStatusLine | None = None,
) -> None:
    """Toggle session-scoped read-only mode."""

    parts = text.split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""
    setter = getattr(runtime, "set_read_only_mode", None)
    getter = getattr(runtime, "read_only_mode", None)
    if not callable(setter):
        console.print(
            Text(
                "(/readonly: runtime does not expose set_read_only_mode)",
                style=_MUTED_STYLE,
            )
        )
        return
    current = bool(getter) if not callable(getter) else bool(getter)
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
                style=_ERR_STYLE,
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
        Text(f"(read-only mode: {label} — {hint})", style=_MUTED_ITALIC_STYLE)
    )


def _handle_slash_compact(*, runtime: Any, console: Console) -> None:
    """Compact conversation via ``OpenMinionRuntime.compact_history``."""

    compacter = getattr(runtime, "compact_history", None)
    if not callable(compacter):
        console.print(
            Text(
                "(/compact: runtime does not expose compact_history)",
                style=_MUTED_STYLE,
            )
        )
        return
    try:
        result = compacter()
    except Exception as exc:
        console.print(Text(f"(/compact: error — {exc})", style=_ERR_STYLE))
        return
    if not isinstance(result, dict):
        console.print(
            Text(
                f"(/compact: unexpected result shape: {type(result).__name__})",
                style=_ERR_STYLE,
            )
        )
        return
    if result.get("reason") == "no_session":
        console.print(Text("(/compact: no active session)", style=_MUTED_ITALIC_STYLE))
        return
    count = int(result.get("compacted_count", 0) or 0)
    if count == 0:
        console.print(
            Text(
                "(/compact: nothing to compact — recent messages are below the keep threshold)",
                style=_MUTED_ITALIC_STYLE,
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
            style=_MUTED_ITALIC_STYLE,
        )
    )


def _handle_slash_verbosity(
    cmd: str, *, transcript: TerminalTranscript, console: Console
) -> None:
    """Apply a live verbosity override."""

    new_level = cmd[1:]  # strip the leading slash
    transcript.set_verbosity(new_level)
    if new_level == "quiet":
        hint = "tool blocks hidden until /normal or /verbose"
    elif new_level == "verbose":
        hint = "tool blocks show full output until /normal or /quiet"
    else:  # normal
        hint = "tool blocks truncated to 6 lines, /expand for full"
    console.print(Text(f"(verbosity: {new_level} — {hint})", style=_MUTED_ITALIC_STYLE))


def _handle_slash_details(
    text: str, *, transcript: TerminalTranscript, console: Console
) -> None:
    arg = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
    new_level, message = resolve_details_mode(transcript._verbosity, arg)
    transcript.set_verbosity(new_level)
    console.print(Text(f"(details: {message})", style=_MUTED_ITALIC_STYLE))


def _handle_slash_export(*, runtime: Any, console: Console) -> None:
    session_id = str(getattr(runtime, "session_id", "") or "").strip()
    if session_id:
        command = f"openminion export transcript --session-id {session_id} --format md"
    else:
        command = "openminion export transcript --session-id <session-id> --format md"
    console.print(
        Text(
            f"(export: run `{command}` from a regular terminal; "
            "add `--output transcript.md` to write a file)",
            style=_MUTED_ITALIC_STYLE,
        )
    )


def _handle_slash_editor(console: Console) -> None:
    console.print(
        Text(
            "(editor: external-editor composition is not bound in this renderer yet; "
            "use multiline input, paste content, or @-mention files)",
            style=_MUTED_ITALIC_STYLE,
        )
    )


def _print_slash_help(console: Console) -> None:
    console.print(Text("Slash commands:", style="bold"))
    for slash, description in slash_help_rows(terminal_only=True):
        console.print(f"  {slash:<12} {description}")


def _print_unknown_slash_notice(cmd: str, console: Console) -> None:
    """Unknown / unimplemented slashes get a one-line note instead of a crash."""

    console.print(
        Text(
            f"(slash {cmd} is not yet implemented in terminal-flow; "
            "use `openminion focus --rich` for the Textual shell with "
            "full slash support)",
            style=_MUTED_ITALIC_STYLE,
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
    arg = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
    if cmd == "/context":
        console.print(Text(render_context_report(runtime), style=_SYSTEM_STYLE))
    elif cmd == "/memory":
        console.print(Text(render_memory_report(runtime), style=_SYSTEM_STYLE))
    elif cmd == "/skills":
        console.print(Text(render_skills_report(runtime), style=_SYSTEM_STYLE))
    elif cmd == "/effort":
        console.print(
            Text(handle_effort_command(runtime, arg), style=_MUTED_ITALIC_STYLE)
        )
    elif cmd == "/statusline":
        console.print(
            Text(handle_statusline_command(runtime, arg), style=_MUTED_ITALIC_STYLE)
        )
        status_line.set_state(custom=statusline_label(runtime))
    elif cmd == "/undo":
        console.print(
            Text(
                handle_undo_command(runtime, arg, working_dir=working_dir),
                style=_MUTED_ITALIC_STYLE,
            )
        )


async def _handle_slash(
    text: str,
    *,
    runtime: Any,
    console: Console,
    transcript: TerminalTranscript,
    overlay: TerminalOverlayPresenter,
    status_line: TerminalStatusLine,
    working_dir: str,
) -> bool:
    """Dispatch a slash command and return whether the shell should exit."""

    cmd = text.split(maxsplit=1)[0]

    if cmd in ("/exit", "/quit"):
        return True
    if cmd in ("/", "/help"):
        _print_slash_help(console)
        return False
    if cmd == "/clear":
        transcript.clear_messages()
        return False
    if cmd == "/init":
        _run_init_command(
            runtime=runtime,
            console=console,
            overlay=overlay,
            working_dir=working_dir,
        )
        return False
    if cmd == "/new":
        _start_new_focus_session(
            runtime=runtime, console=console, transcript=transcript
        )
        return False
    if cmd == "/dashboard":
        await _open_dashboard_side_trip(
            runtime=runtime, console=console, transcript=transcript
        )
        return False
    if cmd == "/diff":
        _handle_slash_diff(
            text,
            transcript=transcript,
            console=console,
            working_dir=working_dir,
        )
        return False
    if cmd == "/expand":
        _handle_slash_expand(text, transcript=transcript, console=console)
        return False
    if cmd == "/sessions":
        _render_sessions_list(runtime=runtime, console=console)
        return False
    if cmd == "/resume":
        _resume_focus_session(
            runtime=runtime,
            console=console,
            transcript=transcript,
            overlay=overlay,
        )
        return False
    if cmd == "/status":
        _render_status_block(runtime=runtime, console=console, working_dir=working_dir)
        return False
    if cmd in ("/context", "/memory", "/skills", "/effort", "/statusline", "/undo"):
        _handle_visible_parity_slash(
            cmd,
            text,
            runtime=runtime,
            console=console,
            status_line=status_line,
            working_dir=working_dir,
        )
        return False
    if cmd == "/tools":
        _render_tools_list(runtime=runtime, console=console)
        return False
    if cmd == "/mcp":
        _render_mcp_status(runtime=runtime, console=console)
        return False
    if cmd == "/theme":
        _handle_slash_theme(text, console=console)
        return False
    if cmd == "/model":
        _handle_slash_model(text, runtime=runtime, console=console)
        return False
    if cmd == "/cost":
        _render_cost_snapshot(runtime=runtime, console=console)
        return False
    if cmd == "/agents":
        _handle_slash_agents(text, runtime=runtime, console=console)
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
    if cmd == "/queue":
        console.print(
            Text(
                "(/queue is handled by the focus input loop; use it from the focus prompt)",
                style=_MUTED_ITALIC_STYLE,
            )
        )
        return False
    if cmd in ("/quiet", "/verbose", "/normal"):
        _handle_slash_verbosity(cmd, transcript=transcript, console=console)
        return False
    if cmd == "/details":
        _handle_slash_details(text, transcript=transcript, console=console)
        return False
    if cmd == "/export":
        _handle_slash_export(runtime=runtime, console=console)
        return False
    if cmd == "/editor":
        _handle_slash_editor(console)
        return False
    _print_unknown_slash_notice(cmd, console)
    return False


def _start_new_focus_session(
    *,
    runtime: Any,
    console: Console,
    transcript: TerminalTranscript,
) -> None:
    creator = getattr(runtime, "create_new_session", None)
    if not callable(creator):
        console.print(
            Text(
                "(runtime does not expose create_new_session)",
                style=_MUTED_STYLE,
            )
        )
        return
    try:
        session_id = str(creator() or "").strip()
    except Exception as exc:
        console.print(Text(f"(could not start new session: {exc})", style=_ERR_STYLE))
        return
    transcript.clear_messages()
    if session_id:
        console.print(
            Text(f"(started new session: {session_id})", style=_MUTED_ITALIC_STYLE)
        )
    else:
        console.print(Text("(started new session)", style=_MUTED_ITALIC_STYLE))


def _resume_focus_session(
    *,
    runtime: Any,
    console: Console,
    transcript: TerminalTranscript,
    overlay: TerminalOverlayPresenter,
) -> None:
    lister = getattr(runtime, "list_directory_sessions", None)
    binder = getattr(runtime, "bind_session", None)
    history_getter = getattr(runtime, "get_current_history", None)
    if not callable(lister) or not callable(binder) or not callable(history_getter):
        console.print(
            Text(
                "(runtime does not expose resume session helpers)",
                style=_MUTED_STYLE,
            )
        )
        return
    try:
        sessions = list(lister(limit=50) or [])
    except Exception as exc:
        console.print(Text(f"(could not list sessions: {exc})", style=_ERR_STYLE))
        return
    non_empty = [
        item for item in sessions if int(getattr(item, "message_count", 0) or 0) > 0
    ]
    if not non_empty:
        console.print(
            Text(
                "No prior sessions with messages found in this directory. "
                "Use `/new` to start one.",
                style=_MUTED_ITALIC_STYLE,
            )
        )
        return
    chosen = overlay.present_resume_picker(non_empty)
    chosen_id = str(chosen or "").strip()
    if not chosen_id:
        return
    try:
        binder(chosen_id)
        history = list(history_getter() or [])
    except Exception as exc:
        console.print(Text(f"(could not resume session: {exc})", style=_ERR_STYLE))
        return
    transcript.set_messages(history)
    console.print(Text(f"(resumed session: {chosen_id})", style=_MUTED_ITALIC_STYLE))


async def _open_dashboard_side_trip(
    *,
    runtime: Any,
    console: Console,
    transcript: TerminalTranscript,
) -> None:
    """Launch the dashboard side trip without taking runtime ownership."""
    console.print(Text("launching dashboard…", style=_MUTED_STYLE))
    try:
        from openminion.cli.commands.tui import launch_dashboard
        from openminion.cli.tui.providers.runtime import OpenMinionRuntime
        from openminion.cli.parser.contracts import ProviderBundle

        base_runtime = getattr(runtime, "api_runtime", runtime)
        tui_runtime = OpenMinionRuntime(base_runtime, prompt_on_resume=True)
        bundle = ProviderBundle.from_api_runtime(base_runtime)
        launch_dashboard(
            app_runtime=tui_runtime,
            providers=bundle,
            owns_runtime=False,
        )
    except Exception as exc:
        transcript.push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=(
                    f"Dashboard side-trip not available in this context "
                    f"({exc}). Use `openminion tui` from another terminal "
                    "for the full dashboard view."
                ),
            )
        )


async def _run_shell_escape(
    *,
    command: str,
    console: Console,
    transcript: TerminalTranscript,
    working_dir: str,
) -> None:
    """FNS-09 parity: `!cmd` runs via subprocess, output as tool block."""
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


def _push_greeter(console: Console, *, runtime: Any, working_dir: str) -> None:
    """Print the terminal-focus greeter panel."""
    from openminion import __version__
    from openminion.cli.tui.presentation.header import shorten_working_dir
    from rich.panel import Panel

    agent = str(getattr(runtime, "agent_id", "openminion") or "openminion")
    model = _runtime_label(runtime)
    cwd_label = shorten_working_dir(working_dir) or working_dir or "."
    body_lines = [
        Text.assemble(
            ("OpenMinion Focus", token_rich_style(StyleToken.INFO, bold=True)),
            ("  ", ""),
            (f"(v{__version__})", _MUTED_STYLE),
        ),
        Text.assemble(
            ("terminal flow", _SYSTEM_STYLE),
            ("  ·  ", _MUTED_STYLE),
            ("type-ahead queue enabled", _MUTED_STYLE),
        ),
        Text.assemble(
            ("", ""),
        ),
        Text.assemble(
            ("model:      ", _MUTED_STYLE),
            (model, _SYSTEM_STYLE),
        ),
        Text.assemble(
            ("directory:  ", _MUTED_STYLE),
            (cwd_label, _SYSTEM_STYLE),
        ),
        Text.assemble(
            ("agent:      ", _MUTED_STYLE),
            (agent, _SYSTEM_STYLE),
        ),
    ]
    project_context = getattr(runtime, "project_context", None)
    if project_context is not None:
        context_bits = [
            ("context:    ", _MUTED_STYLE),
            (f"{project_context.display_name}", _SYSTEM_STYLE),
            ("  ", ""),
            (f"({project_context.size_bytes} bytes)", _MUTED_STYLE),
        ]
        body_lines.append(Text.assemble(*context_bits))
    panel_body = Text("\n").join(body_lines)
    console.print()
    console.print(
        Panel(
            panel_body,
            border_style="dim",
            padding=(0, 1),
            expand=False,
        )
    )
    console.print(
        Text(
            "Tip: / for commands · @ to mention a file · keep typing while a turn runs",
            style=_MUTED_ITALIC_STYLE,
        )
    )
    if project_context is not None and not bool(
        getattr(project_context, "is_canonical_name", False)
    ):
        console.print(
            Text(
                f"found {project_context.display_name}; consider renaming to OPENMINION.md for canonical support",
                style=_MUTED_ITALIC_STYLE,
            )
        )
    console.print()


def _run_init_command(
    *,
    runtime: Any,
    console: Console,
    overlay: TerminalOverlayPresenter,
    working_dir: str,
) -> None:
    agent_id = (
        str(getattr(runtime, "agent_id", "") or "openminion").strip() or "openminion"
    )
    target_preview = str(
        find_project_context_target_root(working_dir) / "OPENMINION.md"
    )
    decision = overlay.present_approval(
        f"Create OPENMINION.md for this project?\nPath: {target_preview}"
    )
    if decision not in ("allow", "always"):
        console.print(Text("(init cancelled)", style=_MUTED_ITALIC_STYLE))
        return
    try:
        target_path = write_init_template(working_dir=working_dir, agent_id=agent_id)
    except FileExistsError as exc:
        console.print(
            Text(
                f"(project context file already exists: {exc})",
                style=_MUTED_ITALIC_STYLE,
            )
        )
        return
    except (OSError, TypeError, ValueError) as exc:
        console.print(Text(f"(could not write OPENMINION.md: {exc})", style=_ERR_STYLE))
        return
    setter = getattr(runtime, "set_project_context", None)
    if callable(setter):
        try:
            from openminion.cli.tui.project_context import resolve_project_context

            setter(resolve_project_context(working_dir))
        except (AttributeError, OSError, TypeError, ValueError):
            pass
    console.print(Text(f"(wrote {target_path})", style=_MUTED_ITALIC_STYLE))


def _copy_to_clipboard(text: str) -> bool:
    """Write text to the first available clipboard backend."""
    import subprocess

    candidates: tuple[tuple[str, ...], ...] = (
        ("pbcopy",),
        ("wl-copy",),
        ("xclip", "-selection", "clipboard"),
        ("xsel", "--clipboard", "--input"),
        ("clip.exe",),
    )
    payload = text.encode("utf-8", errors="replace")
    for cmd in candidates:
        try:
            proc = subprocess.run(
                cmd,
                input=payload,
                capture_output=True,
                timeout=3,
                check=False,
            )
        except (FileNotFoundError, OSError):
            continue
        except Exception:
            continue
        if proc.returncode == 0:
            return True
    return False
