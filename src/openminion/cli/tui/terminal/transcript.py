from __future__ import annotations

from typing import Any, Callable, Iterable, Literal

from rich.console import Console
from rich.text import Text

from openminion.cli.presentation.styles import StyleToken
from openminion.cli.tui.presentation.markers import token_rich_style
from openminion.cli.tui.presentation.models import ChatMessage, MessageKind
from openminion.cli.tui.presentation.messages import (
    render_body,
    render_error_text,
    render_system_text,
    render_user_text,
)

from .streaming import (
    TerminalTurnHandle,
    _TOOL_BLOCK_VERBOSE_MAX_LINES,
    _body_line_count,
    _render_in_progress_tool_block,
    _render_full_tool_block,
    _render_tool_block,
    is_truncated,
)

_ERROR_STYLE = token_rich_style(StyleToken.ERROR)


def get_app_or_none() -> Any | None:
    try:
        import importlib

        context = importlib.import_module("textual._context")
        active_app = getattr(context, "active_app", None)
        if active_app is None:
            return None
        return active_app.get(None)
    except (ImportError, LookupError, RuntimeError, AttributeError):
        return None


def run_in_terminal(func: Callable[[], None], *, render_cli_done: bool = False) -> Any:
    app = get_app_or_none()
    runner = getattr(app, "run_in_terminal", None)
    if callable(runner):
        return runner(func, render_cli_done=render_cli_done)
    func()
    return None


class TerminalTranscript:
    def __init__(
        self,
        console: Console,
        *,
        plain_spinner: bool = False,
        verbosity: str = "normal",
        show_response_time: bool = True,
    ) -> None:
        self._console = console
        self._messages: list[ChatMessage] = []
        self._selected_message_id: str | None = None
        self._plain_spinner = bool(plain_spinner)
        self._show_response_time = bool(show_response_time)
        self._verbosity: str = (
            verbosity if verbosity in ("quiet", "normal", "verbose") else "normal"
        )
        self._hidden_tool_count: int = 0
        self._hidden_failed_count: int = 0
        self._truncated_blocks: list[Any] = []
        self._live_narrated_call_ids: set[str] = set()
        self._active_handle: Any | None = None
        self._terminal_writer: Callable[[Callable[[], None]], Any] | None = None

    def set_terminal_writer(self, writer: Callable[[Callable[[], None]], Any]) -> None:
        self._terminal_writer = writer

    def _write_render(self, render: Callable[[], None]) -> None:
        writer = self._terminal_writer
        if writer is not None:
            writer(render)
            return
        app = get_app_or_none()
        if bool(getattr(app, "is_running", False)):
            run_in_terminal(render, render_cli_done=False)
            return
        render()

    def begin_turn(
        self,
        role: Literal["user", "assistant"] = "assistant",
        *,
        footer_provider: Callable[[], str] | None = None,
    ) -> TerminalTurnHandle:
        kind = MessageKind.AGENT if role == "assistant" else MessageKind.USER
        message = ChatMessage(kind=kind, sender=role, body="")
        self._messages.append(message)
        self._selected_message_id = message.msg_id
        handle = TerminalTurnHandle(
            self._console,
            plain=self._plain_spinner,
            footer_provider=footer_provider,
            show_response_time=self._show_response_time,
        )
        if self._terminal_writer is not None:
            handle.set_terminal_writer(self._terminal_writer)
        handle.start()
        self._active_handle = handle
        original_complete = handle.complete

        def _complete(final_text: str | None = None) -> None:
            if final_text is not None:
                message.body = final_text
            else:
                message.body = handle._buffer  # type: ignore[attr-defined]
            original_complete(final_text)
            self._active_handle = None
            self._maybe_print_hidden_tool_summary()

        handle.complete = _complete  # type: ignore[method-assign]
        return handle

    def push_message(self, message: ChatMessage, *, render: bool = True):
        if message.kind == MessageKind.USER:
            self._hidden_tool_count = 0
            self._hidden_failed_count = 0
        self._messages.append(message)
        self._selected_message_id = message.msg_id
        if render:
            self._render(message)
        if render and message.kind == MessageKind.AGENT:
            self._maybe_print_hidden_tool_summary()

    def _maybe_print_hidden_tool_summary(self) -> None:
        if self._hidden_tool_count <= 0:
            return
        n = self._hidden_tool_count
        failed = self._hidden_failed_count
        noun = "tool call" if n == 1 else "tool calls"
        if failed > 0:
            line = (
                f"({n} {noun} hidden — {failed} failed; "
                "/verbose to show, /expand 0 to list)"
            )
        else:
            line = f"({n} {noun} hidden — /verbose to show, /expand 0 to list)"
        self._console.print(Text(line, style="dim italic"))
        self._hidden_tool_count = 0
        self._hidden_failed_count = 0

    def set_messages(self, messages: list[ChatMessage]) -> None:
        self.reset_session_state()
        self._messages = []
        self._selected_message_id = None
        for msg in messages:
            self.push_message(msg)

    def clear_messages(self) -> None:
        self.reset_session_state()
        self._messages = []
        self._selected_message_id = None
        self._console.print(Text("─" * 60, style="dim"))

    def reset_session_state(self) -> None:
        self._hidden_tool_count = 0
        self._hidden_failed_count = 0
        self._truncated_blocks = []
        self._live_narrated_call_ids = set()

    def filter_messages(self, query: str) -> None:
        if query:
            self._console.print(
                Text(
                    f"(filter: '{query}' — use your terminal's native "
                    f"search instead; terminal-flow scrollback is "
                    f"searchable directly)",
                    style="dim italic",
                )
            )

    def copy_selected_message(self) -> str | None:
        if self._selected_message_id is None:
            return None
        for msg in self._messages:
            if msg.msg_id == self._selected_message_id:
                return _copyable_text(msg)
        return None

    def copy_last_copyable_message(self) -> str | None:
        for msg in reversed(self._messages):
            text = _copyable_text(msg)
            if text:
                return text
        return None

    def drop_message(self, msg_id: str) -> bool:
        if not msg_id:
            return False
        before = len(self._messages)
        self._messages = [m for m in self._messages if m.msg_id != msg_id]
        if len(self._messages) == before:
            return False
        if self._selected_message_id == msg_id:
            self._selected_message_id = (
                self._messages[-1].msg_id if self._messages else None
            )
        return True

    def _render(self, message: ChatMessage) -> None:
        if message.kind == MessageKind.USER:
            self._write_render(
                lambda: self._console.print(render_user_text(message.body or ""))
            )
            return
        if message.kind == MessageKind.AGENT:
            body = str(message.body or "")
            self._write_render(lambda: self._console.print(render_body(body)))
            return
        if message.kind == MessageKind.SYSTEM:
            self._write_render(
                lambda: self._console.print(render_system_text(message.body or ""))
            )
            return
        if message.kind == MessageKind.ERROR:
            self._write_render(
                lambda: self._console.print(render_error_text(message.body or ""))
            )
            return
        if message.kind == MessageKind.TOOL and message.tool_event is not None:
            call_id = getattr(message.tool_event, "call_id", "")
            if call_id and call_id in self._live_narrated_call_ids:
                return
            if self._verbosity == "quiet":
                self._hidden_tool_count += 1
                exit_code = getattr(message.tool_event, "exit_code", None)
                if exit_code is not None and exit_code != 0:
                    self._hidden_failed_count += 1
                self._truncated_blocks.append(message.tool_event)
                return
            if self._verbosity == "verbose":
                self._console.print(
                    _render_full_tool_block(
                        message.tool_event,
                        cap=_TOOL_BLOCK_VERBOSE_MAX_LINES,
                    )
                )
                if _body_line_count(message.tool_event) > _TOOL_BLOCK_VERBOSE_MAX_LINES:
                    self._truncated_blocks.append(message.tool_event)
                return
            self._console.print(_render_tool_block(message.tool_event))
            if is_truncated(message.tool_event):
                self._truncated_blocks.append(message.tool_event)
            return
        self._console.print(Text(message.body or ""))

    def set_verbosity(self, level: str) -> None:
        self._verbosity = level if level in ("quiet", "normal", "verbose") else "normal"

    def handle_tool_started(self, payload: dict[str, Any]) -> None:
        import time as _time

        call_id = str(payload.get("call_id") or payload.get("id") or "").strip()
        tool_name = str(
            payload.get("tool_name") or payload.get("name") or payload.get("tool") or ""
        ).strip()
        args = payload.get("args") or payload.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        if call_id and call_id in self._live_narrated_call_ids:
            return
        if self._verbosity == "quiet":
            self._hidden_tool_count += 1
            if call_id:
                self._live_narrated_call_ids.add(call_id)
            return
        renderable = _render_in_progress_tool_block(tool_name, args)
        handle = self._active_handle
        if handle is not None and hasattr(handle, "set_active_tool"):
            try:
                handle.set_active_tool(
                    call_id=call_id or tool_name or "tool",
                    tool_name=tool_name,
                    args=args,
                    started_at=_time.monotonic(),
                )
                if not self._append_live_renderable(renderable):
                    self._write_render(lambda: self._console.print(renderable))
                if call_id:
                    self._live_narrated_call_ids.add(call_id)
                return
            except (AttributeError, RuntimeError, ValueError):
                pass
        self._console.print(renderable)
        if call_id:
            self._live_narrated_call_ids.add(call_id)

    def handle_tool_completed(self, payload: dict[str, Any]) -> None:
        from openminion.cli.tui.presentation.models import ToolEvent

        call_id = str(payload.get("call_id") or payload.get("id") or "").strip()
        tool_name = str(
            payload.get("tool_name") or payload.get("name") or payload.get("tool") or ""
        ).strip()
        args = payload.get("args") or payload.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        content = str(payload.get("content") or payload.get("result") or "")
        exit_code = payload.get("exit_code")
        if exit_code is None:
            exit_code = 0 if payload.get("ok", True) else 1
        try:
            exit_code = int(exit_code)
        except (TypeError, ValueError):
            exit_code = None
        duration_ms = payload.get("duration_ms")
        try:
            duration_ms = int(duration_ms) if duration_ms is not None else None
        except (TypeError, ValueError):
            duration_ms = None

        event = ToolEvent(
            tool_name=tool_name,
            args=args,
            content=content,
            full_content=content,
            exit_code=exit_code,
            duration_ms=duration_ms,
            call_id=call_id,
        )

        handle = self._active_handle
        if handle is not None and hasattr(handle, "clear_active_tool"):
            try:
                handle.clear_active_tool(call_id=call_id)
            except (AttributeError, RuntimeError, ValueError):
                pass

        if self._verbosity == "quiet":
            if exit_code is not None and exit_code != 0:
                self._hidden_failed_count += 1
            if call_id:
                self._live_narrated_call_ids.add(call_id)
            return

        if self._verbosity == "verbose":
            renderable = _render_full_tool_block(
                event, cap=_TOOL_BLOCK_VERBOSE_MAX_LINES
            )
            if not self._append_live_renderable(renderable):
                self._write_render(lambda: self._console.print(renderable))
            if _body_line_count(event) > _TOOL_BLOCK_VERBOSE_MAX_LINES:
                self._truncated_blocks.append(event)
            if call_id:
                self._live_narrated_call_ids.add(call_id)
            return

        renderable = _render_tool_block(event)
        if not self._append_live_renderable(renderable):
            self._write_render(lambda: self._console.print(renderable))
        if is_truncated(event):
            self._truncated_blocks.append(event)
        if call_id:
            self._live_narrated_call_ids.add(call_id)

    def _append_live_renderable(self, renderable: Any) -> bool:
        handle = self._active_handle
        append = getattr(handle, "append_renderable", None)
        if not callable(append):
            return False
        try:
            append(renderable)
        except (AttributeError, RuntimeError, ValueError):
            return False
        return True

    def push_activity_event(self, event: Any) -> None:
        from openminion.cli.status.activity_ledger import (
            KIND_APPROVAL,
            KIND_BACKGROUND,
            KIND_BUDGET,
            KIND_ERROR,
            KIND_PLAN,
            KIND_SEARCH,
            KIND_STATUS,
            KIND_SUMMARY,
            KIND_TOOL,
            STATE_COMPLETED,
            STATE_DENIED,
            format_activity_line,
        )

        if event is None:
            return
        kind = getattr(event, "kind", "")
        if kind in {KIND_TOOL, KIND_SEARCH}:
            return
        line = format_activity_line(event)
        if not line:
            return
        if kind == KIND_ERROR:
            style = token_rich_style(StyleToken.ERROR)
        elif kind == KIND_APPROVAL:
            state = getattr(event, "state", "")
            if state == STATE_DENIED:
                style = token_rich_style(StyleToken.ERROR)
            elif state == STATE_COMPLETED:
                style = token_rich_style(StyleToken.SUCCESS)
            else:
                style = token_rich_style(StyleToken.WARNING)
        elif kind == KIND_BUDGET:
            style = token_rich_style(StyleToken.MUTED)
        elif kind in {KIND_PLAN, KIND_BACKGROUND, KIND_STATUS, KIND_SUMMARY}:
            style = token_rich_style(StyleToken.SYSTEM)
        else:
            style = token_rich_style(StyleToken.SYSTEM)
        renderable = Text(line, style=style or "")
        if self._append_live_renderable(renderable):
            return
        try:
            self._console.print(renderable)
        except Exception:
            return

    def expand_block(self, index: int = 1) -> bool:
        if not self._truncated_blocks:
            self._console.print(
                Text("(no truncated tool blocks to expand)", style="dim italic")
            )
            return False
        if index == 0:
            self._console.print(Text("Truncated tool blocks:", style="bold"))
            for i, event in enumerate(reversed(self._truncated_blocks), start=1):
                verb = event.tool_name or "tool"
                first_line = (
                    (event.full_content or event.content or "").strip().split("\n")[0]
                )
                preview = first_line[:50] + ("…" if len(first_line) > 50 else "")
                self._console.print(f"  {i}. {verb} — {preview}")
            return True
        if index < 1 or index > len(self._truncated_blocks):
            self._console.print(
                Text(
                    f"(no truncated block at index {index})",
                    style=_ERROR_STYLE,
                )
            )
            return False
        event = list(reversed(self._truncated_blocks))[index - 1]
        self._console.print(_render_full_tool_block(event))
        return True


def _copyable_text(message: ChatMessage) -> str | None:
    if message.kind == MessageKind.TOOL:
        if message.tool_event is not None:
            return message.tool_event.full_content or message.tool_event.content or None
        body = str(message.tool_result or message.body or "").strip()
        return body or None
    body = str(message.body or "").strip()
    return body or None


def iter_message_bodies(messages: Iterable[ChatMessage]) -> Iterable[str]:
    for m in messages:
        if m.body:
            yield m.body
