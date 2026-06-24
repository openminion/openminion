from __future__ import annotations

from textual.message import Message
from textual.widgets import Static


class SelectableRow(Static):
    class Clicked(Message):
        def __init__(self, row_key: str) -> None:
            super().__init__()
            self.row_key = row_key

    def __init__(
        self,
        text: str,
        *,
        row_key: str,
        dom_id: str,
        classes: str = "",
    ) -> None:
        super().__init__(text, classes=classes, id=dom_id)
        self._row_key = row_key

    @property
    def row_key(self) -> str:
        return self._row_key

    def on_click(self) -> None:
        self.post_message(self.Clicked(self._row_key))


__all__ = ["SelectableRow"]
