from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.css.query import QueryError
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Label, TextArea

from openminion.cli.ux.input_normalization import normalize_multiline_input_text


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


class _FocusComposerInput(Input):
    """`Input` variant that releases shell-reserved keys."""

    BINDINGS = _strip_reserved(Input.BINDINGS)


class _FocusComposerEditorSubmitted(Message):
    """Internal multi-line editor submit event."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class _FocusComposerEditor(TextArea):
    """`TextArea` for multi-line composition. Enter submits."""

    BINDINGS = [("enter", "submit", "Submit")]

    def action_submit(self) -> None:
        self.post_message(_FocusComposerEditorSubmitted(self.text))


class FocusComposer(Widget):
    """Focus-native bottom input area."""

    PLACEHOLDER_FRESH = "Ask anything · @ to mention a file · / for commands"
    PLACEHOLDER_RESUMED = "Reply, or / for commands · ↑ for history"

    class Submitted(Message):
        """Emitted on Enter in single-line or multi-line mode."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    BINDINGS = [
        ("ctrl+l", "toggle_multiline", "Multiline"),
        ("ctrl+j", "newline", "Newline"),
    ]

    DEFAULT_CSS = """
    FocusComposer {
        height: auto;
        border: round $accent;
        padding: 0 1;
        background: $surface;
    }
    FocusComposer > #focus-prompt {
        width: 2;
        color: $accent;
        padding: 0;
    }
    FocusComposer > Input { background: transparent; }
    FocusComposer > TextArea { background: transparent; height: auto; }
    FocusComposer > .--hidden { display: none; }
    """

    def __init__(
        self,
        placeholder: str | None = None,
        *,
        is_resumed: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(id=kwargs.pop("id", "focus-composer"), **kwargs)
        self._is_resumed = bool(is_resumed)
        self._placeholder = self._resolve_placeholder(placeholder)
        self._history: list[str] = []
        self._history_pos: int = -1
        self._draft: str = ""
        self._multiline = False
        self._disabled = False

    def _resolve_placeholder(self, override: str | None) -> str:
        if override is not None:
            return override
        return self.PLACEHOLDER_RESUMED if self._is_resumed else self.PLACEHOLDER_FRESH

    def set_resumed(self, is_resumed: bool) -> None:
        """Update the resumed flag and re-pick placeholder."""
        self._is_resumed = bool(is_resumed)
        new_placeholder = self._resolve_placeholder(None)
        if new_placeholder == self._placeholder:
            return
        self._placeholder = new_placeholder
        try:
            self.query_one("#focus-input", Input).placeholder = new_placeholder
        except (QueryError, AttributeError):
            pass
        try:
            ed = self.query_one("#focus-editor", TextArea)
            if hasattr(ed, "placeholder"):
                ed.placeholder = new_placeholder
        except (QueryError, AttributeError):
            pass

    def set_disabled(self, disabled: bool) -> None:
        """Gate input during turn-in-flight."""
        self._disabled = bool(disabled)
        try:
            self.query_one("#focus-input", Input).disabled = self._disabled
        except (QueryError, AttributeError):
            pass
        try:
            self.query_one("#focus-editor", TextArea).disabled = self._disabled
        except (QueryError, AttributeError):
            pass

    def focus_input(self) -> None:
        """Move focus to the active input (single-line or multi-line)."""
        try:
            if self._multiline:
                self.query_one("#focus-editor", TextArea).focus()
            else:
                self.query_one("#focus-input", Input).focus()
        except (QueryError, AttributeError):
            pass

    def toggle_multiline(self) -> None:
        """Swap single-line ↔ multi-line composition."""
        self._multiline = not self._multiline
        try:
            inp = self.query_one("#focus-input", Input)
            ed = self.query_one("#focus-editor", TextArea)
            if self._multiline:
                ed.text = inp.value
                inp.add_class("--hidden")
                ed.remove_class("--hidden")
                ed.focus()
            else:
                inp.value = ed.text.rstrip("\n")
                ed.add_class("--hidden")
                inp.remove_class("--hidden")
                inp.focus()
        except (QueryError, AttributeError):
            pass

    def action_toggle_multiline(self) -> None:
        self.toggle_multiline()

    def action_newline(self) -> None:
        """Insert a newline, promoting single-line mode to multiline when needed."""
        if not self._multiline:
            try:
                single = self.query_one("#focus-input", Input)
            except (QueryError, AttributeError):
                self.toggle_multiline()
                return
            value = single.value or ""
            cursor = max(
                0,
                min(
                    int(getattr(single, "cursor_position", len(value)) or 0), len(value)
                ),
            )
            single.value = value[:cursor] + "\n" + value[cursor:]
            if not self._multiline:
                self.toggle_multiline()
            try:
                editor = self.query_one("#focus-editor", TextArea)
                if hasattr(editor, "move_cursor"):
                    editor.move_cursor((1, 0))
            except (QueryError, AttributeError):
                pass
            return
        try:
            editor = self.query_one("#focus-editor", TextArea)
        except (QueryError, AttributeError):
            return
        if hasattr(editor, "insert"):
            try:
                editor.insert("\n")
                return
            except (QueryError, AttributeError):
                pass
        editor.text = (editor.text or "") + "\n"

    def compose(self) -> ComposeResult:
        yield Label("❯", id="focus-prompt")
        yield _FocusComposerInput(placeholder=self._placeholder, id="focus-input")
        yield _FocusComposerEditor(
            text="",
            id="focus-editor",
            classes="--hidden",
        )

    def on_mount(self) -> None:
        self.focus_input()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "focus-input":
            event.stop()
            value = event.value or ""
            if value.rstrip().endswith("\\"):
                self._handle_line_continuation(value)
                return
            self._dispatch(value)

    def _handle_line_continuation(self, value: str) -> None:
        """Promote a trailing backslash continuation into multiline mode."""
        try:
            single = self.query_one("#focus-input", Input)
        except (QueryError, AttributeError):
            return
        stripped = value.rstrip()
        if stripped.endswith("\\"):
            stripped = stripped[:-1].rstrip()
        single.value = stripped
        if not self._multiline:
            self.toggle_multiline()
        try:
            editor = self.query_one("#focus-editor", TextArea)
            editor.text = stripped + "\n"
            if hasattr(editor, "move_cursor"):
                editor.move_cursor((1, 0))
        except (QueryError, AttributeError):
            pass

    def on_key(self, event) -> None:
        """Promote Shift+Enter to multiline input."""
        if not self._multiline and getattr(event, "key", "") == "shift+enter":
            self._handle_shift_enter(event)

    def _handle_shift_enter(self, event) -> None:
        try:
            single = self.query_one("#focus-input", Input)
        except (QueryError, AttributeError):
            return
        original = single.value or ""
        try:
            cursor = int(single.cursor_position)
        except (QueryError, AttributeError):
            cursor = len(original)
        cursor = max(0, min(cursor, len(original)))
        with_newline = original[:cursor] + "\n" + original[cursor:]
        single.value = with_newline
        if not self._multiline:
            self.toggle_multiline()
        try:
            editor = self.query_one("#focus-editor", TextArea)
            if hasattr(editor, "move_cursor"):
                editor.move_cursor((1, 0))
        except (QueryError, AttributeError):
            pass
        try:
            event.stop()
        except (QueryError, AttributeError):
            pass

    def on_paste(self, event) -> None:
        """Auto-promote multiline pastes into the editor surface."""
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
                self.query_one("#focus-input", Input).value = ""
            except (QueryError, AttributeError):
                pass
            self.toggle_multiline()
        try:
            editor = self.query_one("#focus-editor", TextArea)
            editor.text = text
        except (QueryError, AttributeError):
            pass
        try:
            event.stop()
        except (QueryError, AttributeError):
            pass

    def on_focus_composer_editor_submitted(
        self, event: _FocusComposerEditorSubmitted
    ) -> None:
        event.stop()
        self._dispatch(event.text)

    def _dispatch(self, text: str) -> None:
        body = (text or "").rstrip("\n")
        if not body.strip():
            return
        if self._disabled:
            return
        if not self._history or self._history[-1] != body:
            self._history.append(body)
        self._history_pos = -1
        try:
            self.query_one("#focus-input", Input).value = ""
        except (QueryError, AttributeError):
            pass
        try:
            self.query_one("#focus-editor", TextArea).text = ""
        except (QueryError, AttributeError):
            pass
        self.post_message(self.Submitted(body))


__all__ = ["FocusComposer"]
