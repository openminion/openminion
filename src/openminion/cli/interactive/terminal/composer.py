from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, FormattedText
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.menus import CompletionsMenuControl
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

from openminion.cli.ux.input_normalization import normalize_multiline_input_text


_PROMPT_FRESH = "❯ "
_PROMPT_RESUMED = "↳ "
_PROMPT_DISABLED = "… "
_PROMPT_BUSY = "❯ "
_COMPLETION_MENU_ROWS = 10
_PLACEHOLDER_IDLE = "Ask anything · @ to mention a file · / for commands"
_PLACEHOLDER_BUSY = "Type to queue while the current turn runs · Esc interrupts"
_FOCUS_PROMPT_STYLE = Style.from_dict(
    {
        "bottom-toolbar": "noreverse bg:#111827 #8b949e",
        "bottom-toolbar.text": "noreverse bg:#111827 #8b949e",
        "placeholder": "italic #6b7280",
    }
)


def _call_safely(callback: object) -> None:
    if not callable(callback):
        return
    try:
        callback()
    except Exception:
        pass


class _ClickableCompletionMenuControl(CompletionsMenuControl):
    """Vertical completion menu that applies clicked entries."""

    def mouse_handler(self, mouse_event: MouseEvent) -> object:
        if mouse_event.event_type != MouseEventType.MOUSE_UP:
            return super().mouse_handler(mouse_event)

        buffer = get_app().current_buffer
        state = buffer.complete_state
        if state is None:
            return None

        index = mouse_event.position.y
        if 0 <= index < len(state.completions):
            buffer.apply_completion(state.completions[index])
        return None


def _install_clickable_completion_menu(session: PromptSession[str]) -> None:
    """Make prompt-toolkit's vertical completion menu click-to-apply."""

    seen: set[int] = set()

    def visit(node: object) -> None:
        node_id = id(node)
        if node_id in seen:
            return
        seen.add(node_id)

        if (
            isinstance(node, Window)
            and isinstance(node.content, CompletionsMenuControl)
            and not isinstance(node.content, _ClickableCompletionMenuControl)
        ):
            node.content = _ClickableCompletionMenuControl()

        content = getattr(node, "content", None)
        if content is not None:
            visit(content)

        alternative = getattr(node, "alternative_content", None)
        if alternative is not None:
            visit(alternative)

        for child in getattr(node, "children", ()) or ():
            visit(child)

        for float_item in getattr(node, "floats", ()) or ():
            visit(getattr(float_item, "content", None))

    try:
        visit(session.layout.container)
    except Exception:
        return


class _SlashAndAtCompleter(Completer):
    """Completer that fires on `/` (slash commands) or `@` (paths)."""

    def __init__(
        self,
        slash_commands: Iterable[str] | Mapping[str, str],
        path_completer: Completer | None = None,
    ) -> None:
        if isinstance(slash_commands, Mapping):
            self._slash_descriptions = {
                str(name): str(description)
                for name, description in slash_commands.items()
            }
            self._slashes = sorted(self._slash_descriptions)
        else:
            self._slashes = sorted({str(name) for name in slash_commands})
            self._slash_descriptions = {
                slash: "slash command" for slash in self._slashes
            }
        self._path_completer = path_completer

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/"):
            for slash in self._slashes:
                if slash.startswith(text):
                    yield Completion(
                        slash,
                        start_position=-len(text),
                        display=slash,
                        display_meta=self._slash_descriptions.get(
                            slash, "slash command"
                        ),
                    )
            return
        at_pos = text.rfind("@")
        if at_pos >= 0 and self._path_completer is not None:
            from prompt_toolkit.document import Document

            sub_doc = Document(text=text[at_pos + 1 :])
            for c in self._path_completer.get_completions(sub_doc, complete_event):
                yield Completion(
                    "@" + c.text,
                    start_position=c.start_position - 1,
                    display=c.display,
                    display_meta=c.display_meta,
                )


class TerminalComposer:
    """Prompt-toolkit input region for the default interactive CLI."""

    def __init__(
        self,
        *,
        slash_commands: Iterable[str] | Mapping[str, str] = (),
        bottom_toolbar: object = None,
        history_file: str | None = None,
        on_ctrl_l: object = None,
        on_ctrl_o: object = None,
        on_shift_tab: object = None,
        on_escape: Callable[[], None] | None = None,
        working_dir: str | None = None,
    ) -> None:
        self._on_escape = on_escape
        try:
            from prompt_toolkit.completion import PathCompleter

            path = PathCompleter(only_directories=False)
        except ImportError:
            path = None
        self._completer = _SlashAndAtCompleter(slash_commands, path)
        self._is_resumed = False
        self._disabled = False
        self._busy = False
        self._multiline = False
        self._bottom_toolbar = bottom_toolbar
        self._working_dir = working_dir
        kb = KeyBindings()

        @kb.add("c-j")
        def _(event):
            self._insert_newline(event)

        @kb.add("enter")
        def _(event):
            self._insert_newline(event)

        @kb.add("/")
        def _(event):
            self._insert_slash(event)

        @kb.add("<bracketed-paste>")
        def _(event):
            self._handle_bracketed_paste(event)

        # optional Ctrl+L / Ctrl+O bindings.
        if callable(on_ctrl_l):

            @kb.add("c-l")
            def _ctrl_l(event) -> None:
                _call_safely(on_ctrl_l)

        if callable(on_ctrl_o):

            @kb.add("c-o")
            def _ctrl_o(event) -> None:
                _call_safely(on_ctrl_o)

        if callable(on_shift_tab):

            @kb.add("s-tab")
            def _shift_tab(event) -> None:
                _call_safely(on_shift_tab)

        if callable(on_escape):

            @kb.add("escape")
            def _escape(event: Any) -> None:
                _call_safely(self._on_escape)
                event.app.invalidate()

        self._key_bindings = kb
        history = FileHistory(history_file) if history_file else None
        self._session: PromptSession[str] = PromptSession(
            history=history,
            key_bindings=kb,
            enable_history_search=True,
            mouse_support=True,
            reserve_space_for_menu=_COMPLETION_MENU_ROWS,
            style=_FOCUS_PROMPT_STYLE,
        )
        _install_clickable_completion_menu(self._session)

    def set_resumed(self, is_resumed: bool) -> None:
        self._is_resumed = bool(is_resumed)

    def set_disabled(self, disabled: bool) -> None:
        self._disabled = bool(disabled)

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)

    def focus_input(self) -> None:
        pass

    def invalidate(self) -> None:
        app = getattr(self._session, "app", None)
        invalidator = getattr(app, "invalidate", None)
        if callable(invalidator):
            invalidator()

    @property
    def prompt_session(self) -> PromptSession[str]:
        return self._session

    def toggle_multiline(self) -> None:
        self._multiline = not self._multiline

    def _insert_newline(self, event) -> None:
        if not self._multiline:
            event.app.current_buffer.validate_and_handle()
            return
        event.app.current_buffer.insert_text("\n")

    def _insert_slash(self, event) -> None:
        buffer = event.app.current_buffer
        buffer.insert_text("/")
        try:
            buffer.start_completion(select_first=False)
        except Exception:
            pass

    def _apply_pasted_text(self, text: str, *, buffer) -> None:
        text = normalize_multiline_input_text(text)
        if not text:
            return
        from pathlib import Path as _Path

        from openminion.cli.presentation.image_paste import (
            detect_image_path,
            format_image_reference,
        )

        working = _Path(self._working_dir) if self._working_dir else None
        detected = detect_image_path(text, working_dir=working)
        if detected is not None:
            buffer.insert_text(format_image_reference(detected, working_dir=working))
            return
        if "\n" in text and not self._multiline:
            self._multiline = True
        buffer.insert_text(text)

    def _handle_bracketed_paste(self, event) -> None:
        self._apply_pasted_text(
            str(getattr(event, "data", "") or ""),
            buffer=event.app.current_buffer,
        )

    async def read_line(self) -> str:
        """Read one line from the user."""
        if self._disabled:
            raise RuntimeError("composer disabled — refuse to read input")
        prompt = self._prompt_text()
        # Historical guard: patch_stdout(raw=True)
        with patch_stdout():
            try:
                text = await self._session.prompt_async(
                    FormattedText([("ansicyan", prompt)]),
                    completer=self._completer,
                    complete_while_typing=True,
                    multiline=Condition(lambda: self._multiline),
                    bottom_toolbar=self._formatted_bottom_toolbar,
                    placeholder=self._formatted_placeholder,
                )
            finally:
                self._multiline = False
        return str(text or "").rstrip("\n")

    def _formatted_bottom_toolbar(self):
        if self._bottom_toolbar is None:
            return None
        value = (
            self._bottom_toolbar()
            if callable(self._bottom_toolbar)
            else self._bottom_toolbar
        )
        if isinstance(value, str):
            if not value.strip():
                return None
            return ANSI(value)
        return value

    def _formatted_placeholder(self) -> FormattedText:
        return FormattedText(
            [
                (
                    "class:placeholder",
                    _PLACEHOLDER_BUSY if self._busy else _PLACEHOLDER_IDLE,
                )
            ]
        )

    def _prompt_text(self) -> str:
        if self._disabled:
            return _PROMPT_DISABLED
        if self._busy:
            return _PROMPT_BUSY
        if self._is_resumed:
            return _PROMPT_RESUMED
        return _PROMPT_FRESH
