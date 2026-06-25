from __future__ import annotations

from textual.css.query import QueryError

from .tokens import active_at_token, cursor_offset_for_text_area
from .widgets import (
    FileMentionOverlay,
    FocusComposer,
    SlashCommandOverlay,
)


class InputStateMixin:
    def _slash_overlay(self) -> SlashCommandOverlay | None:
        try:
            return self.query_one(SlashCommandOverlay)
        except QueryError:
            return None

    def _file_overlay(self) -> FileMentionOverlay | None:
        try:
            return self.query_one(FileMentionOverlay)
        except QueryError:
            return None

    def _active_editor_value_and_cursor(self) -> tuple[str, int]:
        from textual.widgets import Input, TextArea

        try:
            bar = self.query_one(FocusComposer)
        except QueryError:
            return ("", 0)
        if bool(getattr(bar, "_multiline", False)):
            try:
                editor = self.query_one("#focus-editor", TextArea)
                text = str(getattr(editor, "text", "") or "")
                line, col = editor.cursor_location
                return (text, cursor_offset_for_text_area(text, line, col))
            except (QueryError, AttributeError, TypeError, ValueError):
                return ("", 0)
        try:
            inp = self.query_one("#focus-input", Input)
            text = str(getattr(inp, "value", "") or "")
            cursor = int(getattr(inp, "cursor_position", len(text)))
            return (text, cursor)
        except (QueryError, AttributeError, TypeError, ValueError):
            return ("", 0)

    def _set_input_value(self, value: str) -> None:
        try:
            from textual.widgets import Input, TextArea

            bar = self.query_one(FocusComposer)
            if bool(getattr(bar, "_multiline", False)):
                editor = self.query_one("#focus-editor", TextArea)
                editor.text = value
                try:
                    last_line = max(0, value.count("\n"))
                    last_col = len(value.split("\n")[-1])
                    editor.move_cursor((last_line, last_col))
                except AttributeError:
                    pass
                return
            inp = self.query_one("#focus-input", Input)
            inp.value = value
            try:
                inp.cursor_position = len(value)
            except AttributeError:
                pass
        except QueryError:
            pass

    def _replace_active_token(self, *, start: int, end: int, replacement: str) -> None:
        from textual.widgets import Input, TextArea

        try:
            bar = self.query_one(FocusComposer)
        except QueryError:
            return
        if bool(getattr(bar, "_multiline", False)):
            try:
                editor = self.query_one("#focus-editor", TextArea)
                old = str(getattr(editor, "text", "") or "")
                new_text = old[:start] + replacement + old[end:]
                editor.text = new_text
                new_offset = start + len(replacement)
                head = new_text[:new_offset]
                line = head.count("\n")
                col = len(head.split("\n")[-1])
                try:
                    editor.move_cursor((line, col))
                except AttributeError:
                    pass
            except (QueryError, AttributeError):
                pass
            return
        try:
            inp = self.query_one("#focus-input", Input)
            old = str(getattr(inp, "value", "") or "")
            new_value = old[:start] + replacement + old[end:]
            inp.value = new_value
            try:
                inp.cursor_position = start + len(replacement)
            except AttributeError:
                pass
        except QueryError:
            pass

    def _apply_overlays_for_value(self, *, value: str, cursor_offset: int) -> None:
        slash_overlay = self._slash_overlay()
        file_overlay = self._file_overlay()
        at_token = active_at_token(value, cursor_offset) if file_overlay else None

        if self._suppress_slash_overlay_once or self._suppress_file_overlay_once:
            self._suppress_slash_overlay_once = False
            self._suppress_file_overlay_once = False
            if slash_overlay is not None:
                slash_overlay.visible = False
            if file_overlay is not None:
                file_overlay.visible = False
            return

        if at_token is not None and file_overlay is not None:
            file_overlay.query = at_token.text
            file_overlay.visible = True
            if slash_overlay is not None:
                slash_overlay.visible = False
            return
        if slash_overlay is not None and value.startswith("/"):
            slash_overlay.query = value
            slash_overlay.visible = True
            if file_overlay is not None:
                file_overlay.visible = False
            return
        if slash_overlay is not None:
            slash_overlay.visible = False
        if file_overlay is not None:
            file_overlay.visible = False

    def _sync_input_state(self) -> None:
        input_bar = self.query_one(FocusComposer)
        disabled = self._session_initializing or not bool(
            getattr(self._runtime, "is_bound", False)
        )
        input_bar.set_disabled(disabled)
        if not disabled:
            input_bar.focus_input()
