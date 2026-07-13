from __future__ import annotations

import pytest
from textual.app import App
from textual.containers import Vertical
from textual.widgets import Input, TextArea

from openminion.cli.interactive.widgets import FocusComposer


class _Harness(App):
    def __init__(self) -> None:
        super().__init__()
        self.composer: FocusComposer | None = None
        self.received: list[str] = []

    def compose(self):
        self.composer = FocusComposer()
        yield Vertical(self.composer)

    def on_focus_composer_submitted(self, event: FocusComposer.Submitted) -> None:
        self.received.append(event.text)


@pytest.mark.asyncio
async def test_backslash_at_end_toggles_multiline_no_submit() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        inp = c.query_one("#focus-input", Input)
        inp.value = "first line\\"
        await pilot.press("enter")
        await pilot.pause()
        assert c._multiline is True
        editor = c.query_one("#focus-editor", TextArea)
        # Trailing backslash stripped; newline appended.
        assert "first line" in editor.text
        assert editor.text.endswith("\n")
        # No Submitted event fired.
        assert pilot.app.received == []


@pytest.mark.asyncio
async def test_backslash_with_trailing_whitespace_still_continues() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        inp = c.query_one("#focus-input", Input)
        inp.value = "first \\   "
        await pilot.press("enter")
        await pilot.pause()
        assert c._multiline is True
        assert pilot.app.received == []


@pytest.mark.asyncio
async def test_no_backslash_submits_normally() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        inp = c.query_one("#focus-input", Input)
        inp.value = "no continuation"
        await pilot.press("enter")
        await pilot.pause()
        assert pilot.app.received == ["no continuation"]
        assert c._multiline is False
