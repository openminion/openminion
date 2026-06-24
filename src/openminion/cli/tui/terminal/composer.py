from __future__ import annotations

from collections.abc import Iterable, Mapping

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, FormattedText
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from openminion.cli.ux.input_normalization import normalize_multiline_input_text


_PROMPT_FRESH = "❯ "
_PROMPT_RESUMED = "↳ "
_PROMPT_DISABLED = "… "


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
    """Prompt-toolkit-based input region for the terminal-flow shell."""

    def __init__(
        self,
        *,
        slash_commands: Iterable[str] | Mapping[str, str] = (),
        bottom_toolbar: object = None,
        history_file: str | None = None,
        on_ctrl_l: object = None,
        on_ctrl_o: object = None,
        on_shift_tab: object = None,
        working_dir: str | None = None,
    ) -> None:
        try:
            from prompt_toolkit.completion import PathCompleter

            path = PathCompleter(only_directories=False)
        except ImportError:
            path = None
        self._completer = _SlashAndAtCompleter(slash_commands, path)
        self._is_resumed = False
        self._disabled = False
        self._multiline = False
        self._bottom_toolbar = bottom_toolbar
        self._working_dir = working_dir
        kb = KeyBindings()

        @kb.add("c-j")
        def _(event):
            self._insert_newline(event)

        @kb.add("enter")
        def _(event):
            event.current_buffer.validate_and_handle()

        @kb.add("<bracketed-paste>")
        def _(event):
            self._handle_bracketed_paste(event)

        # optional Ctrl+L / Ctrl+O bindings.
        if callable(on_ctrl_l):

            @kb.add("c-l")
            def _ctrl_l(event) -> None:
                try:
                    on_ctrl_l()
                except Exception:
                    pass

        if callable(on_ctrl_o):

            @kb.add("c-o")
            def _ctrl_o(event) -> None:
                try:
                    on_ctrl_o()
                except Exception:
                    pass

        if callable(on_shift_tab):

            @kb.add("s-tab")
            def _shift_tab(event) -> None:
                try:
                    on_shift_tab()
                except Exception:
                    pass

        self._key_bindings = kb
        history = FileHistory(history_file) if history_file else None
        self._session = PromptSession(
            history=history,
            key_bindings=kb,
            enable_history_search=True,
        )

    def set_resumed(self, is_resumed: bool) -> None:
        self._is_resumed = bool(is_resumed)

    def set_disabled(self, disabled: bool) -> None:
        self._disabled = bool(disabled)

    def focus_input(self) -> None:
        pass

    def toggle_multiline(self) -> None:
        self._multiline = not self._multiline

    def _insert_newline(self, event) -> None:
        if not self._multiline:
            self._multiline = True
        event.app.current_buffer.insert_text("\n")

    def _apply_pasted_text(self, text: str, *, buffer) -> None:
        text = normalize_multiline_input_text(text)
        if not text:
            return
        from pathlib import Path as _Path

        from openminion.cli.tui.presentation.image_paste import (
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
        with patch_stdout():
            try:
                text = await self._session.prompt_async(
                    FormattedText([("ansicyan", prompt)]),
                    completer=self._completer,
                    complete_while_typing=True,
                    multiline=Condition(lambda: self._multiline),
                    bottom_toolbar=self._formatted_bottom_toolbar,
                    placeholder=FormattedText(
                        [
                            (
                                "class:placeholder",
                                "Ask anything · @ to mention a file · / for commands",
                            )
                        ]
                    ),
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
            return ANSI(value)
        return value

    def _prompt_text(self) -> str:
        if self._disabled:
            return _PROMPT_DISABLED
        if self._is_resumed:
            return _PROMPT_RESUMED
        return _PROMPT_FRESH
