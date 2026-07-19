from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.css.query import QueryError
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Label


class ChatSearchBar(Widget):
    class SearchChanged(Message):
        def __init__(self, query: str) -> None:
            super().__init__()
            self.query = query

    class SearchClosed(Message):
        pass

    DEFAULT_CSS = "ChatSearchBar { height: auto; display: none; }"

    def compose(self) -> ComposeResult:
        with Horizontal(classes="chat-search-row"):
            yield Label("search:", classes="chat-search-icon")
            yield Input(placeholder="Search messages...", id="chat-search-input")
            yield Label("Esc close", classes="chat-search-hint")

    def show(self) -> None:
        self.display = True
        try:
            self.query_one("#chat-search-input").focus()
        except (QueryError, AttributeError):
            pass

    def hide(self) -> None:
        self.display = False
        try:
            self.query_one("#chat-search-input", Input).value = ""
        except (QueryError, AttributeError):
            pass
        self.post_message(self.SearchChanged(""))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "chat-search-input":
            self.post_message(self.SearchChanged(event.value))

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape" and self.display:
            self.hide()
            self.post_message(self.SearchClosed())
            event.stop()
