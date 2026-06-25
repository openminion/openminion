from __future__ import annotations

import time

import pytest
from textual.app import App
from textual.containers import Vertical
from textual.css.query import QueryError

from openminion.cli.tui.focus.widgets import (
    FocusMessageWidget,
    FocusTranscript,
    TurnHandle,
)
from openminion.cli.tui.presentation.models import ChatMessage, MessageKind, ToolEvent


class _Harness(App):
    def __init__(self) -> None:
        super().__init__()
        self.transcript: FocusTranscript | None = None

    def compose(self):
        self.transcript = FocusTranscript()
        yield Vertical(self.transcript)


def test_constructor_accepts_no_args() -> None:
    t = FocusTranscript()
    assert t.id == "focus-transcript"


@pytest.mark.asyncio
async def test_push_message_appends_and_returns_widget() -> None:
    async with _Harness().run_test() as pilot:
        t = pilot.app.transcript
        msg = ChatMessage(kind=MessageKind.USER, sender="you", body="hello")
        widget = t.push_message(msg)
        await pilot.pause()
        assert isinstance(widget, FocusMessageWidget)
        assert widget in list(t.query(FocusMessageWidget))


@pytest.mark.asyncio
async def test_set_messages_bulk_replaces() -> None:
    async with _Harness().run_test() as pilot:
        t = pilot.app.transcript
        m1 = ChatMessage(kind=MessageKind.USER, sender="you", body="first")
        t.push_message(m1)
        await pilot.pause()
        t.set_messages(
            [
                ChatMessage(kind=MessageKind.USER, sender="you", body="three"),
                ChatMessage(kind=MessageKind.AGENT, sender="agent", body="four"),
            ]
        )
        await pilot.pause()
        widgets = list(t.query(FocusMessageWidget))
        assert len(widgets) == 2
        bodies = [w._message.body for w in widgets]
        assert bodies == ["three", "four"]


@pytest.mark.asyncio
async def test_clear_messages_removes_all() -> None:
    async with _Harness().run_test() as pilot:
        t = pilot.app.transcript
        t.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body="x"))
        await pilot.pause()
        t.clear_messages()
        await pilot.pause()
        assert list(t.query(FocusMessageWidget)) == []


@pytest.mark.asyncio
async def test_filter_messages_hides_non_matching() -> None:
    async with _Harness().run_test() as pilot:
        t = pilot.app.transcript
        t.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body="alpha"))
        t.push_message(ChatMessage(kind=MessageKind.AGENT, sender="agent", body="beta"))
        await pilot.pause()
        t.filter_messages("alpha")
        await pilot.pause()
        widgets = list(t.query(FocusMessageWidget))
        assert widgets[0].display is True
        assert widgets[1].display is False
        t.filter_messages("")
        await pilot.pause()
        assert all(w.display for w in t.query(FocusMessageWidget))


@pytest.mark.asyncio
async def test_copy_selected_message_returns_body() -> None:
    async with _Harness().run_test() as pilot:
        t = pilot.app.transcript
        t.push_message(
            ChatMessage(kind=MessageKind.USER, sender="you", body="copyable")
        )
        await pilot.pause()
        assert t.copy_selected_message() == "copyable"


@pytest.mark.asyncio
async def test_copy_last_copyable_message_falls_back() -> None:
    async with _Harness().run_test() as pilot:
        t = pilot.app.transcript
        t.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body="first"))
        t.push_message(ChatMessage(kind=MessageKind.AGENT, sender="agent", body="last"))
        await pilot.pause()
        assert t.copy_last_copyable_message() == "last"


@pytest.mark.asyncio
async def test_copy_returns_tool_full_content_for_tool_messages() -> None:
    async with _Harness().run_test() as pilot:
        t = pilot.app.transcript
        event = ToolEvent(
            tool_name="bash",
            args={"cmd": "ls"},
            content="short",
            full_content="full output",
        )
        t.push_message(
            ChatMessage(
                kind=MessageKind.TOOL,
                sender="bash",
                body="",
                tool_event=event,
            )
        )
        await pilot.pause()
        assert t.copy_last_copyable_message() == "full output"


@pytest.mark.asyncio
async def test_begin_turn_returns_handle() -> None:
    async with _Harness().run_test() as pilot:
        t = pilot.app.transcript
        handle = t.begin_turn(role="assistant")
        await pilot.pause()
        assert isinstance(handle, TurnHandle)
        widgets = list(t.query(FocusMessageWidget))
        assert len(widgets) == 1
        assert widgets[0]._message.kind is MessageKind.AGENT


@pytest.mark.asyncio
async def test_append_token_builds_incrementally() -> None:
    async with _Harness().run_test() as pilot:
        t = pilot.app.transcript
        handle = t.begin_turn(role="assistant")
        await pilot.pause()
        handle.append_token("Hello")
        handle.append_token(", ")
        handle.append_token("world!")
        await pilot.pause()
        widgets = list(t.query(FocusMessageWidget))
        assert widgets[0]._message.body == "Hello, world!"


@pytest.mark.asyncio
async def test_complete_finalizes_with_explicit_text() -> None:
    async with _Harness().run_test() as pilot:
        t = pilot.app.transcript
        handle = t.begin_turn(role="assistant")
        await pilot.pause()
        handle.append_token("partial")
        handle.complete(final_text="full final body")
        await pilot.pause()
        widgets = list(t.query(FocusMessageWidget))
        assert widgets[0]._message.body == "full final body"
        assert widgets[0]._streaming is None


@pytest.mark.asyncio
async def test_complete_within_50ms_suppresses_blink() -> None:
    async with _Harness().run_test() as pilot:
        t = pilot.app.transcript
        handle = t.begin_turn(role="assistant")
        await pilot.pause()
        # Fire complete immediately — well under 50 ms.
        handle.append_token("instant")
        handle.complete()
        await pilot.pause()
        widgets = list(t.query(FocusMessageWidget))
        # Streaming state is cleared; no active timer.
        assert widgets[0]._streaming is None


@pytest.mark.asyncio
async def test_complete_after_50ms_does_not_suppress_blink() -> None:
    async with _Harness().run_test() as pilot:
        t = pilot.app.transcript
        handle = t.begin_turn(role="assistant")
        await pilot.pause()
        time.sleep(0.06)  # > 50ms threshold
        handle.append_token("slow stream")
        handle.complete()
        await pilot.pause()
        widgets = list(t.query(FocusMessageWidget))
        assert widgets[0]._message.body == "slow stream"
        assert widgets[0]._streaming is None


def test_focus_message_widget_refresh_body_ignores_missing_body_widget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget = FocusMessageWidget(
        ChatMessage(kind=MessageKind.AGENT, sender="agent", body="hello")
    )

    def _raise_query_error(*args, **kwargs):
        raise QueryError("missing body")

    monkeypatch.setattr(widget, "query_one", _raise_query_error)
    widget._refresh_body()


def test_focus_message_widget_sanitizes_textual_widget_id() -> None:
    widget = FocusMessageWidget(
        ChatMessage(
            kind=MessageKind.AGENT,
            sender="agent",
            body="hello",
            msg_id="'692dc2caf55741a4b16357d0e119ec0c'",
        )
    )

    assert widget.id == "msg-692dc2caf55741a4b16357d0e119ec0c"


def test_transcript_module_does_not_import_forbidden_symbols() -> None:
    import openminion.cli.tui.focus.widgets.transcript as transcript_mod

    forbidden = {"ChatView", "ChatInputBar", "MessageWidget"}
    module_names = set(vars(transcript_mod).keys())
    leaked = forbidden & module_names
    assert not leaked, (
        f"forbidden symbols leaked into focus/widgets/transcript.py "
        f"namespace: {leaked}. The §4 anti-shared-widget boundary is "
        f"violated."
    )
