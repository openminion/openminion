from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label

RESULT_LIMIT = 50


class FileMentionOverlay(Widget):
    """Reactive list of matching workspace files for `@` mentions.

    The screen seeds cached items once, then drives visibility, query updates,
    and keyboard navigation while this widget handles bounded filtering.
    """

    visible: reactive[bool] = reactive(False)
    query: reactive[str] = reactive("")
    highlighted_index: reactive[int] = reactive(0)

    DEFAULT_CSS = """
    FileMentionOverlay {
        layer: overlay;
        dock: bottom;
        offset-y: -3;
        height: auto;
        max-height: 12;
        padding: 0 1;
        background: $panel;
        border: tall $primary-darken-2;
        display: none;
    }
    FileMentionOverlay.--visible { display: block; }
    FileMentionOverlay > Label {
        height: 1;
        padding: 0 1;
        color: $text;
    }
    FileMentionOverlay > Label.--highlighted {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    """

    def __init__(self) -> None:
        super().__init__(id="file-mention-overlay")
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
        raw = self.query or ""
        query = raw[1:] if raw.startswith("@") else raw
        query = query.strip().lower()

        if not query:
            self._filtered = list(self._items[:RESULT_LIMIT])
        else:
            tier1: list[tuple[str, str]] = []
            tier2: list[tuple[str, str]] = []
            for entry in self._items:
                rel_lower = entry[0].lower()
                if rel_lower.startswith(query):
                    tier1.append(entry)
                elif query in rel_lower:
                    tier2.append(entry)
                if len(tier1) + len(tier2) >= RESULT_LIMIT * 2:
                    break
            self._filtered = (tier1 + tier2)[:RESULT_LIMIT]

        self.highlighted_index = 0
        self._render_rows()

    def _render_rows(self) -> None:
        try:
            for child in list(self.children):
                child.remove()
        except Exception:
            return
        if not self._filtered:
            self.mount(Label("(no matching files)", classes="dim-hint"))
            return
        idx = max(0, min(self.highlighted_index, len(self._filtered) - 1))
        for i, (rel, _abs) in enumerate(self._filtered):
            classes = "--highlighted" if i == idx else ""
            self.mount(Label(f"  {rel}", classes=classes))


__all__ = ["FileMentionOverlay", "RESULT_LIMIT"]
