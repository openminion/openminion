from __future__ import annotations

import pytest
from textual.app import App
from textual.containers import Vertical
from textual.widgets import Input, TextArea

from openminion.cli.tui.focus.widgets import FocusComposer


class _Harness(App):
    def __init__(self) -> None:
        super().__init__()
        self.composer: FocusComposer | None = None

    def compose(self):
        self.composer = FocusComposer()
        yield Vertical(self.composer)


@pytest.mark.asyncio
async def test_ctrl_j_in_single_line_toggles_multiline_and_inserts_newline() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        single = c.query_one("#focus-input", Input)
        single.value = "hello"
        single.cursor_position = len(single.value)
        c.focus_input()
        await pilot.pause()
        c.action_newline()
        await pilot.pause()
        assert c._multiline is True
        editor = c.query_one("#focus-editor", TextArea)
        assert "hello" in editor.text
        assert "\n" in editor.text


@pytest.mark.asyncio
async def test_ctrl_j_in_multiline_inserts_newline() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        c.toggle_multiline()
        await pilot.pause()
        editor = c.query_one("#focus-editor", TextArea)
        editor.text = "line one"
        await pilot.pause()
        c.action_newline()
        await pilot.pause()
        # Editor text should have grown by a newline (either inserted
        # at cursor or appended via fallback).
        assert "\n" in c.query_one("#focus-editor", TextArea).text


@pytest.mark.asyncio
async def test_shift_enter_still_works_after_ctrl_j_added() -> None:
    from types import SimpleNamespace

    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        single = c.query_one("#focus-input", Input)
        single.value = "fiu05"
        single.cursor_position = len(single.value)
        # Dispatch a synthetic Shift+Enter event through `on_key`.
        c.on_key(SimpleNamespace(key="shift+enter", stop=lambda: None))
        await pilot.pause()
        assert c._multiline is True
        editor = c.query_one("#focus-editor", TextArea)
        assert "fiu05" in editor.text
        assert "\n" in editor.text


def test_ctrl_j_binding_registered_on_composer() -> None:
    # BINDINGS entries can be tuples or Binding objects depending
    # on how they were declared; normalize to a string key.
    keys: list[str] = []
    for b in FocusComposer.BINDINGS:
        keys.append(b[0] if isinstance(b, tuple) else getattr(b, "key", ""))
    assert "ctrl+j" in keys
