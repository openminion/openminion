from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.css.query import QueryError
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Label

from openminion.cli.status import format_primary_status_text as format_progress_label
from openminion.cli.status.models import PhaseStatusViewModel
from openminion.cli.presentation.styles import _SPINNER_FRAMES
from openminion.cli.presentation.animation import AnimationSpec

_SPINNER = _SPINNER_FRAMES

DEFAULT_THINKING_LABEL = "thinking…"
DEFAULT_PROGRESS_FALLBACK = "Working..."


class ThinkingIndicator(Widget):
    is_thinking: reactive[bool] = reactive(False)
    status_label: reactive[str] = reactive(DEFAULT_THINKING_LABEL)
    elapsed_text: reactive[str] = reactive("")
    view_model: reactive[PhaseStatusViewModel | None] = reactive(None)
    _frame: reactive[int] = reactive(0)

    def __init__(
        self,
        *,
        animation: AnimationSpec | None = None,
        progress: str = "full",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._animation = animation
        self._progress = progress if progress in ("full", "minimal", "off") else "full"
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Label("", id="thinking-label")

    def on_mount(self) -> None:
        self._sync_timer()
        self.styles.opacity = 0

    def _tick(self) -> None:
        if self.is_thinking:
            frames = self._frames()
            self._frame = (self._frame + 1) % len(frames)

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
            ch = self._current_frame(frame)
            try:
                renderable = Text()
                if ch:
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
            if self._progress == "off":
                self.styles.opacity = 0
                return
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
            self.watch__frame(self._frame % len(self._frames()))
        except (QueryError, AttributeError):
            pass

    def update_animation(self, animation: AnimationSpec, *, progress: str | None = None) -> None:
        self._animation = animation
        if progress in ("full", "minimal", "off"):
            self._progress = progress
        self._frame = 0
        self._sync_timer()
        self._refresh_label()

    def _frames(self) -> tuple[str, ...]:
        if self._animation is not None and self._animation.frames:
            return self._animation.frames
        return tuple(_SPINNER_FRAMES)

    def _current_frame(self, frame: int) -> str:
        if self._progress == "off":
            return ""
        if self._progress == "minimal":
            return "•"
        frames = self._frames()
        return frames[frame % len(frames)]

    def _sync_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        interval = (self._animation.interval_ms if self._animation else 80) / 1000
        self._timer = self.set_interval(interval, self._tick)


__all__ = [
    "DEFAULT_PROGRESS_FALLBACK",
    "DEFAULT_THINKING_LABEL",
    "ThinkingIndicator",
    "format_progress_label",
]
