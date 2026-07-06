from __future__ import annotations

import time
from dataclasses import dataclass
from dataclasses import replace

from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer
from textual.css.query import QueryError
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Input, Label, Static

from openminion.cli.tui.presentation.models import (
    ChatMessage,
    MessageKind,
    ToolEvent,
    format_chat_timestamp,
)
from openminion.cli.tui.presentation.messages import (
    looks_like_markdown,
    render_body,
)
from openminion.cli.tui.presentation.tool.blocks import ToolBlockWidget
from .chat_selection import ChatSelectionMixin, copyable_text_for_message  # noqa: F401

__all__ = [
    "ChatMessage",
    "ChatSearchBar",
    "ChatView",
    "EmptyStatePulse",
    "IdleAnimation",
    "MessageContent",
    "MessageKind",
    "MessageWidget",
    "TUIRenderMeasurement",
    "ToolBlockWidget",
    "ToolEvent",
    "format_chat_timestamp",
]

_MAX_RENDER_MEASUREMENTS = 128
_TUI_RENDER_VIEW_FAMILY = "chat"
_STREAM_CURSOR = "▍"
_TOOL_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SEARCH_MATCH_STYLE = "black on rgb(255,215,0) underline"
_IDLE_FRAMES = (
    (
        "                                        \n"
        "                                        \n"
        "                  ░░                    \n"
        "                ░░▒▒░░                  \n"
        "                  ░░                    \n"
        "                                        \n"
        "                                        "
    ),
    (
        "                                        \n"
        "                  ░░                    \n"
        "                ░░▒▒░░                  \n"
        "              ░░▒▒▓▓▒▒░░                \n"
        "                ░░▒▒░░                  \n"
        "                  ░░                    \n"
        "                                        "
    ),
    (
        "                  ░░                    \n"
        "                ░░▒▒░░                  \n"
        "              ░░▒▒▓▓▒▒░░                \n"
        "            ░░▒▒▓▓██▓▓▒▒░░              \n"
        "              ░░▒▒▓▓▒▒░░                \n"
        "                ░░▒▒░░                  \n"
        "                  ░░                    "
    ),
    (
        "                ░░▒▒░░                  \n"
        "              ░░▒▒▓▓▒▒░░                \n"
        "            ░░▒▒▓▓██▓▓▒▒░░              \n"
        "          ░░▒▒▓▓████████▓▓▒▒░░          \n"
        "            ░░▒▒▓▓██▓▓▒▒░░              \n"
        "              ░░▒▒▓▓▒▒░░                \n"
        "                ░░▒▒░░                  "
    ),
    (
        "              ░░▒▒▓▓▒▒░░                \n"
        "            ░░▒▒▓▓██▓▓▒▒░░              \n"
        "          ░░▒▒▓▓████████▓▓▒▒░░          \n"
        "        ░░▒▒▓▓████████████▓▓▒▒░░        \n"
        "          ░░▒▒▓▓████████▓▓▒▒░░          \n"
        "            ░░▒▒▓▓██▓▓▒▒░░              \n"
        "              ░░▒▒▓▓▒▒░░                "
    ),
    (
        "            ░░▒▒▓▓██▓▓▒▒░░              \n"
        "          ░░▒▒▓▓████████▓▓▒▒░░          \n"
        "        ░░▒▒▓▓████████████▓▓▒▒░░        \n"
        "      ░░▒▒▓▓████████████████▓▓▒▒░░      \n"
        "        ░░▒▒▓▓████████████▓▓▒▒░░        \n"
        "          ░░▒▒▓▓████████▓▓▒▒░░          \n"
        "            ░░▒▒▓▓██▓▓▒▒░░              "
    ),
    (
        "          ░░▒▒▓▓████████▓▓▒▒░░          \n"
        "        ░░▒▒▓▓████████████▓▓▒▒░░        \n"
        "      ░░▒▒▓▓████████████████▓▓▒▒░░      \n"
        "    ░░▒▒▓▓████████████████████▓▓▒▒░░    \n"
        "      ░░▒▒▓▓████████████████▓▓▒▒░░      \n"
        "        ░░▒▒▓▓████████████▓▓▒▒░░        \n"
        "          ░░▒▒▓▓████████▓▓▒▒░░          "
    ),
    (
        "        ░░▒▒▓▓████████████▓▓▒▒░░        \n"
        "      ░░▒▒▓▓████████████████▓▓▒▒░░      \n"
        "    ░░▒▒▓▓████████████████████▓▓▒▒░░    \n"
        "  ░░▒▒▓▓████████████████████████▓▓▒▒░░  \n"
        "    ░░▒▒▓▓████████████████████▓▓▒▒░░    \n"
        "      ░░▒▒▓▓████████████████▓▓▒▒░░      \n"
        "        ░░▒▒▓▓████████████▓▓▒▒░░        "
    ),
    (
        "          ░░▒▒▓▓████████▓▓▒▒░░          \n"
        "        ░░▒▒▓▓████████████▓▓▒▒░░        \n"
        "      ░░▒▒▓▓████████████████▓▓▒▒░░      \n"
        "    ░░▒▒▓▓████████████████████▓▓▒▒░░    \n"
        "      ░░▒▒▓▓████████████████▓▓▒▒░░      \n"
        "        ░░▒▒▓▓████████████▓▓▒▒░░        \n"
        "          ░░▒▒▓▓████████▓▓▒▒░░          "
    ),
    (
        "            ░░▒▒▓▓██▓▓▒▒░░              \n"
        "          ░░▒▒▓▓████████▓▓▒▒░░          \n"
        "        ░░▒▒▓▓████████████▓▓▒▒░░        \n"
        "      ░░▒▒▓▓████████████████▓▓▒▒░░      \n"
        "        ░░▒▒▓▓████████████▓▓▒▒░░        \n"
        "          ░░▒▒▓▓████████▓▓▒▒░░          \n"
        "            ░░▒▒▓▓██▓▓▒▒░░              "
    ),
    (
        "              ░░▒▒▓▓▒▒░░                \n"
        "            ░░▒▒▓▓██▓▓▒▒░░              \n"
        "          ░░▒▒▓▓████████▓▓▒▒░░          \n"
        "        ░░▒▒▓▓████████████▓▓▒▒░░        \n"
        "          ░░▒▒▓▓████████▓▓▒▒░░          \n"
        "            ░░▒▒▓▓██▓▓▒▒░░              \n"
        "              ░░▒▒▓▓▒▒░░                "
    ),
    (
        "                ░░▒▒░░                  \n"
        "              ░░▒▒▓▓▒▒░░                \n"
        "            ░░▒▒▓▓██▓▓▒▒░░              \n"
        "          ░░▒▒▓▓████████▓▓▒▒░░          \n"
        "            ░░▒▒▓▓██▓▓▒▒░░              \n"
        "              ░░▒▒▓▓▒▒░░                \n"
        "                ░░▒▒░░                  "
    ),
    (
        "                  ░░                    \n"
        "                ░░▒▒░░                  \n"
        "              ░░▒▒▓▓▒▒░░                \n"
        "            ░░▒▒▓▓██▓▓▒▒░░              \n"
        "              ░░▒▒▓▓▒▒░░                \n"
        "                ░░▒▒░░                  \n"
        "                  ░░                    "
    ),
    (
        "                                        \n"
        "                  ░░                    \n"
        "                ░░▒▒░░                  \n"
        "              ░░▒▒▓▓▒▒░░                \n"
        "                ░░▒▒░░                  \n"
        "                  ░░                    \n"
        "                                        "
    ),
)

_IDLE_FRAME_INTERVALS = tuple(
    0.5 if index == 7 else 0.2 for index in range(len(_IDLE_FRAMES))
)
_EMPTY_STATE_FRAMES = (
    "·  ·  ·",
    "•  ·  ·",
    "•  •  ·",
    "•  •  •",
)


@dataclass(frozen=True)
class TUIRenderMeasurement:
    view_family: str
    render_chunk_ms: int
    queue_pressure: int
    retained_messages: int
    outcome: str = "ok"

    def as_dict(self) -> dict[str, object]:
        return {
            "view_family": self.view_family,
            "render_chunk_ms": self.render_chunk_ms,
            "queue_pressure": self.queue_pressure,
            "retained_messages": self.retained_messages,
            "outcome": self.outcome,
        }


def _elapsed_ms_since_ns(started_ns: int) -> int:
    return max(0, int((time.perf_counter_ns() - started_ns) // 1_000_000))


class MessageContent(Static):
    DEFAULT_CSS = "MessageContent { height: auto; }"

    def __init__(
        self,
        renderable: object,
        *,
        markdown_enabled: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(renderable, **kwargs)
        self.renderable_value = renderable
        self.markdown_enabled = markdown_enabled

    def set_content(
        self,
        renderable: object,
        *,
        markdown_enabled: bool = False,
    ) -> None:
        self.renderable_value = renderable
        self.markdown_enabled = markdown_enabled
        self.update(renderable)


class EmptyStatePulse(Widget):
    _frame: reactive[int] = reactive(0)
    _timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Label(_EMPTY_STATE_FRAMES[0], classes="empty-state-pulse-label")

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.35, self._tick)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(_EMPTY_STATE_FRAMES)

    def watch__frame(self, frame: int) -> None:
        try:
            self.query_one(".empty-state-pulse-label", Label).update(
                _EMPTY_STATE_FRAMES[frame]
            )
        except (QueryError, AttributeError):
            pass


class IdleAnimation(Widget):
    is_active: reactive[bool] = reactive(True)
    _frame: reactive[int] = reactive(0)
    _timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Label(_IDLE_FRAMES[0], id="chat-idle-pattern")
        yield Label(
            "Ready.  Type a message, or / for commands.",
            id="chat-idle-caption",
        )

    def on_mount(self) -> None:
        self._schedule_next_tick()

    def _tick(self) -> None:
        if self.is_active:
            self._frame = (self._frame + 1) % len(_IDLE_FRAMES)
        self._schedule_next_tick()

    def _schedule_next_tick(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._timer = self.set_timer(
            self.frame_interval_seconds(self._frame),
            self._tick,
        )

    @staticmethod
    def frame_interval_seconds(frame: int) -> float:
        return _IDLE_FRAME_INTERVALS[frame % len(_IDLE_FRAME_INTERVALS)]

    def watch__frame(self, frame: int) -> None:
        if not self.is_active:
            return
        try:
            self.query_one("#chat-idle-pattern", Label).update(_IDLE_FRAMES[frame])
        except (QueryError, AttributeError):
            pass

    def watch_is_active(self, active: bool) -> None:
        if active:
            self.remove_class("--hidden")
            self._frame = 0
            try:
                self.query_one("#chat-idle-pattern", Label).update(_IDLE_FRAMES[0])
            except (QueryError, AttributeError):
                pass
            self._schedule_next_tick()
        else:
            self.add_class("--hidden")
            if self._timer is not None:
                self._timer.stop()
                self._timer = None

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None


class MessageWidget(Widget):
    DEFAULT_CSS = "MessageWidget { height: auto; }"

    def __init__(self, message: ChatMessage) -> None:
        safe_id = self._safe_message_id(message.msg_id)
        super().__init__(
            classes=f"message msg-{message.kind.value}",
            id=safe_id or None,
        )
        self._message = message
        self._streaming = False
        self._search_query = ""
        self._stream_cursor_visible = True
        self._stream_timer: Timer | None = None
        self._tool_spinner_frame = 0
        self._tool_spinner_timer: Timer | None = None
        self._last_render_measurement: TUIRenderMeasurement | None = None
        if not message.show_header and message.kind in {
            MessageKind.USER,
            MessageKind.AGENT,
            MessageKind.TOOL,
        }:
            self.add_class("--continued")

    def on_click(self, event: events.Click) -> None:  # noqa: D401
        try:
            chat_view: ChatView | None = None
            for node in self.ancestors_with_self:
                if isinstance(node, ChatView):
                    chat_view = node
                    break
            if chat_view is not None:
                chat_view.select_message(self._message.msg_id)
                try:
                    chat_view.focus()
                except (QueryError, AttributeError):
                    pass
                event.stop()
        except (QueryError, AttributeError):
            pass

    def compose(self) -> ComposeResult:
        msg = self._message

        if msg.kind == MessageKind.USER:
            if msg.show_header:
                yield from self._compose_header("▸ you")
            yield MessageContent(
                self._body_renderable(msg.body),
                classes="message-body",
                id=self._body_widget_id(),
            )

        elif msg.kind == MessageKind.AGENT:
            if msg.show_header:
                yield from self._compose_header(f"◆ {msg.sender}")
            body_renderable = self._body_renderable(msg.body)
            yield MessageContent(
                self._agent_body_renderable(msg.body, body_renderable),
                markdown_enabled=bool(msg.body)
                and not self._search_query
                and not self._streaming
                and isinstance(body_renderable, RichMarkdown),
                classes="message-body message-agent-body",
                id=self._body_widget_id(),
            )

        elif msg.kind == MessageKind.TOOL:
            if msg.show_header:
                yield from self._compose_header(f"⚙ {msg.sender}")
            if msg.tool_event is not None:
                yield ToolBlockWidget(
                    msg.tool_event,
                    pending=msg.tool_result is None,
                    id=f"{self._safe_message_id(msg.msg_id)}-tool-block",
                )
                return
            if msg.tool_result is None:
                yield Label(
                    "",
                    classes="message-tool-active",
                    id=self._tool_spinner_widget_id(),
                )
            yield MessageContent(
                self._body_renderable(msg.body, markdown_allowed=False),
                classes="message-body message-tool-call",
                id=self._body_widget_id(),
            )
            if msg.tool_result is not None:
                tool_result_renderable = self._body_renderable(msg.tool_result)
                yield Static(
                    self._text_renderable("─ result"),
                    classes="message-tool-divider",
                )
                yield MessageContent(
                    tool_result_renderable,
                    markdown_enabled=bool(msg.tool_result)
                    and not self._search_query
                    and isinstance(tool_result_renderable, RichMarkdown),
                    classes="message-tool-result",
                    id=self._tool_result_widget_id(),
                )

        elif msg.kind == MessageKind.SYSTEM:
            yield Static(self._text_renderable(msg.body), classes="message-system")

        elif msg.kind == MessageKind.ERROR:
            if msg.show_header:
                yield from self._compose_header("✗ error")
            yield MessageContent(
                self._render_error_body(),
                classes="message-body message-error-body",
                id=self._body_widget_id(),
            )

    def on_mount(self) -> None:
        self._sync_stream_timer()
        self._sync_tool_spinner()

    def refresh_timestamp(self) -> None:
        if self._message.kind == MessageKind.SYSTEM:
            return
        try:
            self.query_one(f"#{self._timestamp_widget_id()}", Label).update(
                self._message.display_timestamp()
            )
        except (QueryError, AttributeError):
            pass

    def update_body(self, text: str, streaming: bool = False) -> None:
        started_ns = time.perf_counter_ns()
        outcome = "ok"
        self._message.body = text
        self._streaming = streaming
        self._sync_stream_timer()
        try:
            body_renderable = self._body_renderable(text)
            self.query_one(f"#{self._body_widget_id()}", MessageContent).set_content(
                self._agent_body_renderable(text, body_renderable),
                markdown_enabled=bool(text)
                and not streaming
                and not self._search_query
                and isinstance(body_renderable, RichMarkdown),
            )
            self.refresh_timestamp()
        except (QueryError, AttributeError):
            outcome = "missing_widget"
            pass
        finally:
            self._last_render_measurement = TUIRenderMeasurement(
                view_family=_TUI_RENDER_VIEW_FAMILY,
                render_chunk_ms=_elapsed_ms_since_ns(started_ns),
                queue_pressure=1 if streaming else 0,
                retained_messages=1,
                outcome=outcome,
            )

    def render_measurement_snapshot(self) -> dict[str, object] | None:
        if self._last_render_measurement is None:
            return None
        return self._last_render_measurement.as_dict()

    def set_search_query(self, query: str) -> None:
        self._search_query = str(query or "").strip().lower()
        body = self._message.body
        if self._message.kind in {
            MessageKind.USER,
            MessageKind.AGENT,
            MessageKind.TOOL,
            MessageKind.ERROR,
        }:
            try:
                body_widget = self.query_one(
                    f"#{self._body_widget_id()}",
                    MessageContent,
                )
                if self._message.kind == MessageKind.ERROR:
                    body_widget.set_content(self._render_error_body())
                else:
                    renderable = self._body_renderable(
                        body,
                        markdown_allowed=self._message.kind != MessageKind.TOOL,
                    )
                    body_widget.set_content(
                        self._agent_body_renderable(body, renderable)
                        if self._message.kind == MessageKind.AGENT
                        else renderable,
                        markdown_enabled=bool(body)
                        and self._message.kind == MessageKind.AGENT
                        and not self._streaming
                        and not self._search_query
                        and isinstance(renderable, RichMarkdown),
                    )
            except (QueryError, AttributeError):
                pass
        if (
            self._message.kind == MessageKind.TOOL
            and self._message.tool_result is not None
        ):
            try:
                result_widget = self.query_one(
                    f"#{self._tool_result_widget_id()}",
                    MessageContent,
                )
                renderable = self._body_renderable(self._message.tool_result)
                result_widget.set_content(
                    renderable,
                    markdown_enabled=bool(self._message.tool_result)
                    and not self._search_query
                    and isinstance(renderable, RichMarkdown),
                )
            except (QueryError, AttributeError):
                pass

    def set_tool_result(self, text: str) -> None:
        self._message.tool_result = text
        if self._message.tool_event is not None:
            self._message.tool_event = replace(
                self._message.tool_event,
                content=text,
                full_content=text or self._message.tool_event.full_content,
                truncated=False,
            )
            try:
                block = self.query_one(
                    f"#{self._safe_message_id(self._message.msg_id)}-tool-block",
                    ToolBlockWidget,
                )
                block.update_event(self._message.tool_event, pending=False)
                return
            except (QueryError, AttributeError):
                pass
        self._sync_tool_spinner()
        try:
            result_widget = self.query_one(
                f"#{self._tool_result_widget_id()}",
                MessageContent,
            )
            result_renderable = self._body_renderable(text)
            result_widget.set_content(
                result_renderable,
                markdown_enabled=bool(text)
                and not self._search_query
                and isinstance(result_renderable, RichMarkdown),
            )
            return
        except (QueryError, AttributeError):
            pass

        try:
            self.mount(
                Static(
                    self._text_renderable("─ result"),
                    classes="message-tool-divider",
                )
            )
            result_renderable = self._body_renderable(text)
            self.mount(
                MessageContent(
                    result_renderable,
                    markdown_enabled=bool(text)
                    and not self._search_query
                    and isinstance(result_renderable, RichMarkdown),
                    classes="message-tool-result",
                    id=self._tool_result_widget_id(),
                )
            )
        except (QueryError, AttributeError):
            pass

    def _compose_header(self, label_text: str) -> ComposeResult:
        with Horizontal(classes="message-header-row"):
            yield Label(label_text, classes="message-header-main")
            yield Label(
                self._message.display_timestamp(),
                classes="message-timestamp",
                id=self._timestamp_widget_id(),
            )

    def _agent_body_renderable(self, text: str, final_renderable: object) -> object:
        if self._streaming:
            cursor = _STREAM_CURSOR if self._stream_cursor_visible else " "
            if text:
                return self._text_renderable(f"{text}{cursor}")
            return self._text_renderable(cursor)
        return final_renderable

    def _body_renderable(self, text: str, *, markdown_allowed: bool = True) -> object:
        clean = str(text or "")
        if self._search_query:
            return self._highlight_text(clean, self._search_query)
        if not clean.strip():
            return Text(clean)
        return render_body(clean, markdown_allowed=markdown_allowed)

    @staticmethod
    def _text_renderable(text: str) -> Text:
        return Text(str(text or ""))

    @staticmethod
    def _highlight_text(text: str, query: str) -> Text:
        renderable = Text(str(text or ""))
        if query:
            renderable.highlight_words([query], style=_SEARCH_MATCH_STYLE)
        return renderable

    @staticmethod
    def _looks_like_markdown(text: str) -> bool:
        return looks_like_markdown(text)

    @staticmethod
    def _safe_message_id(message_id: str) -> str:
        safe_id = message_id
        if safe_id and safe_id[0].isdigit():
            safe_id = f"m-{safe_id}"
        return safe_id

    def _render_error_body(self) -> Text:
        lines = str(self._message.body or "").splitlines() or [""]
        renderable = Text()
        renderable.append(lines[0], style="bold red")
        for line in lines[1:]:
            renderable.append("\n")
            renderable.append(line, style="dim red")
        if self._message.retryable_error:
            renderable.append("\n")
            renderable.append("Will retry automatically", style="italic yellow")
        return renderable

    def _sync_stream_timer(self) -> None:
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer = None
        if self._streaming:
            self._stream_cursor_visible = True
            self._stream_timer = self.set_interval(0.5, self._toggle_stream_cursor)
        else:
            self._stream_cursor_visible = True

    def _toggle_stream_cursor(self) -> None:
        self._stream_cursor_visible = not self._stream_cursor_visible
        try:
            body_widget = self.query_one(f"#{self._body_widget_id()}", MessageContent)
        except (QueryError, AttributeError):
            return
        renderable = self._body_renderable(self._message.body)
        body_widget.set_content(
            self._agent_body_renderable(self._message.body, renderable),
            markdown_enabled=False,
        )

    def _sync_tool_spinner(self) -> None:
        if self._tool_spinner_timer is not None:
            self._tool_spinner_timer.stop()
            self._tool_spinner_timer = None
        if self._message.kind == MessageKind.TOOL and self._message.tool_result is None:
            self._tool_spinner_frame = 0
            self._tool_spinner_timer = self.set_interval(
                0.08, self._advance_tool_spinner
            )
            self._update_tool_spinner()
        else:
            try:
                spinner = self.query_one(f"#{self._tool_spinner_widget_id()}", Label)
                spinner.display = False
            except (QueryError, AttributeError):
                pass

    def _advance_tool_spinner(self) -> None:
        self._tool_spinner_frame = (self._tool_spinner_frame + 1) % len(_TOOL_SPINNER)
        self._update_tool_spinner()

    def _update_tool_spinner(self) -> None:
        try:
            spinner = self.query_one(f"#{self._tool_spinner_widget_id()}", Label)
        except (QueryError, AttributeError):
            return
        spinner.display = self._message.tool_result is None
        spinner.update(f"{_TOOL_SPINNER[self._tool_spinner_frame]}  tool in progress")

    def on_unmount(self) -> None:
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer = None
        if self._tool_spinner_timer is not None:
            self._tool_spinner_timer.stop()
            self._tool_spinner_timer = None

    def _body_widget_id(self) -> str:
        return f"{self._safe_message_id(self._message.msg_id)}-body"

    def _timestamp_widget_id(self) -> str:
        return f"{self._safe_message_id(self._message.msg_id)}-ts"

    def _tool_result_widget_id(self) -> str:
        return f"{self._safe_message_id(self._message.msg_id)}-tool-result"

    def _tool_spinner_widget_id(self) -> str:
        return f"{self._safe_message_id(self._message.msg_id)}-tool-spinner"


class ChatSearchBar(Widget):
    class SearchChanged(Message):
        def __init__(self, query: str) -> None:
            super().__init__()
            self.query = query

    class SearchClosed(Message):
        pass

    DEFAULT_CSS = "ChatSearchBar { height: auto; display: none; }"

    def compose(self) -> ComposeResult:
        with Horizontal(classes="chat-search-row"):
            yield Label("search:", classes="chat-search-icon")
            yield Input(placeholder="Search messages…", id="chat-search-input")
            yield Label("Esc close", classes="chat-search-hint")

    def show(self) -> None:
        self.display = True
        try:
            self.query_one("#chat-search-input").focus()
        except (QueryError, AttributeError):
            pass

    def hide(self) -> None:
        self.display = False
        try:
            self.query_one("#chat-search-input", Input).value = ""
        except (QueryError, AttributeError):
            pass
        self.post_message(self.SearchChanged(""))

    def on_input_changed(self, event) -> None:
        if getattr(event.input, "id", "") == "chat-search-input":
            self.post_message(self.SearchChanged(event.value))

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape" and self.display:
            self.hide()
            self.post_message(self.SearchClosed())
            event.stop()


class ChatView(ChatSelectionMixin, ScrollableContainer):
    can_focus = True

    BINDINGS = [
        ("up", "select_previous", "Select previous"),
        ("k", "select_previous", "Select previous"),
        ("down", "select_next", "Select next"),
        ("j", "select_next", "Select next"),
        ("home", "select_first", "Select first"),
        ("end", "select_last", "Select last"),
        ("escape", "clear_selection", "Clear selection"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(id="chat-view", **kwargs)
        self._messages: list[ChatMessage] = []
        self._bottom_gap = 0
        self._search_query = ""
        self._selected_message_id: str | None = None
        self._render_measurements: list[TUIRenderMeasurement] = []

    @property
    def bottom_gap(self) -> int:
        return self._bottom_gap

    def compose(self) -> ComposeResult:
        yield Static("", id="chat-bottom-spacer")
        yield IdleAnimation(id="chat-idle")
        for msg in self._messages:
            yield MessageWidget(msg)

    def on_mount(self) -> None:
        self.set_interval(30, self._refresh_timestamps)
        self.call_after_refresh(self._sync_layout_state)

    def on_resize(self, event: events.Resize) -> None:
        del event
        self.call_after_refresh(self._sync_layout_state)

    def push_message(self, message: ChatMessage) -> MessageWidget:
        started_ns = time.perf_counter_ns()
        outcome = "ok"
        was_tail_selected = False
        try:
            if self._messages:
                previous = self._messages[-1]
                message.show_header = self._starts_new_group(previous, message)
                if self._selected_message_id == previous.msg_id:
                    was_tail_selected = True
            elif self._selected_message_id is None:
                was_tail_selected = True
            self._messages.append(message)
            widget = MessageWidget(message)
            self.mount(widget)
            if was_tail_selected:
                self._apply_selection(message.msg_id)
            self.call_after_refresh(self._sync_layout_state)
            self.call_after_refresh(lambda: self.scroll_end(animate=False))
            return widget
        except Exception:
            outcome = "error"
            raise
        finally:
            self._record_render_measurement(
                started_ns=started_ns,
                queue_pressure=1,
                outcome=outcome,
            )

    def clear_messages(self) -> None:
        self._messages.clear()
        self._selected_message_id = None
        self.query("MessageWidget").remove()
        self.call_after_refresh(self._sync_layout_state)

    def set_messages(self, messages: list[ChatMessage]) -> None:
        started_ns = time.perf_counter_ns()
        outcome = "ok"
        self.clear_messages()
        try:
            previous: ChatMessage | None = None
            for msg in messages:
                msg.show_header = (
                    True if previous is None else self._starts_new_group(previous, msg)
                )
                self._messages.append(msg)
                self.mount(MessageWidget(msg))
                previous = msg
            self.call_after_refresh(self._sync_layout_state)
            self.call_after_refresh(lambda: self.scroll_end(animate=False))
        except Exception:
            outcome = "error"
            raise
        finally:
            self._record_render_measurement(
                started_ns=started_ns,
                queue_pressure=len(messages),
                outcome=outcome,
            )

    def render_measurements_snapshot(self) -> list[dict[str, object]]:
        return [measurement.as_dict() for measurement in self._render_measurements]

    def _record_render_measurement(
        self,
        *,
        started_ns: int,
        queue_pressure: int,
        outcome: str,
    ) -> None:
        self._render_measurements.append(
            TUIRenderMeasurement(
                view_family=_TUI_RENDER_VIEW_FAMILY,
                render_chunk_ms=_elapsed_ms_since_ns(started_ns),
                queue_pressure=max(0, int(queue_pressure)),
                retained_messages=len(self._messages),
                outcome=outcome,
            )
        )
        if len(self._render_measurements) > _MAX_RENDER_MEASUREMENTS:
            del self._render_measurements[:-_MAX_RENDER_MEASUREMENTS]

    def _refresh_timestamps(self) -> None:
        for widget in self.query(MessageWidget):
            widget.refresh_timestamp()

    def _sync_layout_state(self) -> None:
        self._update_idle_state()
        self._refresh_timestamps()
        self.call_after_refresh(self._update_bottom_spacer)

    def _update_idle_state(self) -> None:
        has_real_turns = any(
            msg.kind in {MessageKind.USER, MessageKind.AGENT, MessageKind.TOOL}
            for msg in self._messages
        )
        try:
            self.query_one("#chat-idle", IdleAnimation).is_active = not has_real_turns
        except (QueryError, AttributeError):
            pass

    def filter_messages(self, query: str) -> None:
        self._search_query = query.strip().lower()
        for widget in self.query(MessageWidget):
            widget.set_search_query(self._search_query)
            if not self._search_query:
                widget.display = True
            else:
                widget.display = self._message_matches_query(
                    widget._message, self._search_query
                )

    def _update_bottom_spacer(self) -> None:
        try:
            spacer = self.query_one("#chat-bottom-spacer", Static)
        except (QueryError, AttributeError):
            return

        viewport_height = (
            self.scrollable_content_region.height
            or self.content_region.height
            or self.size.height
        )
        content_height = sum(
            child.outer_size.height
            for child in self.children
            if child.id != "chat-bottom-spacer"
        )
        gap = max(0, int(viewport_height - content_height))
        if gap == self._bottom_gap:
            return
        self._bottom_gap = gap
        spacer.styles.height = gap

    @staticmethod
    def _starts_new_group(previous: ChatMessage, current: ChatMessage) -> bool:
        groupable = {
            MessageKind.USER,
            MessageKind.AGENT,
            MessageKind.TOOL,
            MessageKind.ERROR,
        }
        return not (
            previous.kind == current.kind
            and previous.sender == current.sender
            and current.kind in groupable
        )

    @staticmethod
    def _message_matches_query(message: ChatMessage, query: str) -> bool:
        tool_event = getattr(message, "tool_event", None)
        tool_text = ""
        if tool_event is not None:
            tool_text = " ".join(
                [
                    str(getattr(tool_event, "tool_name", "") or ""),
                    str(getattr(tool_event, "content", "") or ""),
                    str(getattr(tool_event, "full_content", "") or ""),
                ]
            ).lower()
        return any(
            query in candidate
            for candidate in (
                str(message.body or "").lower(),
                str(message.sender or "").lower(),
                str(message.tool_result or "").lower(),
                tool_text,
            )
        )
