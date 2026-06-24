from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer
from textual.css.query import QueryError
from textual.message import Message
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Input, Label, Static

from ..widgets import EmptyStatePulse

_TYPE_ICON = {
    "episodic": "📖",
    "semantic": "🧠",
    "working": "⚙",
}


class _MemoryRow(Static):
    class Clicked(Message):
        def __init__(self, record_id: str) -> None:
            super().__init__()
            self.record_id = record_id

    def __init__(self, record: dict, selected: bool = False) -> None:
        rtype = record.get("type", "")
        icon = _TYPE_ICON.get(rtype, "·")
        scope = record.get("scope", "")
        preview = record.get("content_preview", "")
        ts = record.get("ts", "")
        super().__init__(
            f"  {icon} [{scope:<8}] {preview:<40} {ts}",
            classes=f"memory-row memory-{rtype}",
            id=f"mem-{record.get('id', '')}",
        )
        self._record = record
        self.set_selected(selected)

    @property
    def record_id(self) -> str:
        return str(self._record.get("id", ""))

    def set_selected(self, selected: bool) -> None:
        if selected:
            self.add_class("selected")
        else:
            self.remove_class("selected")

    def on_click(self) -> None:
        self.post_message(self.Clicked(self.record_id))


class _CandidateRow(Static):
    def __init__(self, candidate: dict) -> None:
        raw_score = float(candidate.get("score", 0.0) or 0.0)
        score = max(0.0, min(1.0, raw_score))
        preview = candidate.get("content_preview", "")
        filled = max(0, min(10, int(score * 10)))
        bar = "█" * filled + "░" * (10 - filled)
        super().__init__(
            f"  [{bar}] {score:.2f}  {preview}",
            classes="candidate-row",
            id=f"cand-{candidate.get('id', '')}",
        )


class MemoryTab(Widget):
    can_focus = True

    BINDINGS = [
        ("/", "focus_search", "Search"),
    ]

    def __init__(self, provider=None) -> None:
        super().__init__(id="memory-tab")
        self._provider = provider
        self._records: list[dict] = []
        self._candidates: list[dict] = []
        self._search_timer: Timer | None = None
        self._search_generation = 0
        self._search_query = ""
        self._selected_record_id: str | None = None

    def compose(self) -> ComposeResult:
        if self._provider is None:
            yield EmptyStatePulse(classes="empty-state-pulse")
            yield Static(
                "No data — runtime provider not available.\n"
                "Start with a config to browse memory records.",
                classes="tab-empty-notice",
            )
            return
        with Horizontal(id="memory-body"):
            with ScrollableContainer(id="memory-list"):
                yield Input(
                    value=self._search_query,
                    placeholder="Search memory…",
                    id="memory-search",
                )
                yield Label("RECORDS", classes="sidebar-heading")
                if self._records:
                    for r in self._records:
                        yield _MemoryRow(
                            r,
                            selected=(r.get("id") == self._selected_record_id),
                        )
                else:
                    yield Label("No memory records", classes="dim-hint")

            with ScrollableContainer(id="memory-candidates"):
                yield Label("DETAIL", classes="sidebar-heading")
                selected = self._selected_record()
                if selected is not None:
                    yield Static(
                        str(
                            selected.get("content")
                            or selected.get("content_preview")
                            or ""
                        ),
                        classes="memory-detail-body",
                    )
                    yield Static(
                        str(selected.get("metadata") or {}),
                        classes="memory-detail-meta",
                    )
                else:
                    yield Label("Select a memory row to inspect it", classes="dim-hint")
                yield Label("CANDIDATES", classes="sidebar-heading")
                if self._candidates:
                    for c in self._candidates:
                        yield _CandidateRow(c)
                else:
                    yield Label("No candidates", classes="dim-hint")

    async def on_mount(self) -> None:
        if self._provider is not None:
            self._records = self._provider.list_records()
            self._candidates = self._provider.list_candidates()
            await self.recompose()
            self._sync_selected_record()
            self._sync_layout_mode()

    def on_resize(self, event) -> None:
        del event
        self.call_after_refresh(self._sync_layout_mode)

    def _sync_layout_mode(self) -> None:
        try:
            body = self.query_one("#memory-body", Horizontal)
        except QueryError:
            return
        if self.app.size.width < 100:
            body.add_class("--stacked")
        else:
            body.remove_class("--stacked")

    async def on__memory_row_clicked(self, event: _MemoryRow.Clicked) -> None:
        self._selected_record_id = event.record_id
        await self.recompose()
        self._sync_selected_record()

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "memory-search":
            return

        query = event.value.strip()
        self._search_query = query
        self._search_generation += 1
        generation = self._search_generation
        self._cancel_search_timer()

        if not query:
            if self._provider is not None:
                self._records = self._provider.list_records()
            if self._selected_record() is None:
                self._selected_record_id = None
            await self.recompose()
            self._sync_selected_record()
            return

        def _run_debounced() -> None:
            self.call_later(self._apply_search, query, generation)

        self._search_timer = self.set_timer(0.2, _run_debounced)

    async def _apply_search(self, query: str, generation: int) -> None:
        if generation != self._search_generation:
            return
        if self._provider is not None:
            try:
                self._records = self._provider.search(query)
            except Exception:
                self._records = []
        if self._selected_record() is None:
            self._selected_record_id = None
        await self.recompose()
        self._sync_selected_record()

    def _cancel_search_timer(self) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
            self._search_timer = None

    def on_unmount(self) -> None:
        self._cancel_search_timer()

    def action_focus_search(self) -> None:
        self.query_one("#memory-search", Input).focus()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            focused = self.app.focused
            if focused is not None and getattr(focused, "id", None) == "memory-search":
                focused.blur()
                event.stop()

    def _selected_record(self) -> dict | None:
        if not self._selected_record_id:
            return None
        for record in self._records:
            if str(record.get("id", "")) == self._selected_record_id:
                return record
        return None

    def _sync_selected_record(self) -> None:
        for row in self.query(_MemoryRow):
            row.set_selected(row.record_id == self._selected_record_id)
