from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.css.query import QueryError
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label

from openminion.cli.status import format_primary_status_text as format_progress_label
from openminion.cli.status.models import PhaseStatusViewModel
from openminion.cli.presentation.styles import _SPINNER_FRAMES

_SPINNER = _SPINNER_FRAMES

DEFAULT_THINKING_LABEL = "thinking…"
DEFAULT_PROGRESS_FALLBACK = "Working..."


class ThinkingIndicator(Widget):
    is_thinking: reactive[bool] = reactive(False)
    status_label: reactive[str] = reactive(DEFAULT_THINKING_LABEL)
    elapsed_text: reactive[str] = reactive("")
    view_model: reactive[PhaseStatusViewModel | None] = reactive(None)
    _frame: reactive[int] = reactive(0)

    def compose(self) -> ComposeResult:
        yield Label("", id="thinking-label")

    def on_mount(self) -> None:
        self.set_interval(0.08, self._tick)
        self.styles.opacity = 0

    def _tick(self) -> None:
        if self.is_thinking:
            self._frame = (self._frame + 1) % len(_SPINNER_FRAMES)

    def _composite_label(self) -> str:
        view = self.view_model
        if view is not None:
            label = view.primary_text or DEFAULT_THINKING_LABEL
        else:
            label = self.status_label or DEFAULT_THINKING_LABEL
        elapsed = self.elapsed_text
        if elapsed:
            return f"{elapsed} | {label}"
        return label

    def watch__frame(self, frame: int) -> None:
        if self.is_thinking:
            ch = _SPINNER_FRAMES[frame]
            try:
                renderable = Text()
                renderable.append(
                    f"{ch}  ",
                    style="bold" if frame % 3 == 0 else "",
                )
                renderable.append(self._composite_label(), style="dim")
                self.query_one("#thinking-label", Label).update(renderable)
            except (QueryError, AttributeError):
                pass

    def watch_is_thinking(self, thinking: bool) -> None:
        if thinking:
            self.add_class("--visible")
            self.status_label = DEFAULT_THINKING_LABEL
            self.elapsed_text = ""
            self.view_model = None
            self._frame = 0
            self.styles.opacity = 1
            self.watch__frame(0)
        else:
            self.remove_class("--visible")
            self.styles.opacity = 0
            self.elapsed_text = ""
            self.view_model = None

    def watch_status_label(self, label: str) -> None:
        del label
        self._refresh_label()

    def watch_elapsed_text(self, _text: str) -> None:
        self._refresh_label()

    def watch_view_model(self, view: PhaseStatusViewModel | None) -> None:
        if view is None:
            return
        self.status_label = view.primary_text or DEFAULT_THINKING_LABEL
        self.elapsed_text = view.elapsed_text or ""
        self._refresh_label()

    def _refresh_label(self) -> None:
        if not self.is_thinking:
            return
        try:
            self.watch__frame(self._frame % len(_SPINNER_FRAMES))
        except (QueryError, AttributeError):
            pass


__all__ = [
    "DEFAULT_PROGRESS_FALLBACK",
    "DEFAULT_THINKING_LABEL",
    "ThinkingIndicator",
    "format_progress_label",
]
