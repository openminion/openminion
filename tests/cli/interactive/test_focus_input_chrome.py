from __future__ import annotations

import tempfile
from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult
from textual.css.query import QueryError
from textual.widgets import Input, TextArea

from openminion.cli.presentation import styles
from openminion.cli.theme import DARK
from openminion.cli.interactive.app import FocusApp, _DemoFocusRuntime
from openminion.cli.interactive.widgets.status_line import FocusStatusLine
from openminion.cli.interactive.widgets import FocusComposer
from openminion.cli.tui.widgets.input_bar import ChatInput, ChatInputBar


@pytest.fixture(autouse=True)
def _restore_active_theme():
    original_codes = dict(styles._ANSI_CODES)
    original_name = styles.get_active_theme_name()
    styles.set_active_theme(DARK)
    yield
    styles._ANSI_CODES.clear()
    styles._ANSI_CODES.update(original_codes)
    styles._ACTIVE_THEME_NAME = original_name


# ── Dashboard regression guard ───────────────────────────────────────────────


def test_dashboard_input_bar_preserves_fcc_placeholder() -> None:
    bar = ChatInputBar()
    assert bar._focus_mode is False
    assert bar._placeholder == ChatInputBar.DEFAULT_PLACEHOLDER


class _InputBarHarness(App[None]):
    def __init__(self, bar: ChatInputBar) -> None:
        super().__init__()
        self.bar = bar

    def compose(self) -> ComposeResult:
        yield self.bar


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


@pytest.mark.asyncio
async def test_dashboard_input_bar_preserves_space_key() -> None:
    bar = ChatInputBar()
    app = _InputBarHarness(bar)
    async with app.run_test() as pilot:
        await pilot.pause()
        single = bar.query_one("#message-input", Input)
        single.focus()
        await pilot.press("w", "h", "a", "t", "space", "n", "o", "w")
        await pilot.pause()
        assert single.value == "what now"


@pytest.mark.asyncio
async def test_dashboard_input_bar_inserts_bare_terminal_space_key() -> None:
    bar = ChatInputBar()
    app = _InputBarHarness(bar)
    async with app.run_test() as pilot:
        await pilot.pause()
        single = bar.query_one("#message-input", ChatInput)
        single.value = "what"
        single.cursor_position = len(single.value)

        event = _BareSpaceKeyEvent()
        await single._on_key(event)

        assert single.value == "what "
        assert single.cursor_position == len("what ")
        assert event.stopped is True
        assert event.prevented is True


@pytest.mark.asyncio
async def test_dashboard_input_bar_parent_inserts_space_when_input_focused() -> None:
    bar = ChatInputBar()
    app = _InputBarHarness(bar)
    async with app.run_test() as pilot:
        await pilot.pause()
        single = bar.query_one("#message-input", Input)
        single.value = "what"
        single.cursor_position = len(single.value)
        single.focus()

        event = _BareSpaceKeyEvent()
        bar.on_key(event)

        assert single.value == "what "
        assert single.cursor_position == len("what ")
        assert event.stopped is True
        assert event.prevented is True


@pytest.mark.asyncio
async def test_dashboard_input_bar_paste_auto_toggles_multiline() -> None:
    bar = ChatInputBar()
    app = _InputBarHarness(bar)
    async with app.run_test() as pilot:
        await pilot.pause()
        event = SimpleNamespace(text="line one\nline two", stop=lambda: None)
        bar.on_paste(event)
        await pilot.pause()
        assert bar._multiline is True
        editor = bar.query_one("#message-editor", TextArea)
        assert editor.text == "line one\nline two"


@pytest.mark.asyncio
async def test_dashboard_input_bar_paste_normalizes_carriage_returns() -> None:
    bar = ChatInputBar()
    app = _InputBarHarness(bar)
    async with app.run_test() as pilot:
        await pilot.pause()
        event = SimpleNamespace(
            text="line one\r\nline two\rline three", stop=lambda: None
        )
        bar.on_paste(event)
        await pilot.pause()
        assert bar._multiline is True
        editor = bar.query_one("#message-editor", TextArea)
        assert editor.text == "line one\nline two\nline three"


@pytest.mark.asyncio
async def test_dashboard_input_bar_shift_enter_toggles_multiline() -> None:
    bar = ChatInputBar()
    app = _InputBarHarness(bar)
    async with app.run_test() as pilot:
        await pilot.pause()
        single = bar.query_one("#message-input", Input)
        single.value = "first line"
        single.cursor_position = len(single.value)
        event = SimpleNamespace(key="shift+enter", stop=lambda: None)
        bar.on_key(event)
        await pilot.pause()
        assert bar._multiline is True
        editor = bar.query_one("#message-editor", TextArea)
        assert editor.text == "first line\n"


@pytest.mark.asyncio
async def test_dashboard_input_bar_shift_enter_tolerates_bad_cursor_value() -> None:
    bar = ChatInputBar()
    app = _InputBarHarness(bar)
    async with app.run_test() as pilot:
        await pilot.pause()
        single = bar.query_one("#message-input", Input)
        single.value = "first line"
        original_cursor_position = Input.cursor_position
        Input.cursor_position = property(lambda self: "not-a-number")
        try:
            event = SimpleNamespace(key="shift+enter")
            bar.on_key(event)
        finally:
            Input.cursor_position = original_cursor_position
        await pilot.pause()
        assert bar._multiline is True
        editor = bar.query_one("#message-editor", TextArea)
        assert editor.text == "first line\n"


# ── focus_mode=True placeholder split ────────────────────────────────────────


def test_focus_mode_fresh_placeholder() -> None:
    bar = ChatInputBar(focus_mode=True, is_resumed=False)
    assert bar._placeholder == ChatInputBar.FOCUS_PLACEHOLDER_FRESH
    assert "Ask anything" in bar._placeholder


def test_focus_mode_resumed_placeholder() -> None:
    bar = ChatInputBar(focus_mode=True, is_resumed=True)
    assert bar._placeholder == ChatInputBar.FOCUS_PLACEHOLDER_RESUMED
    assert "Reply" in bar._placeholder
    assert "↑ for history" in bar._placeholder


def test_focus_mode_explicit_placeholder_override_wins() -> None:
    bar = ChatInputBar("custom prompt", focus_mode=True, is_resumed=False)
    assert bar._placeholder == "custom prompt"


def test_set_resumed_updates_focus_placeholder() -> None:
    bar = ChatInputBar(focus_mode=True, is_resumed=False)
    assert bar._placeholder == ChatInputBar.FOCUS_PLACEHOLDER_FRESH
    bar.set_resumed(True)
    assert bar._placeholder == ChatInputBar.FOCUS_PLACEHOLDER_RESUMED


def test_set_resumed_no_op_when_focus_mode_off() -> None:
    bar = ChatInputBar()
    bar.set_resumed(True)
    assert bar._placeholder == ChatInputBar.DEFAULT_PLACEHOLDER


def test_set_resumed_ignores_missing_widgets(monkeypatch: pytest.MonkeyPatch) -> None:
    bar = ChatInputBar(focus_mode=True, is_resumed=False)

    def _raise_query_error(*args, **kwargs):
        raise QueryError("missing widget")

    monkeypatch.setattr(bar, "query_one", _raise_query_error)
    bar.set_resumed(True)
    assert bar._placeholder == ChatInputBar.FOCUS_PLACEHOLDER_RESUMED


# ── focus_mode=True smart-paste ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_focus_mode_paste_multiline_auto_toggles() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="paste-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = app.screen.query_one(FocusComposer)
            assert composer._multiline is False
            event = SimpleNamespace(
                text="def foo():\n    return 42",
                stop=lambda: None,
            )
            composer.on_paste(event)
            await pilot.pause()
            assert composer._multiline is True
            editor = composer.query_one("#focus-editor", TextArea)
            assert "def foo()" in editor.text
            assert "return 42" in editor.text


@pytest.mark.asyncio
async def test_focus_mode_paste_normalizes_carriage_returns() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="cr-paste-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = app.screen.query_one(FocusComposer)
            event = SimpleNamespace(
                text="alpha\r\nbeta\rgamma",
                stop=lambda: None,
            )
            composer.on_paste(event)
            await pilot.pause()
            assert composer._multiline is True
            editor = composer.query_one("#focus-editor", TextArea)
            assert editor.text == "alpha\nbeta\ngamma"


@pytest.mark.asyncio
async def test_focus_mode_paste_single_line_stays_single_line() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="single-paste-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = app.screen.query_one(FocusComposer)
            event = SimpleNamespace(text="single line", stop=lambda: None)
            composer.on_paste(event)
            await pilot.pause()
            assert composer._multiline is False


# ── Shift+Enter newline (ported from FIU-05) ─────────────────────────────────


@pytest.mark.asyncio
async def test_focus_mode_shift_enter_inserts_newline_and_toggles() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="shift-enter-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = app.screen.query_one(FocusComposer)
            single = composer.query_one("#focus-input", Input)
            single.value = "first line"
            single.cursor_position = len(single.value)
            event = SimpleNamespace(key="shift+enter", stop=lambda: None)
            composer.on_key(event)
            await pilot.pause()
            assert composer._multiline is True
            editor = composer.query_one("#focus-editor", TextArea)
            assert "first line" in editor.text
            assert "\n" in editor.text


# ── Adaptive bottom hint ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_typing_shifts_bottom_hint_to_typing_variant() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="hint-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            status_line = app.screen.query_one(FocusStatusLine)
            assert status_line.input_state == "empty"
            single = app.screen.query_one("#focus-input", Input)
            single.value = "hello"
            await pilot.pause()
            assert status_line.input_state == "typing"
            text = status_line._text()
            assert "Enter to send" in text
            single.value = ""
            await pilot.pause()
            assert status_line.input_state == "empty"


def test_status_line_unknown_input_state_falls_back_to_empty() -> None:
    line = FocusStatusLine()
    line.set_state(input_state="weird-mode")
    assert line.input_state == "empty"


def test_status_line_busy_state_overrides_input_state_hint() -> None:
    line = FocusStatusLine()
    line.set_state(state="responding", elapsed_seconds=5.0, input_state="typing")
    text = line._text()
    assert "responding" in text
    assert "Enter to send" not in text


def test_status_line_busy_state_keeps_runtime_stats() -> None:
    line = FocusStatusLine()
    line.set_state(
        state="responding",
        elapsed_seconds=5.0,
        model="openai/MiniMax-M2.7",
        tokens="1200/8000",
        custom="analyzing request",
        queued_count=1,
        input_state="typing",
    )
    text = line._text()
    assert "responding" in text
    assert "model: openai/MiniMax-M2.7" in text
    assert "tokens: 1200/8000" in text
    assert "status: analyzing request" in text
    assert "queued: 1" in text
    assert "Enter to send" not in text
