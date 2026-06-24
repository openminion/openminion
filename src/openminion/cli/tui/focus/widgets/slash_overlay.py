from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label


class SlashCommandOverlay(Widget):
    """Reactive list of matching slash commands."""

    visible: reactive[bool] = reactive(False)
    query: reactive[str] = reactive("")
    highlighted_index: reactive[int] = reactive(0)

    DEFAULT_CSS = """
    SlashCommandOverlay {
        layer: overlay;
        dock: bottom;
        offset-y: -3;
        height: auto;
        max-height: 8;
        padding: 0 1;
        background: $panel;
        border: tall $primary-darken-2;
        display: none;
    }
    SlashCommandOverlay.--visible { display: block; }
    SlashCommandOverlay > Label {
        height: 1;
        padding: 0 1;
        color: $text;
    }
    SlashCommandOverlay > Label.--highlighted {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    """

    def __init__(self) -> None:
        super().__init__(id="slash-overlay")
        self._items: list[tuple[str, str]] = []
        self._filtered: list[tuple[str, str]] = []

    def set_items(self, items: list[tuple[str, str]]) -> None:
        self._items = list(items)
        self._refilter()

    @property
    def filtered(self) -> list[tuple[str, str]]:
        return list(self._filtered)

    def selected(self) -> str | None:
        if not self._filtered:
            return None
        idx = max(0, min(self.highlighted_index, len(self._filtered) - 1))
        return self._filtered[idx][0]

    def move_highlight(self, delta: int) -> None:
        if not self._filtered:
            return
        new_idx = (self.highlighted_index + delta) % len(self._filtered)
        self.highlighted_index = new_idx

    def watch_visible(self, visible: bool) -> None:
        if visible:
            self.add_class("--visible")
        else:
            self.remove_class("--visible")

    def watch_query(self, _query: str) -> None:
        self._refilter()

    def watch_highlighted_index(self, _idx: int) -> None:
        self._render_rows()

    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        self._render_rows()

    def _refilter(self) -> None:
        query = (self.query or "").strip().lower()
        if not query or not query.startswith("/"):
            self._filtered = list(self._items)
        else:
            prefix_hits = [
                (name, desc)
                for (name, desc) in self._items
                if name.lower().startswith(query)
            ]
            substring_hits = [
                (name, desc)
                for (name, desc) in self._items
                if query[1:]
                and query[1:] in name.lower()
                and (name, desc) not in prefix_hits
            ]
            self._filtered = prefix_hits + substring_hits
        self.highlighted_index = 0
        self._render_rows()

    def _render_rows(self) -> None:
        try:
            for child in list(self.children):
                child.remove()
        except Exception:
            return
        if not self._filtered:
            self.mount(Label("(no matching commands)", classes="dim-hint"))
            return
        idx = max(0, min(self.highlighted_index, len(self._filtered) - 1))
        for i, (name, desc) in enumerate(self._filtered):
            classes = "--highlighted" if i == idx else ""
            self.mount(Label(f"  {name:<11} — {desc}", classes=classes))
