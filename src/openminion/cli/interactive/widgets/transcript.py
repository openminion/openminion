from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import Literal

from rich.text import Text
from textual.containers import ScrollableContainer
from textual.css.query import QueryError
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from openminion.cli.presentation.models import ChatMessage, MessageKind, ToolEvent
from openminion.cli.presentation.messages import (
    render_body,
    render_error_text,
    render_system_text,
    render_user_text,
)
from openminion.cli.presentation.tool.blocks import VerbosityLevel

from .tool_block import ToolBlockWidget

_STREAM_CURSOR = "▍"
_STREAM_BLINK_INTERVAL = 0.5
_BOUNDED_FALLBACK_THRESHOLD_S = 0.05


@dataclass
class _StreamingState:
    """Per-message streaming state owned by `FocusMessageWidget`."""

    started_at: float
    visible: bool = True
    timer: Timer | None = None


class FocusMessageWidget(Widget):
    """Focus-native single-message renderer."""

    DEFAULT_CSS = """
    FocusMessageWidget { height: auto; padding: 0 1; }
    FocusMessageWidget.--system {
        border-left: tall $accent;
        padding: 0 1 0 2;
    }
    FocusMessageWidget.--error {
        border-left: tall $error;
        background: $error 15%;
        padding: 0 1 0 2;
    }
    """

    def __init__(
        self,
        message: ChatMessage,
        *,
        verbosity: VerbosityLevel = "normal",
    ) -> None:
        super().__init__(
            classes=f"focus-message --{message.kind.value}",
            id=self._safe_id(message.msg_id),
        )
        self._message = message
        self._streaming: _StreamingState | None = None
        self._search_query = ""
        self._verbosity: VerbosityLevel = verbosity

    @staticmethod
    def _safe_id(msg_id: str) -> str | None:
        token = str(msg_id or "").strip()
        if not token:
            return None
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in token)
        safe = safe.strip("-_")
        if not safe:
            return None
        return f"msg-{safe}"

    def compose(self):
        msg = self._message
        if msg.kind == MessageKind.TOOL and msg.tool_event is not None:
            yield ToolBlockWidget(
                msg.tool_event,
                pending=msg.tool_result is None,
                verbosity=self._verbosity,
                id=f"{self.id or 'msg'}-tool-block",
            )
            return
        yield Static(self._render_body(), id=f"{self.id or 'msg'}-body")

    def _render_body(self) -> object:
        msg = self._message
        body = str(msg.body or "")
        if msg.kind == MessageKind.USER:
            return self._with_cursor(render_user_text(body))
        if msg.kind == MessageKind.AGENT and not self._streaming:
            return self._body_renderable(body, markdown_allowed=True)
        if msg.kind == MessageKind.SYSTEM:
            return render_system_text(body)
        if msg.kind == MessageKind.ERROR:
            return render_error_text(body)
        return self._with_cursor(Text(body))

    def _body_renderable(self, text: str, *, markdown_allowed: bool = True) -> object:
        renderable = render_body(text, markdown_allowed=markdown_allowed)
        if isinstance(renderable, Text):
            return self._with_cursor(renderable)
        return renderable

    def _with_cursor(self, text: Text) -> Text:
        if self._streaming and self._streaming.visible:
            text.append(_STREAM_CURSOR)
        return text

    def update_body(self, text: str, *, streaming: bool = False) -> None:
        """Streaming-aware body update."""
        self._message.body = text
        if streaming:
            if self._streaming is None:
                self._streaming = _StreamingState(started_at=time.monotonic())
                self._streaming.timer = self.set_interval(
                    _STREAM_BLINK_INTERVAL, self._toggle_cursor
                )
        else:
            if self._streaming and self._streaming.timer is not None:
                self._streaming.timer.stop()
            self._streaming = None
        self._refresh_body()

    def _toggle_cursor(self) -> None:
        if self._streaming is None:
            return
        self._streaming.visible = not self._streaming.visible
        self._refresh_body()

    def _refresh_body(self) -> None:
        try:
            body = self.query_one(f"#{self.id or 'msg'}-body", Static)
            body.update(self._render_body())
        except QueryError:
            pass

    def set_tool_result(self, text: str) -> None:
        """Update a tool message's result post-execution."""
        from dataclasses import replace as _replace

        self._message.tool_result = text
        if self._message.tool_event is not None:
            self._message.tool_event = _replace(
                self._message.tool_event,
                content=text,
                full_content=text or self._message.tool_event.full_content,
                truncated=False,
            )
            try:
                block = self.query_one(
                    f"#{self.id or 'msg'}-tool-block", ToolBlockWidget
                )
                block.update_event(self._message.tool_event, pending=False)
                return
            except QueryError:
                pass
        self._refresh_body()

    def _tool_block_dom_id(self, call_id: str) -> str:
        token = str(call_id or "").strip()
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in token)
        safe = safe.strip("-_") or "tool-block"
        return f"{self.id or 'msg'}-{safe}"

    def upsert_tool_block(
        self,
        *,
        call_id: str,
        event: ToolEvent,
        pending: bool,
    ) -> ToolBlockWidget:
        """Create or update one inline tool block for this message."""
        block_id = self._tool_block_dom_id(call_id or event.call_id or event.tool_name)
        try:
            block = self.query_one(f"#{block_id}", ToolBlockWidget)
        except QueryError:
            block = ToolBlockWidget(
                event,
                pending=pending,
                verbosity=self._verbosity,
                id=block_id,
            )
            self.mount(block)
            return block
        block.update_event(event, pending=pending)
        return block

    def append_tool_block(self, event: ToolEvent) -> None:
        """Mount a completed tool block inside this message."""
        try:
            self.upsert_tool_block(
                call_id=event.call_id or f"tool-block-{int(time.monotonic() * 1000)}",
                event=event,
                pending=False,
            )
        except QueryError:
            pass

    def apply_verbosity(self, verbosity: VerbosityLevel) -> None:
        """Propagate a verbosity change from the transcript."""
        if verbosity not in ("quiet", "normal", "verbose"):
            return
        self._verbosity = verbosity
        try:
            for block in self.query(ToolBlockWidget):
                block.verbosity = verbosity  # triggers watch_verbosity
        except QueryError:
            pass

    def complete_streaming(self, *, suppress_blink: bool) -> None:
        """End streaming; honor bounded-fallback blink suppression."""
        if self._streaming and self._streaming.timer is not None:
            self._streaming.timer.stop()
        self._streaming = None
        if suppress_blink:
            self._refresh_body()


class TurnHandle:
    """Per-turn streaming handle returned by `begin_turn`."""

    def __init__(self, widget: FocusMessageWidget, started_at: float) -> None:
        self._widget = widget
        self._started_at = started_at
        self._buffer = ""

    def append_token(self, s: str) -> None:
        if not s:
            return
        self._buffer += s
        self._widget.update_body(self._buffer, streaming=True)

    def append_tool_block(self, event: ToolEvent) -> None:
        self._widget.append_tool_block(event)

    def upsert_tool_block(
        self,
        *,
        call_id: str,
        event: ToolEvent,
        pending: bool,
    ) -> ToolBlockWidget:
        return self._widget.upsert_tool_block(
            call_id=call_id,
            event=event,
            pending=pending,
        )

    def complete(self, final_text: str | None = None) -> None:
        if final_text is not None:
            self._buffer = final_text
        self._widget._message.body = self._buffer
        elapsed = time.monotonic() - self._started_at
        suppress_blink = elapsed <= _BOUNDED_FALLBACK_THRESHOLD_S
        self._widget.complete_streaming(suppress_blink=suppress_blink)


class FocusTranscript(ScrollableContainer):
    """Scrolling conversation surface owned by the focus shell."""

    can_focus = True

    def __init__(self, **kwargs) -> None:
        # launch-time verbosity (passed via the CUC-defined
        verbosity_kwarg = kwargs.pop("verbosity", "normal")
        super().__init__(id=kwargs.pop("id", "focus-transcript"), **kwargs)
        self._messages: list[ChatMessage] = []
        self._search_query = ""
        self._selected_message_id: str | None = None
        self._verbosity: VerbosityLevel = (
            verbosity_kwarg
            if verbosity_kwarg in ("quiet", "normal", "verbose")
            else "normal"
        )

    @property
    def verbosity(self) -> VerbosityLevel:
        return self._verbosity

    def set_verbosity(self, verbosity: VerbosityLevel) -> None:
        """CUTP-02: flip the live verbosity reactive."""
        if verbosity not in ("quiet", "normal", "verbose"):
            return
        self._verbosity = verbosity
        try:
            for widget in self.query(FocusMessageWidget):
                widget.apply_verbosity(verbosity)
        except QueryError:
            pass

    def push_message(self, message: ChatMessage) -> FocusMessageWidget:
        """Append a message and return the rendered widget."""
        previous = self._messages[-1] if self._messages else None
        message.show_header = self._starts_new_group(previous, message)
        self._messages.append(message)
        widget = FocusMessageWidget(message, verbosity=self._verbosity)
        if not message.show_header and message.kind in {
            MessageKind.USER,
            MessageKind.AGENT,
            MessageKind.TOOL,
        }:
            widget.add_class("--continued")
        self.mount(widget)
        self._selected_message_id = message.msg_id
        self.call_after_refresh(lambda: self.scroll_end(animate=False))
        return widget

    @staticmethod
    def _starts_new_group(previous: ChatMessage | None, current: ChatMessage) -> bool:
        """Return whether the current message starts a new visual group."""
        if previous is None:
            return True
        if previous.kind != current.kind:
            return True
        if (previous.sender or "").strip() != (current.sender or "").strip():
            return True
        return False

    def set_messages(self, messages: list[ChatMessage]) -> None:
        """Replace all messages — used for resume + `/clear` paths."""
        self.clear_messages()
        for msg in messages:
            self._messages.append(msg)
            self.mount(FocusMessageWidget(msg, verbosity=self._verbosity))
        self.call_after_refresh(lambda: self.scroll_end(animate=False))

    def clear_messages(self) -> None:
        self._messages.clear()
        self._selected_message_id = None
        self.query("FocusMessageWidget").remove()

    def drop_message(self, msg_id: str) -> bool:
        """Remove a single message by id; return True if dropped."""
        if not msg_id:
            return False
        before = len(self._messages)
        self._messages = [m for m in self._messages if m.msg_id != msg_id]
        if len(self._messages) == before:
            return False
        for widget in self.query(FocusMessageWidget):
            try:
                if getattr(widget._message, "msg_id", "") == msg_id:
                    widget.remove()
            except AttributeError:
                pass
        if self._selected_message_id == msg_id:
            self._selected_message_id = (
                self._messages[-1].msg_id if self._messages else None
            )
        return True

    def filter_messages(self, query: str) -> None:
        """Show/hide message widgets based on case-insensitive substring."""
        token = query.strip().lower()
        self._search_query = token
        for widget in self.query(FocusMessageWidget):
            msg = widget._message
            if not token:
                widget.display = True
                continue
            haystack = (msg.body or "").lower() + " " + (msg.sender or "").lower()
            if msg.tool_result:
                haystack += " " + str(msg.tool_result).lower()
            widget.display = token in haystack

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

    def begin_turn(
        self,
        role: Literal["user", "assistant"] = "assistant",
        *,
        footer_provider: Callable[[], str] | None = None,
    ) -> TurnHandle:
        """Open a streaming turn and update one message in place."""
        del footer_provider
        kind = MessageKind.AGENT if role == "assistant" else MessageKind.USER
        message = ChatMessage(kind=kind, sender=role, body="")
        widget = self.push_message(message)
        return TurnHandle(widget, started_at=time.monotonic())


def _copyable_text(message: ChatMessage) -> str | None:
    """Return the text that should be copied for a message."""
    if message.kind == MessageKind.TOOL:
        if message.tool_event is not None:
            return message.tool_event.full_content or message.tool_event.content or None
        body = str(message.tool_result or message.body or "").strip()
        return body or None
    body = str(message.body or "").strip()
    return body or None


__all__ = ["FocusMessageWidget", "FocusTranscript", "TurnHandle"]
