from __future__ import annotations

from dataclasses import dataclass
import random

from textual.app import ComposeResult
from textual.css.query import QueryError
from textual.message import Message
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Label, Static
from textual.containers import Vertical
from textual.reactive import reactive

_SPARKLE_FRAMES = ("·", "✧", "·", "✦", "·", "✧")
_SIDEBAR_CATEGORY_SESSION: str = "session"
_SESSION_TYPE_TAGS = {
    "default": "def ",
    "named": "new ",
    "focus": "focus ",
    "room": "room ",
    "other": "other ",
}


class _SparkleWidget(Static):
    _frame: reactive[int] = reactive(0)
    _timer: Timer | None = None

    def __init__(self) -> None:
        super().__init__(_SPARKLE_FRAMES[0], classes="sidebar-sparkle")

    def on_mount(self) -> None:
        self._schedule_next_tick()

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(_SPARKLE_FRAMES)
        self._schedule_next_tick()

    def _schedule_next_tick(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        jitter = random.uniform(0.7, 1.3)
        self._timer = self.set_timer(0.9 * jitter, self._tick)

    def watch__frame(self, frame: int) -> None:
        self.update(_SPARKLE_FRAMES[frame])

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None


@dataclass
class SidebarItem:
    id: str
    label: str
    active: bool = False
    meta: dict | None = None


class _ClickableItem(Static):
    can_focus = True

    class Selected(Message):
        def __init__(self, item_id: str, category: str) -> None:
            super().__init__()
            self.item_id = item_id
            self.category = category

    class PreviewRequested(Message):
        def __init__(self, item_id: str, category: str, preview_text: str) -> None:
            super().__init__()
            self.item_id = item_id
            self.category = category
            self.preview_text = preview_text

    class PreviewCleared(Message):
        def __init__(self, item_id: str, category: str) -> None:
            super().__init__()
            self.item_id = item_id
            self.category = category

    def __init__(self, item: SidebarItem, category: str) -> None:
        prefix = "► " if item.active else "  "
        meta = item.meta or {}
        tag = ""
        if category == _SIDEBAR_CATEGORY_SESSION:
            session_type = str(meta.get("session_type", "") or "").strip()
            tag = _SESSION_TYPE_TAGS.get(session_type, "")
        preview = ""
        age_suffix = ""
        if category == _SIDEBAR_CATEGORY_SESSION:
            channel = str(meta.get("channel", "") or "")
            updated_raw = str(meta.get("updated_at", "") or "")
            age_text = ""
            if updated_raw:
                try:
                    from openminion.cli.tui.presentation.models import (
                        format_chat_timestamp,
                    )

                    age_text = format_chat_timestamp(updated_raw)
                except (TypeError, ValueError, OverflowError):
                    age_text = updated_raw[:16]
            parts = [p for p in [channel, age_text] if p]
            if parts:
                preview = f"  ({', '.join(parts)})"
            if item.active and age_text:
                age_suffix = f" · {age_text}"
        label = f"{prefix}{tag}{item.label}{preview}{age_suffix}"
        classes = "sidebar-item"
        if item.active:
            classes += " --active"
        if category == _SIDEBAR_CATEGORY_SESSION:
            session_type = str(meta.get("session_type", "") or "").strip()
            if session_type:
                classes += f" --stype-{session_type}"
        super().__init__(label, classes=classes)
        self._item = item
        self._category = category

    def on_click(self) -> None:
        self.post_message(self.Selected(self._item.id, self._category))

    def on_enter(self) -> None:
        self._post_preview_requested()

    def on_leave(self) -> None:
        self._post_preview_cleared()

    def on_focus(self) -> None:
        self._post_preview_requested()

    def on_blur(self) -> None:
        self._post_preview_cleared()

    def _preview_text(self) -> str:
        meta = self._item.meta or {}
        preview_lines = meta.get("preview_lines")
        if isinstance(preview_lines, list):
            lines = [
                str(line).strip()[:40] for line in preview_lines if str(line).strip()
            ]
            if lines:
                return "\n".join(lines[:3])
        parts = []
        for key in ("channel", "target", "status"):
            val = str(meta.get(key, "") or "").strip()
            if val:
                parts.append(f"{key}: {val}")
        updated = str(meta.get("updated_at", ""))[:19]
        if updated:
            parts.append(f"updated: {updated}")
        return "\n".join(parts)

    def _post_preview_requested(self) -> None:
        if self._category != _SIDEBAR_CATEGORY_SESSION:
            return
        self.post_message(
            self.PreviewRequested(
                self._item.id,
                self._category,
                self._preview_text(),
            )
        )

    def _post_preview_cleared(self) -> None:
        if self._category != _SIDEBAR_CATEGORY_SESSION:
            return
        self.post_message(self.PreviewCleared(self._item.id, self._category))


class _ToolItem(Static):
    def __init__(self, name: str, enabled: bool) -> None:
        icon = "✓" if enabled else "✗"
        css = "tool-enabled" if enabled else "tool-disabled"
        super().__init__(f"  {icon} {name}", classes=f"sidebar-item {css}")


class _SectionLabel(Label):
    def __init__(self, text: str, count: int | None = None) -> None:
        label = text.upper()
        if count is not None and count > 0:
            label = f"{label} ({count})"
        super().__init__(label, classes="sidebar-heading")


class _EmptySectionHint(Static):
    def __init__(self, text: str = "(none)") -> None:
        super().__init__(f"  {text}", classes="sidebar-item --empty")


class Sidebar(Widget):
    can_focus = False

    sessions: reactive[list[SidebarItem]] = reactive(list, recompose=True)
    agents: reactive[list[SidebarItem]] = reactive(list, recompose=True)
    tools: reactive[list[tuple[str, bool]]] = reactive(list, recompose=True)
    is_collapsed: reactive[bool] = reactive(False)

    def __init__(self, **kwargs) -> None:
        super().__init__(id="sidebar", **kwargs)
        self._preview_timer: Timer | None = None
        self._pending_preview_text = ""

    def compose(self) -> ComposeResult:
        with Vertical(classes="sidebar-section"):
            yield _SectionLabel("Sessions", count=len(self.sessions))
            if self.sessions:
                for item in self.sessions:
                    yield _ClickableItem(item, _SIDEBAR_CATEGORY_SESSION)
            else:
                yield _EmptySectionHint("(none)")

        with Vertical(classes="sidebar-section"):
            yield _SectionLabel("Agents", count=len(self.agents))
            if self.agents:
                for item in self.agents:
                    yield _ClickableItem(item, "agent")
            else:
                yield _EmptySectionHint("(none)")

        yield _SparkleWidget()

        with Vertical(classes="sidebar-section"):
            if self.tools:
                yield Label(self._tools_heading(), classes="sidebar-heading")
                for name, enabled in self.tools:
                    yield _ToolItem(name, enabled)
            else:
                yield _SectionLabel("Tools")
                yield _EmptySectionHint("(none)")

        yield Static("", id="sidebar-preview", classes="sidebar-preview --hidden")

    def watch_is_collapsed(self, collapsed: bool) -> None:
        if collapsed:
            self.add_class("--collapsed")
        else:
            self.remove_class("--collapsed")

    def toggle(self) -> None:
        self.is_collapsed = not self.is_collapsed

    def update_sessions(self, items: list[SidebarItem]) -> None:
        self.sessions = items

    def update_agents(self, items: list[SidebarItem]) -> None:
        self.agents = items

    def update_tools(self, tools: list[tuple[str, bool]]) -> None:
        self.tools = tools

    def set_active_session(self, session_id: str) -> None:
        self.sessions = self._with_active(self.sessions, session_id)

    def set_active_agent(self, agent_id: str) -> None:
        self.agents = self._with_active(self.agents, agent_id)

    def show_preview(self, text: str) -> None:
        try:
            preview = self.query_one("#sidebar-preview", Static)
            preview.update(text)
            if text:
                preview.remove_class("--hidden")
            else:
                preview.add_class("--hidden")
        except QueryError:
            pass

    def hide_preview(self) -> None:
        self._pending_preview_text = ""
        if self._preview_timer is not None:
            self._preview_timer.stop()
            self._preview_timer = None
        self.show_preview("")

    def on__clickable_item_preview_requested(
        self, event: _ClickableItem.PreviewRequested
    ) -> None:
        del event.item_id, event.category
        self._pending_preview_text = event.preview_text
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self._preview_timer = self.set_timer(0.3, self._show_pending_preview)

    def on__clickable_item_preview_cleared(
        self, event: _ClickableItem.PreviewCleared
    ) -> None:
        del event.item_id, event.category
        self.hide_preview()

    def _show_pending_preview(self) -> None:
        self.show_preview(self._pending_preview_text)

    def on_unmount(self) -> None:
        if self._preview_timer is not None:
            self._preview_timer.stop()
            self._preview_timer = None

    def _tools_heading(self) -> str:
        enabled_count = sum(1 for _, enabled in self.tools if enabled)
        total = len(self.tools)
        if enabled_count == total:
            return f"TOOLS ({total})"
        return f"TOOLS ({enabled_count}/{total})"

    def _with_active(
        self, items: list[SidebarItem], active_id: str
    ) -> list[SidebarItem]:
        return [
            SidebarItem(
                item.id, item.label, active=(item.id == active_id), meta=item.meta
            )
            for item in items
        ]
