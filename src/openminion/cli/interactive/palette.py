from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Input, OptionList


class CommandPaletteScreen(Screen[str | None]):
    BINDINGS = [
        ("escape", "dismiss_palette", "Close"),
        ("enter", "select_entry", "Select"),
        ("up", "move_up", "Up"),
        ("down", "move_down", "Down"),
    ]

    def __init__(self, entries: list[tuple[str, str, str]]) -> None:
        super().__init__()
        self._entries = entries
        self._filtered = list(entries)

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-overlay"), Vertical(id="palette-dialog"):
            yield Input(placeholder="Type to search...", id="palette-input")
            yield OptionList(
                *[f"{label}  -  {description}" for label, description, _ in self._filtered],
                id="palette-list",
            )

    def on_mount(self) -> None:
        self.query_one("#palette-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "palette-input":
            return
        query = event.value.strip().lower()
        self._filtered = (
            [
                entry
                for entry in self._entries
                if query in entry[0].lower() or query in entry[1].lower()
            ]
            if query
            else list(self._entries)
        )
        options = self.query_one("#palette-list", OptionList)
        options.clear_options()
        for label, description, _ in self._filtered:
            options.add_option(f"{label}  -  {description}")
        if self._filtered:
            options.highlighted = 0

    def on_option_list_option_selected(self, _event: OptionList.OptionSelected) -> None:
        self.action_select_entry()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "palette-input":
            self.action_select_entry()

    def action_select_entry(self) -> None:
        options = self.query_one("#palette-list", OptionList)
        index = options.highlighted
        if index is not None and 0 <= index < len(self._filtered):
            self.dismiss(self._filtered[index][2])
            return
        self.dismiss(None)

    def action_dismiss_palette(self) -> None:
        self.dismiss(None)

    def action_move_up(self) -> None:
        options = self.query_one("#palette-list", OptionList)
        if options.highlighted is not None and options.highlighted > 0:
            options.highlighted -= 1

    def action_move_down(self) -> None:
        options = self.query_one("#palette-list", OptionList)
        if options.highlighted is not None and options.highlighted < options.option_count - 1:
            options.highlighted += 1
