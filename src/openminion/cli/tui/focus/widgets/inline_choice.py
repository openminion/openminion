from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label


class _InlineChoiceWidget(Widget):
    class Selected(Message):
        def __init__(self, choice: str) -> None:
            super().__init__()
            self.choice = choice

    BINDINGS = [
        ("y", "choose_yes", "Yes"),
        ("enter", "choose_yes", "Yes"),
        ("n", "choose_no", "No"),
        ("escape", "choose_no", "No"),
    ]

    can_focus = True

    def __init__(self, prompt: str) -> None:
        super().__init__(classes="focus-inline-prompt")
        self._prompt = str(prompt or "").strip()

    def compose(self) -> ComposeResult:
        yield Label(self._prompt, classes="focus-inline-prompt-title")
        yield Label("[Y] Yes  [N] No", classes="focus-inline-prompt-hint")

    def action_choose_yes(self) -> None:
        self.post_message(self.Selected("yes"))

    def action_choose_no(self) -> None:
        self.post_message(self.Selected("no"))
