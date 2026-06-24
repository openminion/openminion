from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList


class SessionOverlay(ModalScreen[str | None]):
    BINDINGS = [
        ("escape", "dismiss_overlay", "Close"),
        ("enter", "select_session", "Select"),
    ]

    def __init__(self, sessions: list[Any]) -> None:
        super().__init__()
        self._sessions = list(sessions)

    def compose(self) -> ComposeResult:
        yield Label("Recent sessions", id="focus-session-overlay-title")
        yield OptionList(
            *[self._label_for(session) for session in self._sessions],
            id="focus-session-overlay-list",
        )

    def action_dismiss_overlay(self) -> None:
        self.dismiss(None)

    def action_select_session(self) -> None:
        option_list = self.query_one("#focus-session-overlay-list", OptionList)
        highlighted = option_list.highlighted
        if highlighted is None or highlighted < 0 or highlighted >= len(self._sessions):
            self.dismiss(None)
            return
        session = self._sessions[highlighted]
        self.dismiss(str(getattr(session, "id", "") or ""))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        del event
        self.action_select_session()

    @staticmethod
    def _label_for(session: Any) -> str:
        session_id = str(getattr(session, "id", "") or "")
        updated_at = str(getattr(session, "updated_at", "") or "")
        return f"{session_id}  {updated_at}".strip()
