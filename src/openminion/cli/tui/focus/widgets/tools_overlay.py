from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList


class ToolsOverlay(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss_overlay", "Close")]

    def __init__(self, tools: list[tuple[str, bool]]) -> None:
        super().__init__()
        self._tools = list(tools)

    def compose(self) -> ComposeResult:
        yield Label("Available tools", id="focus-tools-overlay-title")
        yield OptionList(
            *[
                f"{'enabled' if enabled else 'disabled'}  {name}"
                for name, enabled in self._tools
            ],
            id="focus-tools-overlay-list",
        )

    def action_dismiss_overlay(self) -> None:
        self.dismiss(None)
