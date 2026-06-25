from __future__ import annotations

import pytest
from textual.app import App
from textual.containers import Vertical
from textual.widgets import Input, TextArea

from openminion.cli.tui.focus.widgets import FocusComposer


class _Harness(App):
    composer_kwargs: dict = {}

    def __init__(self, **composer_kwargs) -> None:
        super().__init__()
        self.composer_kwargs = composer_kwargs
        self.composer: FocusComposer | None = None
        self.received: list[str] = []

    def compose(self):
        self.composer = FocusComposer(**self.composer_kwargs)
        yield Vertical(self.composer)

    def on_focus_composer_submitted(self, event: FocusComposer.Submitted) -> None:
        self.received.append(event.text)


class _BareSpaceKeyEvent:
    key = "space"
    character = None
    is_printable = False

    def __init__(self) -> None:
        self.stopped = False
        self.prevented = False

    def stop(self) -> None:
        self.stopped = True

    def prevent_default(self) -> None:
        self.prevented = True


def test_constructor_signature_matches_chatinputbar_focus_form() -> None:
    c = FocusComposer()
    assert c._placeholder == FocusComposer.PLACEHOLDER_FRESH

    c2 = FocusComposer(is_resumed=True)
    assert c2._placeholder == FocusComposer.PLACEHOLDER_RESUMED

    c3 = FocusComposer(placeholder="custom")
    assert c3._placeholder == "custom"


@pytest.mark.asyncio
async def test_set_resumed_swaps_placeholder() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        assert c._placeholder == FocusComposer.PLACEHOLDER_FRESH
        c.set_resumed(True)
        await pilot.pause()
        assert c._placeholder == FocusComposer.PLACEHOLDER_RESUMED
        assert (
            c.query_one("#focus-input", Input).placeholder
            == FocusComposer.PLACEHOLDER_RESUMED
        )


@pytest.mark.asyncio
async def test_set_disabled_gates_input() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        c.set_disabled(True)
        await pilot.pause()
        assert c._disabled is True
        assert c.query_one("#focus-input", Input).disabled is True
        c.set_disabled(False)
        await pilot.pause()
        assert c._disabled is False
        assert c.query_one("#focus-input", Input).disabled is False


@pytest.mark.asyncio
async def test_focus_input_focuses_visible_field() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        c.focus_input()
        await pilot.pause()
        assert pilot.app.focused is c.query_one("#focus-input", Input)


@pytest.mark.asyncio
async def test_toggle_multiline_swaps_input_for_textarea() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        inp = c.query_one("#focus-input", Input)
        inp.value = "draft text"
        c.toggle_multiline()
        await pilot.pause()
        assert c._multiline is True
        ed = c.query_one("#focus-editor", TextArea)
        assert ed.text == "draft text"
        assert "--hidden" in inp.classes
        assert "--hidden" not in ed.classes
        c.toggle_multiline()
        await pilot.pause()
        assert c._multiline is False
        assert c.query_one("#focus-input", Input).value == "draft text"


@pytest.mark.asyncio
async def test_submitted_event_class_renamed_from_chat_input_bar() -> None:
    assert FocusComposer.Submitted.__qualname__ == "FocusComposer.Submitted"
    msg = FocusComposer.Submitted("hello")
    assert msg.text == "hello"


@pytest.mark.asyncio
async def test_single_line_input_preserves_space_key() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        inp = c.query_one("#focus-input", Input)
        await pilot.press("w", "h", "a", "t", "space", "n", "o", "w")
        await pilot.pause()
        assert inp.value == "what now"


@pytest.mark.asyncio
async def test_single_line_input_inserts_bare_terminal_space_key() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        inp = c.query_one("#focus-input", Input)
        inp.value = "what"
        inp.cursor_position = len(inp.value)

        event = _BareSpaceKeyEvent()
        await inp._on_key(event)

        assert inp.value == "what "
        assert inp.cursor_position == len("what ")
        assert event.stopped is True
        assert event.prevented is True


@pytest.mark.asyncio
async def test_parent_composer_inserts_space_when_input_focused() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        inp = c.query_one("#focus-input", Input)
        inp.value = "what"
        inp.cursor_position = len(inp.value)
        inp.focus()

        event = _BareSpaceKeyEvent()
        c.on_key(event)

        assert inp.value == "what "
        assert inp.cursor_position == len("what ")
        assert event.stopped is True
        assert event.prevented is True


@pytest.mark.asyncio
async def test_submit_emits_focus_composer_submitted() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        inp = c.query_one("#focus-input", Input)
        inp.value = "hello world"
        await pilot.press("enter")
        await pilot.pause()
        assert pilot.app.received == ["hello world"]
        assert inp.value == ""


@pytest.mark.asyncio
async def test_submit_blocks_when_disabled() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        c.set_disabled(True)
        await pilot.pause()
        c._dispatch("ignored")
        await pilot.pause()
        assert pilot.app.received == []


@pytest.mark.asyncio
async def test_submit_skips_empty_text() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        c._dispatch("   ")
        c._dispatch("")
        await pilot.pause()
        assert pilot.app.received == []


@pytest.mark.asyncio
async def test_submit_pushes_history() -> None:
    async with _Harness().run_test() as pilot:
        c = pilot.app.composer
        c._dispatch("first")
        c._dispatch("second")
        c._dispatch("second")
        await pilot.pause()
        assert c._history == ["first", "second"]


def test_composer_module_does_not_import_forbidden_symbols() -> None:
    import openminion.cli.tui.focus.widgets.composer as composer_mod

    forbidden = {"ChatView", "ChatInputBar", "MessageWidget"}
    leaked = forbidden & set(vars(composer_mod).keys())
    assert not leaked, (
        f"forbidden symbols leaked into focus/widgets/composer.py "
        f"namespace: {leaked}. The §4 anti-shared-widget boundary is "
        f"violated."
    )
