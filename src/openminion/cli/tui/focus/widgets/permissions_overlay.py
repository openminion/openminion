from __future__ import annotations

from textual.app import ComposeResult
from textual.css.query import QueryError
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList

from openminion.cli.tui.presentation.permissions import (
    PERMISSION_CHOICE_FULL_ACCESS,
    PERMISSION_MENU_CHOICES,
)


class PermissionsOverlay(ModalScreen[tuple[str, bool] | None]):
    BINDINGS = [
        ("escape", "dismiss_overlay", "Close"),
        ("enter", "select_permission", "Select"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._full_access_armed = False

    def compose(self) -> ComposeResult:
        yield Label("Session permissions", id="focus-permissions-overlay-title")
        yield Label(
            "Choose a session posture. Full access requires selecting it twice.",
            id="focus-permissions-overlay-note",
        )
        yield OptionList(
            *[
                f"{choice.label} — {choice.description}"
                for choice in PERMISSION_MENU_CHOICES
            ],
            id="focus-permissions-overlay-list",
        )

    def action_dismiss_overlay(self) -> None:
        self.dismiss(None)

    def action_select_permission(self) -> None:
        option_list = self.query_one("#focus-permissions-overlay-list", OptionList)
        highlighted = option_list.highlighted
        if highlighted is None or highlighted < 0:
            self.dismiss(None)
            return
        if highlighted >= len(PERMISSION_MENU_CHOICES):
            self.dismiss(None)
            return
        choice = PERMISSION_MENU_CHOICES[highlighted]
        if choice.choice_id == PERMISSION_CHOICE_FULL_ACCESS:
            if not self._full_access_armed:
                self._full_access_armed = True
                self._set_note(
                    "Full access disables approval prompts for this session. "
                    "Select Full access again to confirm."
                )
                return
            self.dismiss((choice.choice_id, True))
            return
        self.dismiss((choice.choice_id, False))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        del event
        self.action_select_permission()

    def _set_note(self, text: str) -> None:
        try:
            self.query_one("#focus-permissions-overlay-note", Label).update(text)
        except QueryError:
            pass
