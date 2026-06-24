from __future__ import annotations

import re

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.css.query import QueryError
from textual.message import Message
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Static

from ..widgets import EmptyStatePulse

_EVENT_ICON = {
    "llm.call.started": "🤖",
    "llm.call.completed": "✓",
    "tool.request": "⚙",
    "tool.response": "↩",
    "context.manifest.created": "📋",
    "memory.turn.recorded": "💾",
    "memory.capsule.refreshed": "🔄",
}


class _SessionRow(Static):
    class Clicked(Message):
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    def __init__(self, session: dict, selected: bool = False) -> None:
        sid = session.get("id", "?")[:14]
        age = str(session.get("age", "")).strip()
        turns = session.get("turn_count", "")
        count_str = f"  {turns}t" if turns else ""
        agent_id = str(session.get("agent_id", "")).strip()
        channel = str(session.get("channel", "")).strip()
        name = str(session.get("name", "")).strip()
        age_class = _session_age_class(age)

        display_name = name if name else sid
        meta = f"  {agent_id}/{channel}" if agent_id else ""
        label = f"  {display_name:<20}{meta:<18}  {age}{count_str}"

        raw_id = str(session.get("id", ""))
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "-", raw_id)
        super().__init__(
            label,
            classes=f"session-row {age_class}".strip(),
            id=f"sess-row-{safe_id}" if safe_id else None,
        )
        self._session = session
        self._selected = False
        self.set_selected(selected)

    @property
    def session_id(self) -> str:
        return str(self._session.get("id", ""))

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        if selected:
            self.add_class("selected")
        else:
            self.remove_class("selected")

    def on_click(self) -> None:
        self.post_message(self.Clicked(self.session_id))


class _EventRow(Static):
    def __init__(self, event: dict) -> None:
        ts = event.get("ts", "")
        kind = event.get("event_type", event.get("type", ""))
        icon = _EVENT_ICON.get(kind, "·")
        detail = event.get("detail", "")
        super().__init__(
            f"  {ts:<8} {icon} {kind:<36} {detail}",
            classes="event-row",
        )


class _SessionDetail(Static):
    def __init__(self, session: dict) -> None:
        super().__init__(classes="session-detail")
        self._session = session

    def compose(self) -> ComposeResult:
        s = self._session
        name = s.get("name", "")
        sid = s.get("id", "?")
        agent = s.get("agent_id", "—")
        channel = s.get("channel", "—")
        age = s.get("age", "—")
        turns = s.get("turn_count", "—")
        participants = s.get("participants", [])
        lines = []
        if name:
            lines.append(f"  Name:     {name}")
        lines.append(f"  ID:       {sid}")
        lines.append(f"  Agent:    {agent}")
        lines.append(f"  Channel:  {channel}")
        lines.append(f"  Age:      {age}    Turns: {turns}")
        if participants:
            lines.append("  Participants:")
            for participant in participants:
                participant_type = str(participant.get("participant_type", "")).strip()
                participant_id = str(participant.get("participant_id", "")).strip()
                role = str(participant.get("role", "")).strip()
                badge = f"[{participant_type}/{role}]".strip()
                lines.append(f"    {badge} {participant_id}")
        yield Label("\n".join(lines), classes="session-detail-text")


class _RenameModal(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, session_id: str, current_name: str) -> None:
        super().__init__()
        self._session_id = session_id
        self._current_name = current_name

    def compose(self) -> ComposeResult:
        with Vertical(id="rename-dialog"):
            yield Label(f"Rename session  {self._session_id[:14]}", id="rename-title")
            yield Input(
                value=self._current_name,
                placeholder="Session name (leave blank to remove)",
                id="rename-input",
            )
            with Horizontal(id="rename-buttons"):
                yield Button("Save", id="rename-save", variant="primary")
                yield Button("Cancel", id="rename-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "rename-save":
            inp = self.query_one("#rename-input", Input)
            self.dismiss(inp.value.strip())
        elif btn_id == "rename-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())


class _ConfirmDeleteSessionModal(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self._session_id = session_id

    def compose(self) -> ComposeResult:
        with Vertical(id="delete-session-dialog"):
            yield Label(
                f"Delete session {self._session_id[:14]}?",
                id="delete-session-title",
            )
            yield Label(
                "This permanently removes the session history.",
                classes="dim-hint",
            )
            with Horizontal(id="delete-session-buttons"):
                yield Button("Cancel", id="delete-session-cancel")
                yield Button("Delete", id="delete-session-confirm", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "delete-session-confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class SessionsTab(Widget):
    can_focus = True

    BINDINGS = [
        ("/", "focus_search", "Search"),
        ("d", "delete_session", "Delete"),
    ]

    class ResumeRequested(Message):
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    def __init__(self, provider=None) -> None:
        super().__init__(id="sessions-tab")
        self._provider = provider
        self._all_sessions: list[dict] = []
        self._sessions: list[dict] = []
        self._timeline: list[dict] = []
        self._selected_session_id: str | None = None
        self._event_filter = "all"
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        if self._provider is None:
            yield EmptyStatePulse(classes="empty-state-pulse")
            yield Static(
                "No data — runtime provider not available.\n"
                "Start with a config to browse sessions.",
                classes="tab-empty-notice",
            )
            return
        with Horizontal(id="sessions-body"):
            with ScrollableContainer(id="sessions-list"):
                yield Input(placeholder="Search sessions…", id="sessions-search")
                for s in self._sessions:
                    yield _SessionRow(
                        s, selected=(s.get("id") == self._selected_session_id)
                    )
            with Vertical(id="sessions-right"):
                selected_data = next(
                    (
                        s
                        for s in self._sessions
                        if s.get("id") == self._selected_session_id
                    ),
                    None,
                )
                if selected_data is not None:
                    yield _SessionDetail(selected_data)
                with ScrollableContainer(id="sessions-timeline"):
                    with Horizontal(id="sessions-filter-bar"):
                        for filter_name, label in (
                            ("all", "All"),
                            ("llm", "LLM"),
                            ("tool", "Tool"),
                            ("memory", "Memory"),
                            ("system", "System"),
                        ):
                            classes = "timeline-filter-btn"
                            if filter_name == self._event_filter:
                                classes += " --selected"
                            yield Button(
                                label,
                                id=f"timeline-filter-{filter_name}",
                                classes=classes,
                            )
                    visible_timeline = [
                        event
                        for event in self._timeline
                        if _timeline_event_matches_filter(event, self._event_filter)
                    ]
                    if visible_timeline:
                        for event in visible_timeline:
                            yield _EventRow(event)
                    else:
                        yield Label(
                            (
                                "No events match the active filter"
                                if self._selected_session_id
                                else "Select a session to view its timeline"
                            ),
                            classes="dim-hint",
                        )

    async def on_mount(self) -> None:
        if self._provider is not None:
            self._all_sessions = self._provider.list_all_sessions()
            self._sessions = list(self._all_sessions)
            await self.recompose()
            self._sync_selected_session()

    def on_show(self) -> None:
        if self._provider is not None and self._timer is None:
            self._timer = self.set_interval(10, self._refresh_tick)
        self.call_after_refresh(self._sync_layout_mode)

    def on_resize(self, event) -> None:
        del event
        self.call_after_refresh(self._sync_layout_mode)

    def on_hide(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _refresh_tick(self) -> None:
        self.run_worker(self._async_refresh(), exclusive=True)

    async def action_refresh(self) -> None:
        await self._async_refresh()

    async def _async_refresh(self) -> None:
        if self._provider is None:
            return
        self._all_sessions = self._provider.list_all_sessions()
        self._sessions = self._apply_session_query(self._current_query())
        await self.recompose()
        self._sync_selected_session()
        self._sync_layout_mode()

    async def on__session_row_clicked(self, event: _SessionRow.Clicked) -> None:
        self._selected_session_id = event.session_id
        self._timeline = []
        if self._provider is not None:
            self._timeline = self._provider.get_session_timeline(event.session_id)
        await self.recompose()
        self._sync_selected_session()

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "sessions-search":
            return
        self._sessions = self._apply_session_query(event.value)

        visible_ids = {str(s.get("id", "")) for s in self._sessions}
        if self._selected_session_id not in visible_ids:
            self._selected_session_id = None
            self._timeline = []

        await self.recompose()
        self._sync_selected_session()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("timeline-filter-"):
            return
        self._event_filter = button_id.removeprefix("timeline-filter-")
        await self.recompose()
        self._sync_selected_session()
        event.stop()

    def _sync_selected_session(self) -> None:
        for session_row in self.query(_SessionRow):
            session_row.set_selected(
                session_row.session_id == self._selected_session_id
            )

    def action_focus_search(self) -> None:
        self.query_one("#sessions-search", Input).focus()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            focused = self.app.focused
            if (
                focused is not None
                and getattr(focused, "id", None) == "sessions-search"
            ):
                focused.blur()
                event.stop()
            return

        if not self._selected_session_id:
            return

        if event.key == "r":
            self.post_message(SessionsTab.ResumeRequested(self._selected_session_id))
            event.stop()

        elif event.key == "n":
            self._start_rename(self._selected_session_id)
            event.stop()

        elif event.key == "x":
            self._close_selected_session(self._selected_session_id)
            event.stop()
        elif event.key == "d":
            self.action_delete_session()
            event.stop()

    def _start_rename(self, session_id: str) -> None:
        current_name = ""
        for s in self._all_sessions:
            if s.get("id") == session_id:
                current_name = str(s.get("name", ""))
                break

        def _on_rename(new_name: str | None) -> None:
            if new_name is None:
                return
            update = getattr(self._provider, "update_session_name", None)
            if callable(update):
                try:
                    update(session_id, new_name)
                except Exception:
                    pass
            for s in self._all_sessions:
                if s.get("id") == session_id:
                    s["name"] = new_name
            for s in self._sessions:
                if s.get("id") == session_id:
                    s["name"] = new_name
            self.app.call_later(self.recompose)

        self.app.push_screen(_RenameModal(session_id, current_name), _on_rename)

    def _close_selected_session(self, session_id: str) -> None:
        close = getattr(self._provider, "close_session", None)
        if callable(close):
            try:
                close(session_id)
            except Exception:
                pass
        self._all_sessions = [
            s for s in self._all_sessions if s.get("id") != session_id
        ]
        self._sessions = [s for s in self._sessions if s.get("id") != session_id]
        if self._selected_session_id == session_id:
            self._selected_session_id = None
            self._timeline = []
            self.app.call_later(self.recompose)

    def action_delete_session(self) -> None:
        if not self._selected_session_id:
            return

        def _on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            delete = getattr(self._provider, "delete_session", None)
            if callable(delete):
                try:
                    delete(self._selected_session_id)
                except Exception:
                    pass
            deleted_id = self._selected_session_id
            self._all_sessions = [
                s for s in self._all_sessions if s.get("id") != deleted_id
            ]
            self._sessions = [s for s in self._sessions if s.get("id") != deleted_id]
            self._selected_session_id = None
            self._timeline = []
            self.app.call_later(self.recompose)

        self.app.push_screen(
            _ConfirmDeleteSessionModal(self._selected_session_id),
            _on_confirm,
        )

    def _sync_layout_mode(self) -> None:
        try:
            body = self.query_one("#sessions-body", Horizontal)
        except QueryError:
            return
        if self.app.size.width < 100:
            body.add_class("--stacked")
        else:
            body.remove_class("--stacked")

    def _current_query(self) -> str:
        try:
            return self.query_one("#sessions-search", Input).value
        except QueryError:
            return ""

    def _apply_session_query(self, query: str) -> list[dict]:
        normalized = str(query or "").strip().lower()
        if not normalized:
            return list(self._all_sessions)
        return [
            session
            for session in self._all_sessions
            if self._session_matches_query(session, normalized)
        ]

    @staticmethod
    def _session_matches_query(session: dict, query: str) -> bool:
        return (
            str(session.get("id", "")).lower().startswith(query)
            or str(session.get("agent_id", "")).lower().startswith(query)
            or str(session.get("channel", "")).lower().startswith(query)
            or query in str(session.get("name", "")).lower()
        )


def _timeline_event_matches_filter(event: dict, event_filter: str) -> bool:
    normalized_filter = str(event_filter or "all").strip().lower()
    if normalized_filter == "all":
        return True
    event_type = str(event.get("event_type") or event.get("type") or "").lower()
    if normalized_filter == "llm":
        return event_type.startswith("llm.")
    if normalized_filter == "tool":
        return event_type.startswith("tool.")
    if normalized_filter == "memory":
        return event_type.startswith("memory.")
    if normalized_filter == "system":
        return not any(
            event_type.startswith(prefix) for prefix in ("llm.", "tool.", "memory.")
        )
    return True


def _session_age_class(age: str) -> str:
    if not age:
        return ""
    hours = _parse_age_to_hours(age)
    if hours is None:
        return ""
    if hours < 1:
        return "session-age-fresh"
    if hours > 24:
        return "session-age-stale"
    return "session-age-normal"


def _parse_age_to_hours(age: str) -> float | None:
    value = age.strip().lower()
    if not value:
        return None
    unit = value[-1]
    try:
        amount = float(value[:-1])
    except ValueError:
        return None
    if unit == "m":
        return amount / 60
    if unit == "h":
        return amount
    if unit == "d":
        return amount * 24
    return None
