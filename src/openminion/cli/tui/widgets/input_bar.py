from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.css.query import QueryError
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Label, TextArea

from openminion.cli.ux.input_normalization import normalize_multiline_input_text
from openminion.cli.tui.widgets.keys import (
    is_bare_space_key,
    is_space_key,
    stop_key_event,
)


class EditorSubmitted(Message):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ChatEditor(TextArea):
    BINDINGS = [("enter", "submit", "Submit")]

    def action_submit(self) -> None:
        self.post_message(EditorSubmitted(self.text))


_SHELL_RESERVED_INPUT_KEYS: frozenset[str] = frozenset(
    {"ctrl+d", "ctrl+k", "ctrl+f", "ctrl+a"}
)


def _strip_reserved(bindings):
    out = []
    for binding in bindings:
        kept = [
            k.strip()
            for k in binding.key.split(",")
            if k.strip() not in _SHELL_RESERVED_INPUT_KEYS
        ]
        if not kept:
            continue
        out.append(
            Binding(
                key=",".join(kept),
                action=binding.action,
                description=binding.description,
                show=binding.show,
                key_display=binding.key_display,
                priority=binding.priority,
                tooltip=binding.tooltip,
                id=binding.id,
                system=binding.system,
                group=binding.group,
            )
        )
    return out


class ChatInput(Input):
    """`Input` variant that releases keys the surrounding shell owns."""

    BINDINGS = _strip_reserved(Input.BINDINGS)

    async def _on_key(self, event) -> None:  # type: ignore[override]
        if is_bare_space_key(event):
            self.insert_text_at_cursor(" ")
            stop_key_event(event)
            return
        await super()._on_key(event)


class ChatInputBar(Widget):
    DEFAULT_PLACEHOLDER = "Message…   /commands   ^P palette   ^L multiline   F1 help"
    FOCUS_PLACEHOLDER_FRESH = "Ask anything · @ to mention a file · / for commands"
    FOCUS_PLACEHOLDER_RESUMED = "Reply, or / for commands · ↑ for history"

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    BINDINGS = [("ctrl+l", "toggle_multiline", "Multiline")]

    DEFAULT_CSS = ""

    def __init__(
        self,
        placeholder: str | None = None,
        *,
        focus_mode: bool = False,
        is_resumed: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(id="input-bar", **kwargs)
        self._focus_mode = bool(focus_mode)
        self._is_resumed = bool(is_resumed)
        self._placeholder = self._resolve_placeholder(placeholder)
        self._history: list[str] = []
        self._history_pos: int = -1
        self._draft: str = ""
        self._multiline = False

    def _resolve_placeholder(self, override: str | None) -> str:
        if override is not None:
            return override
        if self._focus_mode:
            return (
                self.FOCUS_PLACEHOLDER_RESUMED
                if self._is_resumed
                else self.FOCUS_PLACEHOLDER_FRESH
            )
        return self.DEFAULT_PLACEHOLDER

    def set_resumed(self, is_resumed: bool) -> None:
        self._is_resumed = bool(is_resumed)
        if not self._focus_mode:
            return
        new_placeholder = self._resolve_placeholder(None)
        if new_placeholder == self._placeholder:
            return
        self._placeholder = new_placeholder
        self._set_placeholder(new_placeholder)

    def compose(self) -> ComposeResult:
        yield Label("❯", id="input-prompt")
        yield ChatInput(
            placeholder=self._placeholder,
            id="message-input",
        )
        yield ChatEditor(
            text="",
            placeholder=self._placeholder,
            id="message-editor",
            classes="--hidden",
        )
        yield Label("", id="input-mode-label")
        yield Label("", id="char-count")

    def on_mount(self) -> None:
        self.focus_input()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "message-input":
            self._update_counters(event.value)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "message-editor":
            self._update_counters(event.text_area.text)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not self._multiline:
            self._dispatch(event.value)

    def on_editor_submitted(self, event: EditorSubmitted) -> None:
        if self._multiline:
            self._dispatch(event.text)

    def on_key(self, event) -> None:
        if not self._multiline and getattr(event, "key", "") == "shift+enter":
            self._handle_shift_enter(event)
            return

        if self._multiline:
            return

        inp = self.query_one("#message-input", Input)
        if is_space_key(event) and self.app.focused is inp:
            inp.insert_text_at_cursor(" ")
            stop_key_event(event)
            return
        if event.key == "up":
            self._history_back(inp)
            event.stop()
        elif event.key == "down":
            self._history_forward(inp)
            event.stop()

    def _handle_shift_enter(self, event) -> None:
        single = self.query_one("#message-input", Input)
        original = single.value or ""
        try:
            cursor = int(single.cursor_position)
        except (TypeError, ValueError):
            cursor = len(original)
        cursor = max(0, min(cursor, len(original)))
        with_newline = original[:cursor] + "\n" + original[cursor:]
        single.value = with_newline
        if not self._multiline:
            self.toggle_multiline()
        try:
            editor = self.query_one("#message-editor", TextArea)
            editor.move_cursor((1, 0)) if hasattr(editor, "move_cursor") else None
        except (QueryError, AttributeError):
            pass
        self._stop_event(event)

    def on_paste(self, event) -> None:
        text = ""
        for attr in ("text", "data", "value"):
            candidate = getattr(event, attr, None)
            if isinstance(candidate, str):
                text = candidate
                break
        text = normalize_multiline_input_text(text)
        if not text or "\n" not in text:
            return
        if not self._multiline:
            try:
                self.query_one("#message-input", Input).value = ""
            except QueryError:
                pass
            self.toggle_multiline()
        try:
            editor = self.query_one("#message-editor", TextArea)
            editor.text = text
            self._update_counters(text)
        except (QueryError, AttributeError):
            pass
        self._stop_event(event)

    def _dispatch(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.query_one("#message-input", Input).value = ""
        self.query_one("#message-editor", TextArea).text = ""
        self._update_counters("")
        self._history.append(text)
        self._history_pos = -1
        self._draft = ""
        self.post_message(self.Submitted(text))

    def _history_back(self, inp: Input) -> None:
        if not self._history:
            return
        if self._history_pos == -1:
            self._draft = inp.value
            self._history_pos = len(self._history) - 1
        elif self._history_pos > 0:
            self._history_pos -= 1
        inp.value = self._history[self._history_pos]
        inp.cursor_position = len(inp.value)

    def _history_forward(self, inp: Input) -> None:
        if self._history_pos == -1:
            return
        if self._history_pos < len(self._history) - 1:
            self._history_pos += 1
            inp.value = self._history[self._history_pos]
        else:
            self._history_pos = -1
            inp.value = self._draft
        inp.cursor_position = len(inp.value)

    def set_disabled(self, disabled: bool) -> None:
        self.query_one("#message-input", Input).disabled = disabled
        self.query_one("#message-editor", TextArea).disabled = disabled
        prompt = self.query_one("#input-prompt", Label)
        if disabled:
            prompt.add_class("--busy")
        else:
            prompt.remove_class("--busy")

    def focus_input(self) -> None:
        if self._multiline:
            self.query_one("#message-editor", TextArea).focus()
        else:
            self.query_one("#message-input", Input).focus()

    def action_toggle_multiline(self) -> None:
        self.toggle_multiline()

    def toggle_multiline(self) -> None:
        single = self.query_one("#message-input", Input)
        editor = self.query_one("#message-editor", TextArea)
        if self._multiline:
            single.value = editor.text.replace("\n", " ").strip()
            editor.add_class("--hidden")
            single.remove_class("--hidden")
        else:
            editor.text = single.value
            single.add_class("--hidden")
            editor.remove_class("--hidden")
        self._multiline = not self._multiline
        self._update_counters(editor.text if self._multiline else single.value)
        self.focus_input()

    def _update_counters(self, value: str) -> None:
        text = str(value or "")
        count = len(text)
        lines = max(1, text.count("\n") + 1) if self._multiline else 1
        self.query_one("#char-count", Label).update(str(count) if count > 0 else "")
        mode_label = self.query_one("#input-mode-label", Label)
        if self._multiline:
            mode_label.update(f"multi {lines}L")
        else:
            mode_label.update("")

    def _set_placeholder(self, placeholder: str) -> None:
        for selector, widget_type in (
            ("#message-input", Input),
            ("#message-editor", TextArea),
        ):
            try:
                widget = self.query_one(selector, widget_type)
            except (QueryError, AttributeError):
                continue
            if hasattr(widget, "placeholder"):
                widget.placeholder = placeholder

    def _stop_event(self, event) -> None:
        try:
            event.stop()
        except AttributeError:
            pass
