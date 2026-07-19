from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.css.query import QueryError
from textual.widget import Widget
from textual.widgets import Static


class FocusDebugPane(Widget):
    def __init__(self) -> None:
        super().__init__(id="focus-debug-pane")
        self._payload: dict[str, Any] = {}
        self.add_class("--hidden")

    def compose(self) -> ComposeResult:
        yield Static("", id="focus-debug-content")

    def toggle(self) -> None:
        if self.has_class("--hidden"):
            self.remove_class("--hidden")
        else:
            self.add_class("--hidden")

    def set_payload(self, payload: dict[str, Any]) -> None:
        self._payload = dict(payload or {})
        try:
            self.query_one("#focus-debug-content", Static).update(
                json.dumps(self._payload, indent=2, sort_keys=True, default=str)
            )
        except QueryError:
            pass
