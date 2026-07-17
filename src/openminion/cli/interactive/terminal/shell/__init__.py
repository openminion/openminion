from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
import logging
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
from openminion.cli.presentation.models import (
    ChatMessage,
    MessageKind,
)
from openminion.cli.presentation.queue import (
    is_queue_command,
    queue_preserved_after_interrupt_notice,
    queue_run_next_empty_notice,
    queue_run_next_notice,
    queued_message_notice,
)
from openminion.modules.runtime.sync import run_async_compat
from openminion.modules.telemetry.trace.phase_timing import mark_active_chat_first_text

from ..composer import TerminalComposer
from ..overlays import TerminalOverlayPresenter
from ..prompt_output import build_prompt_safe_terminal_writer
from ..status_line import TerminalStatusLine
from ..transcript import TerminalTranscript
from .labels import _runtime_label
from .approval import (
    build_terminal_approval_callback as _build_terminal_approval_callback,
)
from .progress import (
    normalize_progress_kind as _normalize_progress_kind,
    tick_turn_status_line as _tick_turn_status_line,
)
from .queue_control import apply_queue_command
from .startup import (
    cancel_startup_notice as _cancel_startup_notice,
    schedule_startup_notice as _schedule_startup_notice,
)
from .actions import (
    _copy_to_clipboard,
    _handle_slash,
    _push_greeter,
    _run_shell_escape,
    _runtime_permission_mode,
    _cycle_permission_mode,
    _SLASH_COMMANDS,
)
from .sessions import show_dashboard_retirement
from .renderers import (
    _render_cost_snapshot as _render_cost_snapshot,
    _render_mcp_status as _render_mcp_status,
    _render_model_status as _render_model_status,
    _render_sessions_list as _render_sessions_list,
    _render_status_block as _render_status_block,
    _render_tools_list as _render_tools_list,
)
from openminion.cli.presentation.styles import StyleToken
from openminion.cli.presentation.markers import token_rich_style
from openminion.cli.presentation.slash_commands import slash_help_rows
from openminion.cli.presentation.visible_parity import statusline_label

_open_dashboard_side_trip = show_dashboard_retirement

_LOGGER = logging.getLogger(__name__)
_ERR_STYLE = token_rich_style(StyleToken.ERROR)
_INFO_STYLE = token_rich_style(StyleToken.INFO)
_INFO_BOLD_STYLE = token_rich_style(StyleToken.INFO, bold=True)
_MUTED_STYLE = token_rich_style(StyleToken.MUTED)
_MUTED_ITALIC_STYLE = f"italic {_MUTED_STYLE}" if _MUTED_STYLE else "italic"
_SYSTEM_STYLE = token_rich_style(StyleToken.SYSTEM)
_ESCAPE_BYTE = b"\x1b"
_TYPEAHEAD_REOPEN_DELAY_SECONDS = 0.05
_PROMPT_REPLAY_DEDUP_WINDOW_SECONDS = 0.35
_TYPEAHEAD_PROMPT_GAP_LINES = 1


@dataclass(frozen=True)
class _EscapeInterruptWatcher:
    stop: Callable[[], None]
    interrupted: Callable[[], bool]


async def _confirm_terminal_exit(
    *, console: Console, overlay: TerminalOverlayPresenter
) -> bool:
    should_exit = await overlay.present_confirm_async("Exit the interactive CLI?")
    if not should_exit:
        console.print(Text("(exit cancelled)", style=_MUTED_ITALIC_STYLE))
    return should_exit


def _discover_custom_commands_for(*, runtime: Any, working_dir: str) -> dict[str, Any]:
    """Scan project and user-global dirs for custom slash commands."""
    from openminion.cli.presentation.custom_commands import (
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
    try:
        return run_async_compat(
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
    except KeyboardInterrupt:
        return 0


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
    approval_grants: set[str] | None = None,
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
        from openminion.cli.presentation.custom_commands import render_command

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
            approval_callback=_build_terminal_approval_callback(
                overlay=overlay,
                session_grants=approval_grants
                if approval_grants is not None
                else set(),
            ),
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


class _TerminalFocusLoop:
    def __init__(
        self,
        *,
        runtime: Any,
        console: Console,
        transcript: TerminalTranscript,
        status_line: TerminalStatusLine,
        composer: TerminalComposer,
        overlay: TerminalOverlayPresenter,
        working_dir: str,
        custom_commands: dict[str, Any],
        approval_grants: set[str],
    ) -> None:
        self.runtime = runtime
        self.console = console
        self.transcript = transcript
        self.status_line = status_line
        self.composer = composer
        self.overlay = overlay
        self.working_dir = working_dir
        self.custom_commands = custom_commands
        self.approval_grants = approval_grants
        self.pending_turns: deque[str] = deque()
        self.active_turn_task: asyncio.Task[None] | None = None
        self.read_task: asyncio.Task[str] | None = None
        self.exit_after_turn = False
        self.turn_cancel_requested = False
        self.queue_auto_drain_paused = False
        self.run_next_after_interrupt = False
        self.active_turn_text = ""
        self.active_turn_started_at = 0.0

    def refresh_status_line(self, *, state: str = "idle") -> None:
        self.status_line.set_state(
            agent=str(getattr(self.runtime, "agent_id", "") or ""),
            cwd=self.working_dir,
            model=_runtime_label(self.runtime),
            permission_mode=_runtime_permission_mode(self.runtime),
            custom=statusline_label(self.runtime),
            queued_count=len(self.pending_turns),
            state=state,
        )

    def start_read_task(
        self,
        *,
        delay_seconds: float = 0.0,
        leading_blank_lines: int = 0,
    ) -> None:
        if self.read_task is not None and not self.read_task.done():
            return
        if delay_seconds <= 0:
            self.read_task = asyncio.create_task(
                self._read_line_with_prompt_gap(leading_blank_lines)
            )
            return
        self.read_task = asyncio.create_task(
            self._read_line_after_delay(delay_seconds, leading_blank_lines)
        )

    async def _read_line_after_delay(
        self, delay_seconds: float, leading_blank_lines: int
    ) -> str:
        await asyncio.sleep(delay_seconds)
        return await self._read_line_with_prompt_gap(leading_blank_lines)

    async def _read_line_with_prompt_gap(self, leading_blank_lines: int) -> str:
        for _ in range(max(0, int(leading_blank_lines))):
            self.console.print()
        return await self.composer.read_line()

    async def cancel_read_task(self) -> None:
        task = self.read_task
        self.read_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, EOFError, KeyboardInterrupt):
            pass

    def request_turn_interrupt(self) -> None:
        if self.active_turn_task is None or self.active_turn_task.done():
            return
        self.turn_cancel_requested = True
        self.active_turn_task.cancel()

    def _push_system_message(self, body: str) -> None:
        self.transcript.push_message(
            ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=body)
        )

    async def handle_queue_command(self, text: str) -> None:
        result = apply_queue_command(text, self.pending_turns)
        if result.kind == "run_next":
            await self._run_next_queued_turn()
            return
        if result.reset_pause:
            self.queue_auto_drain_paused = False
            self.run_next_after_interrupt = False
        self._push_system_message(result.message)
        self.refresh_status_line(
            state="responding" if self.active_turn_task is not None else "idle"
        )

    async def _run_next_queued_turn(self) -> None:
        if not self.pending_turns:
            self._push_system_message(queue_run_next_empty_notice())
            return
        if self.active_turn_task is not None and not self.active_turn_task.done():
            self.queue_auto_drain_paused = False
            self.run_next_after_interrupt = True
            self.turn_cancel_requested = True
            self.active_turn_task.cancel()
            return
        next_text = self.pending_turns.popleft()
        self.queue_auto_drain_paused = False
        self._push_system_message(queue_run_next_notice(next_text))
        self.refresh_status_line(state="responding")
        await self.start_turn(next_text)

    async def start_turn(self, text: str) -> None:
        self.active_turn_text = str(text or "")
        self.active_turn_started_at = asyncio.get_running_loop().time()
        self.active_turn_task = asyncio.create_task(
            _run_agent_turn(
                text=text,
                runtime=self.runtime,
                transcript=self.transcript,
                status_line=self.status_line,
                approval_callback=_build_terminal_approval_callback(
                    overlay=self.overlay,
                    session_grants=self.approval_grants,
                    pause_prompt=self.cancel_read_task,
                    resume_prompt=self.start_read_task,
                ),
                invalidate_prompt=getattr(self.composer, "invalidate", None),
            )
        )
        if callable(getattr(self.composer, "set_busy", None)):
            self.composer.set_busy(True)
        self.refresh_status_line(state="responding")
        self.start_read_task(
            delay_seconds=_TYPEAHEAD_REOPEN_DELAY_SECONDS,
            leading_blank_lines=_TYPEAHEAD_PROMPT_GAP_LINES,
        )

    async def handle_busy_input(self, text: str) -> None:
        if (
            text == self.active_turn_text
            and asyncio.get_running_loop().time() - self.active_turn_started_at
            <= _PROMPT_REPLAY_DEDUP_WINDOW_SECONDS
        ):
            return
        if text in ("/exit", "/quit"):
            self.exit_after_turn = True
            self._push_system_message("Exit requested after the current turn finishes.")
            return
        if is_queue_command(text):
            await self.handle_queue_command(text)
            return
        if text.startswith("/") or text.startswith("!"):
            self._push_system_message(
                "Commands are unavailable while a turn is running. "
                "Use `/queue` for queued prompts, wait for the reply, "
                "or press Esc to interrupt."
            )
            return
        self.pending_turns.append(text)
        self.transcript.push_message(
            ChatMessage(kind=MessageKind.USER, sender="you", body=text),
            render=False,
        )
        self.queue_auto_drain_paused = False
        self._push_system_message(queued_message_notice(len(self.pending_turns)))
        self.refresh_status_line(state="responding")

    async def handle_turn_completion(self) -> int | None:
        finished_turn = self.active_turn_task
        self.active_turn_task = None
        if finished_turn is None:
            return None
        try:
            await finished_turn
        except asyncio.CancelledError:
            if self.turn_cancel_requested:
                if self.pending_turns and not self.run_next_after_interrupt:
                    self.queue_auto_drain_paused = True
                    self._push_system_message(
                        queue_preserved_after_interrupt_notice(len(self.pending_turns))
                    )
                else:
                    self._push_system_message("Interrupted current turn.")
            else:
                raise
        finally:
            self.turn_cancel_requested = False
            run_next = self.run_next_after_interrupt
            self.run_next_after_interrupt = False
            queued_next = (
                bool(self.pending_turns)
                and not self.exit_after_turn
                and not self.queue_auto_drain_paused
            )
            if callable(getattr(self.composer, "set_busy", None)) and not queued_next:
                self.composer.set_busy(False)
            self.refresh_status_line(state="responding" if queued_next else "idle")
        if self.exit_after_turn:
            self.console.print(Text("(exit)", style=_MUTED_STYLE))
            return 0
        if run_next and self.pending_turns:
            next_text = self.pending_turns.popleft()
            self._push_system_message(queue_run_next_notice(next_text))
            self.refresh_status_line()
            await self.start_turn(next_text)
        elif self.pending_turns and not self.queue_auto_drain_paused:
            next_text = self.pending_turns.popleft()
            self._push_system_message(queue_run_next_notice(next_text))
            self.refresh_status_line()
            await self.start_turn(next_text)
        return None

    async def handle_idle_input(self, text: str) -> int | None:
        if text in ("/exit", "/quit"):
            return 0
        try:
            if is_queue_command(text):
                await self.handle_queue_command(text)
                self.start_read_task()
                return None
            if text.startswith("/"):
                should_exit = await _handle_slash_input(
                    text,
                    runtime=self.runtime,
                    console=self.console,
                    transcript=self.transcript,
                    overlay=self.overlay,
                    status_line=self.status_line,
                    working_dir=self.working_dir,
                    custom_commands=self.custom_commands,
                    approval_grants=self.approval_grants,
                )
                if should_exit:
                    return 0
                self.start_read_task()
                return None
            if text.startswith("!"):
                await _run_shell_escape(
                    command=text[1:].strip(),
                    console=self.console,
                    transcript=self.transcript,
                    working_dir=self.working_dir,
                )
                self.start_read_task()
                return None
            self.transcript.push_message(
                ChatMessage(kind=MessageKind.USER, sender="you", body=text),
                render=False,
            )
            await self.start_turn(text)
        except KeyboardInterrupt:
            if await _confirm_terminal_exit(console=self.console, overlay=self.overlay):
                self.console.print(Text("(exit)", style=_MUTED_STYLE))
                return 0
            self.start_read_task()
        return None

    async def handle_read_completion(self) -> int | None:
        finished_read = self.read_task
        self.read_task = None
        if finished_read is None:
            return None
        try:
            text = await finished_read
        except EOFError:
            if self.active_turn_task is not None:
                self.exit_after_turn = True
                self.transcript.push_message(
                    ChatMessage(
                        kind=MessageKind.SYSTEM,
                        sender="system",
                        body="Exit requested after the current turn finishes.",
                    )
                )
                return None
            self.console.print(Text("(exit)", style=_MUTED_STYLE))
            return 0
        except KeyboardInterrupt:
            if await _confirm_terminal_exit(console=self.console, overlay=self.overlay):
                self.console.print(Text("(exit)", style=_MUTED_STYLE))
                return 0
            self.start_read_task()
            return None
        text = (text or "").strip()
        if not text:
            self.start_read_task(
                leading_blank_lines=_TYPEAHEAD_PROMPT_GAP_LINES
                if self.active_turn_task is not None
                else 0
            )
            return None
        if self.active_turn_task is not None:
            await self.handle_busy_input(text)
            self.start_read_task(leading_blank_lines=_TYPEAHEAD_PROMPT_GAP_LINES)
            return None
        return await self.handle_idle_input(text)

    async def run(self) -> int:
        self.refresh_status_line()
        self.start_read_task()
        while True:
            wait_set = {
                task
                for task in (self.read_task, self.active_turn_task)
                if task is not None
            }
            if not wait_set:
                return 0
            done, _ = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)
            turn_done = (
                self.active_turn_task is not None and self.active_turn_task in done
            )
            read_done = self.read_task is not None and self.read_task in done
            if turn_done:
                result = await self.handle_turn_completion()
                if result is not None:
                    return result
            if read_done:
                result = await self.handle_read_completion()
                if result is not None:
                    return result


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
        on_escape=lambda: None,
        working_dir=working_dir,
    )
    transcript.set_terminal_writer(
        build_prompt_safe_terminal_writer(
            console=console,
            prompt_session=composer.prompt_session,
        )
    )
    overlay = TerminalOverlayPresenter(
        console=console,
        prompt_session=composer.prompt_session,
    )
    approval_grants: set[str] = set()
    _push_greeter(console, runtime=runtime, working_dir=working_dir)
    startup_notice_task = _schedule_startup_notice(
        startup_notice,
        transcript=transcript,
    )
    loop = _TerminalFocusLoop(
        runtime=runtime,
        console=console,
        transcript=transcript,
        status_line=status_line,
        composer=composer,
        overlay=overlay,
        working_dir=working_dir,
        custom_commands=custom_commands,
        approval_grants=approval_grants,
    )
    composer._on_escape = loop.request_turn_interrupt
    try:
        return await loop.run()
    finally:
        await loop.cancel_read_task()
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
        _LOGGER.debug("activity event routing failed", exc_info=True)
    return False


def _build_turn_progress_callback(
    *,
    transcript: TerminalTranscript,
    handle: Any | None = None,
    status_controller: Any | None = None,
    status_line: TerminalStatusLine | None = None,
    invalidate_prompt: Callable[[], None] | None = None,
):
    """Build the progress callback passed to ``runtime.send_message``."""

    def _set_turn_status(label: str) -> None:
        if status_line is None:
            return
        status_line.set_state(state="responding", turn_status=label)
        if callable(invalidate_prompt):
            invalidate_prompt()

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
            _set_turn_status(label)

    return _handle_progress


def _finalize_turn_status_line(runtime: Any, status_line: TerminalStatusLine) -> None:
    """Clear transient turn progress without mutating the persistent footer row."""

    status_line.set_state(
        state="idle",
        permission_mode=_runtime_permission_mode(runtime),
        custom=statusline_label(runtime),
        turn_status="",
    )


async def _run_interruptible_agent_turn(
    *,
    text: str,
    runtime: Any,
    transcript: TerminalTranscript,
    status_line: TerminalStatusLine | None,
    approval_callback: Callable[[str, dict[str, Any], Any], Any] | None = None,
    invalidate_prompt: Callable[[], None] | None = None,
) -> None:
    """Run one agent turn and let Escape cancel it in the terminal CLI."""

    turn_task = asyncio.create_task(
        _run_agent_turn(
            text=text,
            runtime=runtime,
            transcript=transcript,
            status_line=status_line,
            approval_callback=approval_callback,
            invalidate_prompt=invalidate_prompt,
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
    approval_callback: Callable[[str, dict[str, Any], Any], Any] | None = None,
    invalidate_prompt: Callable[[], None] | None = None,
) -> None:
    """Stream tokens through the transcript turn handle."""

    if status_line is not None:
        status_line.set_state(state="responding", elapsed_seconds=0.0, turn_status="")
    status_tick_task: asyncio.Task[None] | None = None
    if status_line is not None:
        status_tick_task = asyncio.create_task(
            _tick_turn_status_line(
                status_line=status_line,
                invalidate_prompt=invalidate_prompt,
            )
        )
    handle = transcript.begin_turn(
        role="assistant",
        footer_provider=status_line.live_turn_footer
        if status_line is not None
        else None,
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
    if status_line is not None:
        status_line.set_state(state="responding", turn_status=initial_label)
    if callable(invalidate_prompt):
        invalidate_prompt()
    try:
        progress_callback = _build_turn_progress_callback(
            transcript=transcript,
            handle=handle,
            status_controller=status_controller,
            status_line=status_line,
            invalidate_prompt=invalidate_prompt,
        )
        send_kwargs: dict[str, Any] = {"progress_callback": progress_callback}
        if approval_callback is not None:
            send_kwargs["approval_callback"] = approval_callback
        async for chunk in runtime.send_message(text, **send_kwargs):
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
            _LOGGER.debug("turn handle cancellation cleanup failed", exc_info=True)
        raise
    except Exception as exc:
        try:
            handle.complete(final_text=reply)
        except Exception:
            _LOGGER.debug("turn handle failure cleanup failed", exc_info=True)
        transcript.push_message(
            ChatMessage(kind=MessageKind.ERROR, sender="error", body=str(exc))
        )
    finally:
        if status_tick_task is not None:
            status_tick_task.cancel()
            try:
                await status_tick_task
            except asyncio.CancelledError:
                pass
        status_controller.end_turn()
        if status_line is not None:
            _finalize_turn_status_line(runtime, status_line)
