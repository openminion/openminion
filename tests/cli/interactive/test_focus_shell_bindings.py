from __future__ import annotations

import tempfile

import pytest
from textual.css.query import QueryError

from openminion.cli.status import TokenUsageSnapshot
from openminion.cli.interactive.app import FocusApp, _DemoFocusRuntime
from openminion.cli.interactive.status import FocusRuntimeStateMixin
from openminion.cli.interactive.screen import FocusDebugPane, FocusScreen
from openminion.cli.interactive.palette import CommandPaletteScreen
from openminion.cli.interactive.search import ChatSearchBar
from openminion.cli.interactive.widgets import FocusTranscript
from openminion.cli.interactive.widgets.composer import _FocusComposerInput as ChatInput


def _make_app(tmp: str) -> FocusApp:
    runtime = _DemoFocusRuntime(working_dir=tmp)
    return FocusApp(runtime=runtime, working_dir=tmp)


class _BrokenRuntimeState(FocusRuntimeStateMixin):
    def __init__(self) -> None:
        self._runtime = _DemoFocusRuntime(working_dir="/tmp")
        self._working_dir = "/tmp"

    def query_one(self, *args, **kwargs):
        raise QueryError("missing status line")


class _UsageFocusRuntime(_DemoFocusRuntime):
    def token_usage_snapshot(self) -> TokenUsageSnapshot:
        return TokenUsageSnapshot(
            turn_total_tokens=300,
            session_total_tokens=4800,
            context_used_tokens=4800,
            context_limit_tokens=200000,
            turn_elapsed_seconds=82.0,
            updated_at_monotonic=100.0,
        )


@pytest.mark.asyncio
async def test_chat_input_does_not_bind_reserved_shell_keys() -> None:
    reserved = {"ctrl+d", "ctrl+k", "ctrl+f", "ctrl+a"}
    leaked = []
    for binding in ChatInput.BINDINGS:
        for key in binding.key.split(","):
            if key.strip() in reserved:
                leaked.append((key.strip(), binding.action))
    assert not leaked, (
        f"ChatInput BINDINGS must not keep shell-reserved keys; leaked={leaked}"
    )


@pytest.mark.asyncio
async def test_ctrl_d_toggles_debug_pane_while_input_focused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            pane = app.screen.query_one(FocusDebugPane)
            assert isinstance(app.focused, ChatInput), (
                "baseline: chat input should auto-focus"
            )
            assert pane.has_class("--hidden"), "debug pane starts hidden"
            await pilot.press("ctrl+d")
            await pilot.pause()
            assert not pane.has_class("--hidden"), (
                "ctrl+d must toggle debug pane even with input focused"
            )


def test_focus_debug_pane_ignores_missing_content_widget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pane = FocusDebugPane()

    def _raise_query_error(*args, **kwargs):
        raise QueryError("missing debug content")

    monkeypatch.setattr(pane, "query_one", _raise_query_error)
    pane.set_payload({"hello": "world"})


def test_focus_runtime_state_refresh_header_ignores_missing_status_line() -> None:
    state = _BrokenRuntimeState()
    state._refresh_header()


@pytest.mark.asyncio
async def test_ctrl_k_clears_chat_while_input_focused() -> None:
    from openminion.cli.presentation.models import ChatMessage, MessageKind

    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            chat = app.screen.query_one(FocusTranscript)
            chat.push_message(
                ChatMessage(kind=MessageKind.USER, sender="you", body="one")
            )
            chat.push_message(
                ChatMessage(kind=MessageKind.AGENT, sender="bot", body="two")
            )
            await pilot.pause()
            assert len(chat._messages) >= 2
            await pilot.press("ctrl+k")
            await pilot.pause()
            assert len(chat._messages) == 0, (
                "ctrl+k must clear chat while chat input is focused"
            )


@pytest.mark.asyncio
async def test_ctrl_f_toggles_search_while_input_focused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            search = app.screen.query_one(ChatSearchBar)
            assert not search.display, "search starts hidden"
            await pilot.press("ctrl+f")
            await pilot.pause()
            assert search.display, "ctrl+f must open search while chat input is focused"
            await pilot.press("escape")
            await pilot.pause()
            assert not search.display, "escape must close search again"


@pytest.mark.asyncio
async def test_focus_status_line_reflects_shell_state() -> None:
    from openminion.cli.interactive.widgets.status_line import FocusStatusLine

    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            status_line = app.screen.query_one(FocusStatusLine)

            # idle state shows the hints strip
            idle_text = status_line._text()
            assert "palette" in idle_text.lower(), idle_text

            # responding state shows elapsed marker
            status_line.set_state(state="responding", elapsed_seconds=1.5)
            await pilot.pause()
            responding_text = status_line._text()
            assert "responding" in responding_text.lower(), responding_text
            assert "1s" in responding_text, responding_text

            # tool state shows tool name
            status_line.set_state(
                state="tool", elapsed_seconds=0.3, tool_name="fs.find"
            )
            await pilot.pause()
            tool_text = status_line._text()
            assert "fs.find" in tool_text, tool_text
            assert "0s" in tool_text, tool_text


@pytest.mark.asyncio
async def test_focus_status_line_renders_token_usage_summary() -> None:
    from openminion.cli.interactive.widgets.status_line import FocusStatusLine

    with tempfile.TemporaryDirectory() as tmp:
        app = FocusApp(runtime=_UsageFocusRuntime(working_dir=tmp), working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            screen._push_status_line(state="idle")
            await pilot.pause()

            status_line = app.screen.query_one(FocusStatusLine)
            text = status_line._text()
            assert "turn 300" in text, text
            assert "session 4.8k" in text, text
            assert "ctx 4.8k / 200k (2%)" in text, text
            assert "total 1m 22s" in text, text


@pytest.mark.asyncio
async def test_focus_clear_keeps_session_token_summary() -> None:
    from openminion.cli.interactive.widgets.status_line import FocusStatusLine

    with tempfile.TemporaryDirectory() as tmp:
        app = FocusApp(runtime=_UsageFocusRuntime(working_dir=tmp), working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            screen.action_clear_screen()
            screen._push_status_line(state="idle")
            await pilot.pause()

            status_line = app.screen.query_one(FocusStatusLine)
            text = status_line._text()
            assert "session 4.8k" in text, text


@pytest.mark.asyncio
async def test_ctrl_p_opens_custom_command_palette_not_textuals_builtin() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = _make_app(tmp)
        assert app.ENABLE_COMMAND_PALETTE is False, (
            "Textual's built-in command palette must be disabled so "
            "our custom palette handles ctrl+p"
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, FocusScreen)
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert isinstance(app.screen, CommandPaletteScreen), (
                f"ctrl+p should open our CommandPaletteScreen; "
                f"got {type(app.screen).__name__}"
            )
