from __future__ import annotations

from typing import Any

from textual import events
from textual.css.query import QueryError

from .tokens import active_at_token


class FocusOverlayInteractionMixin:
    def _consume_visible_file_overlay_submission(self) -> bool:
        file_overlay = self._file_overlay()
        if file_overlay is None or not file_overlay.visible:
            return False
        selected = file_overlay.selected()
        if not selected:
            return False
        live_value, live_cursor = self._active_editor_value_and_cursor()
        token = active_at_token(live_value, live_cursor)
        if token is not None:
            self._suppress_file_overlay_once = True
            self._replace_active_token(
                start=token.start,
                end=token.end,
                replacement=selected + " ",
            )
        file_overlay.visible = False
        return True

    def _consume_visible_slash_overlay_submission(self) -> bool:
        overlay = self._slash_overlay()
        if overlay is None or not overlay.visible:
            return False
        selected = overlay.selected()
        if not selected:
            return False
        self._suppress_slash_overlay_once = True
        self._set_input_value(selected + " ")
        overlay.visible = False
        return True

    def _move_visible_overlay_highlight(
        self,
        overlay: Any,
        *,
        up_keys: tuple[str, ...],
        down_keys: tuple[str, ...],
        event: events.Key,
    ) -> bool:
        key = getattr(event, "key", "") or ""
        if key in up_keys:
            overlay.move_highlight(-1)
        elif key in down_keys:
            overlay.move_highlight(1)
        else:
            return False
        event.stop()
        try:
            event.prevent_default()
        except (QueryError, AttributeError):
            pass
        return True

    def on_key(self, event: events.Key) -> None:
        file_overlay = self._file_overlay()
        if file_overlay is not None and file_overlay.visible:
            if self._move_visible_overlay_highlight(
                file_overlay,
                up_keys=("up", "ctrl+p"),
                down_keys=("down", "ctrl+n"),
                event=event,
            ):
                return
        overlay = self._slash_overlay()
        if overlay is not None and overlay.visible:
            if self._move_visible_overlay_highlight(
                overlay,
                up_keys=("up", "ctrl+p"),
                down_keys=("down", "ctrl+n"),
                event=event,
            ):
                return
