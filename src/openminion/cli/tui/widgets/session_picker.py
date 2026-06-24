from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class _PickerRow(Static):
    can_focus = True

    class Selected(Message):
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    def __init__(self, session: dict, index: int) -> None:
        sid = str(session.get("id", ""))
        name = str(session.get("name", "")).strip()
        agent_id = str(session.get("agent_id", "")).strip()
        age = str(session.get("age", "")).strip()
        turns = session.get("turn_count", 0)
        display = name if name else sid[:20]
        meta = f"  {agent_id}  {age}  {turns}t" if agent_id else f"  {age}  {turns}t"
        super().__init__(
            f"  {display:<24}{meta}",
            classes="picker-row",
        )
        self._session_id = sid
        self._index = index

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def index(self) -> int:
        return self._index

    def on_click(self) -> None:
        self.post_message(self.Selected(self._session_id))


class _NewSessionRow(Static):
    can_focus = True

    class Clicked(Message):
        pass

    def __init__(self) -> None:
        super().__init__("  N  New session", classes="picker-new-option")

    def on_click(self) -> None:
        self.post_message(self.Clicked())


class SessionPickerModal(ModalScreen[str | None]):
    BINDINGS = [
        ("n", "new_session", "New session"),
        ("escape", "new_session", "New session"),
        ("up", "move_up", "Up"),
        ("down", "move_down", "Down"),
        ("enter", "select", "Select"),
    ]

    def __init__(self, sessions: list[dict]) -> None:
        super().__init__()
        self._sessions = sessions[:10]
        self._selected_index = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Label(
                "Resume a session or start new  (↑↓ Enter N)", id="picker-title"
            )
            if self._sessions:
                for i, session in enumerate(self._sessions):
                    yield _PickerRow(session, i)
            else:
                yield Label("No prior sessions found.", classes="dim-hint")
            yield _NewSessionRow()

    def on_mount(self) -> None:
        self._highlight()

    def _highlight(self) -> None:
        rows = list(self.query(_PickerRow))
        for row in rows:
            if row.index == self._selected_index:
                row.add_class("picker-selected")
            else:
                row.remove_class("picker-selected")

    def action_move_up(self) -> None:
        self._move_selection(-1)

    def action_move_down(self) -> None:
        self._move_selection(1)

    def action_select(self) -> None:
        self.dismiss(self._selected_session_id())

    def on__picker_row_selected(self, event: _PickerRow.Selected) -> None:
        self.dismiss(event.session_id)

    def on__new_session_row_clicked(self, event: _NewSessionRow.Clicked) -> None:
        self.dismiss(None)

    def action_new_session(self) -> None:
        self.dismiss(None)

    def _move_selection(self, step: int) -> None:
        if not self._sessions:
            return
        new_index = min(max(0, self._selected_index + step), len(self._sessions) - 1)
        if new_index == self._selected_index:
            return
        self._selected_index = new_index
        self._highlight()

    def _selected_session_id(self) -> str | None:
        if not (self._sessions and 0 <= self._selected_index < len(self._sessions)):
            return None
        return str(self._sessions[self._selected_index].get("id", ""))
