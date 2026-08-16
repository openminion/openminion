from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from openminion.cli.status.overview import (
    OperationsOverview,
    render_operations_overview,
)


class OverviewOverlay(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss_overlay", "Close")]

    def __init__(self, snapshot: OperationsOverview) -> None:
        super().__init__()
        self.snapshot = snapshot

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="focus-overview-panel"):
            yield Label("Operations overview", id="focus-overview-title")
            yield Static(
                render_operations_overview(self.snapshot),
                markup=False,
                id="focus-overview-content",
            )
            yield Label(
                "Details: /tasks · /telemetry latest · /trace list · Esc closes",
                id="focus-overview-hints",
            )

    def action_dismiss_overlay(self) -> None:
        self.dismiss(None)


__all__ = ["OverviewOverlay"]
