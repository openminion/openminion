from __future__ import annotations

import inspect

import pytest
from textual.app import App, ComposeResult

from openminion.cli.tui.presentation.models import (
    ChatMessage,
    MessageKind,
    ToolEvent,
)
from openminion.cli.tui.widgets.chat import (
    ChatView,
    MessageWidget,
    copyable_text_for_message,
)


def _user(body: str, *, msg_id: str = "") -> ChatMessage:
    return ChatMessage(kind=MessageKind.USER, sender="you", body=body, msg_id=msg_id)


def _agent(body: str, *, msg_id: str = "") -> ChatMessage:
    return ChatMessage(
        kind=MessageKind.AGENT, sender="alibaba", body=body, msg_id=msg_id
    )


def _tool(
    *,
    body: str,
    tool_result: str | None = None,
    tool_event: ToolEvent | None = None,
    msg_id: str = "",
) -> ChatMessage:
    return ChatMessage(
        kind=MessageKind.TOOL,
        sender="tool:exec.run",
        body=body,
        tool_result=tool_result,
        tool_event=tool_event,
        msg_id=msg_id,
    )


class _ChatHarness(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.chat = ChatView()

    def compose(self) -> ComposeResult:
        yield self.chat


def test_copyable_text_for_user_returns_body() -> None:
    msg = _user("hello world")
    assert copyable_text_for_message(msg) == "hello world"


def test_copyable_text_for_agent_returns_body() -> None:
    msg = _agent("the answer is 42")
    assert copyable_text_for_message(msg) == "the answer is 42"


def test_copyable_text_for_tool_prefers_result_over_body() -> None:
    msg = _tool(body="exec.run: ls", tool_result="file1\nfile2\n")
    assert copyable_text_for_message(msg) == "file1\nfile2"


def test_copyable_text_for_tool_prefers_full_content_over_content() -> None:
    event = ToolEvent(
        tool_name="file.read",
        args={"path": "x.py"},
        content="short",
        full_content="full content here",
    )
    msg = _tool(body="file.read: x.py", tool_result=None, tool_event=event)
    assert copyable_text_for_message(msg) == "full content here"


def test_copyable_text_for_tool_falls_back_to_content_then_body() -> None:
    event = ToolEvent(
        tool_name="file.read",
        args={"path": "x.py"},
        content="body bytes",
        full_content="",
    )
    msg = _tool(body="file.read: x.py", tool_event=event)
    assert copyable_text_for_message(msg) == "body bytes"

    bare = _tool(body="file.read: x.py")
    assert copyable_text_for_message(bare) == "file.read: x.py"


def test_copyable_text_for_system_and_error_returns_body() -> None:
    sys_msg = ChatMessage(kind=MessageKind.SYSTEM, sender="system", body="New session")
    err_msg = ChatMessage(kind=MessageKind.ERROR, sender="error", body="boom")
    assert copyable_text_for_message(sys_msg) == "New session"
    assert copyable_text_for_message(err_msg) == "boom"


@pytest.mark.asyncio
async def test_chat_view_selection_apis_track_one_message() -> None:
    app = _ChatHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.chat.set_messages([_user("a"), _agent("b"), _user("c")])
        await pilot.pause()
        ids = [m.msg_id for m in app.chat._messages]

        assert app.chat.selected_message_id is None

        app.chat.select_next_message()
        assert app.chat.selected_message_id == ids[0]

        app.chat.select_next_message()
        assert app.chat.selected_message_id == ids[1]

        app.chat.select_previous_message()
        assert app.chat.selected_message_id == ids[0]

        app.chat.select_last_message()
        assert app.chat.selected_message_id == ids[-1]

        app.chat.select_next_message()
        assert app.chat.selected_message_id == ids[-1]

        app.chat.select_first_message()
        assert app.chat.selected_message_id == ids[0]

        app.chat.clear_selection()
        assert app.chat.selected_message_id is None


@pytest.mark.asyncio
async def test_chat_view_selection_toggles_widget_class() -> None:
    app = _ChatHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.chat.set_messages([_user("a"), _agent("b")])
        await pilot.pause()
        first_id = app.chat._messages[0].msg_id
        second_id = app.chat._messages[1].msg_id

        app.chat.select_message(first_id)
        widgets = {w._message.msg_id: w for w in app.chat.query(MessageWidget)}
        assert widgets[first_id].has_class("--selected")
        assert not widgets[second_id].has_class("--selected")

        app.chat.select_message(second_id)
        assert not widgets[first_id].has_class("--selected")
        assert widgets[second_id].has_class("--selected")


@pytest.mark.asyncio
async def test_copy_selected_message_returns_selected_only() -> None:
    app = _ChatHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.chat.set_messages(
            [
                _user("first"),
                _agent("second"),
                _user("third"),
            ]
        )
        await pilot.pause()
        assert app.chat.copy_selected_message() is None
        assert app.chat.copy_last_copyable_message() == "third"

        second_id = app.chat._messages[1].msg_id
        app.chat.select_message(second_id)
        assert app.chat.copy_selected_message() == "second"


@pytest.mark.asyncio
async def test_copy_last_copyable_prefers_tool_result_over_call_body() -> None:
    app = _ChatHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.chat.set_messages(
            [
                _user("do it"),
                _tool(body="exec.run: ls", tool_result="file1\nfile2\n"),
            ]
        )
        await pilot.pause()
        assert app.chat.copy_last_copyable_message() == "file1\nfile2"


@pytest.mark.asyncio
async def test_push_message_preserves_middle_selection() -> None:
    app = _ChatHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.chat.set_messages([_user("a"), _agent("b"), _user("c")])
        await pilot.pause()
        middle_id = app.chat._messages[1].msg_id
        app.chat.select_message(middle_id)

        app.chat.push_message(_agent("d streaming in"))
        await pilot.pause()
        assert app.chat.selected_message_id == middle_id


@pytest.mark.asyncio
async def test_push_message_follows_tail_when_previously_at_tail() -> None:
    app = _ChatHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.chat.set_messages([_user("a"), _agent("b")])
        await pilot.pause()
        tail_id = app.chat._messages[-1].msg_id
        app.chat.select_message(tail_id)

        app.chat.push_message(_agent("c"))
        await pilot.pause()
        new_tail_id = app.chat._messages[-1].msg_id
        assert app.chat.selected_message_id == new_tail_id
        assert new_tail_id != tail_id


def test_dashboard_ctrl_y_uses_shared_chat_view_copy_api() -> None:
    from openminion.cli.tui.tabs.chat import ChatTab

    src = inspect.getsource(ChatTab)
    assert "copy_selected_message" in src
    assert "copy_last_copyable_message" in src
    import re

    private_access = re.findall(r"\.\s*_messages\b", src)
    assert not private_access, (
        "Dashboard `ChatTab` must not read `ChatView._messages` directly "
        f"for copy — route through the shared public API. Found: {private_access}"
    )


def test_focus_ctrl_y_uses_shared_chat_view_copy_api() -> None:
    import re

    from openminion.cli.tui.focus.screen import FocusScreen

    src = inspect.getsource(FocusScreen)
    assert "copy_selected_message" in src
    assert "copy_last_copyable_message" in src
    private_access = re.findall(r"\.\s*_messages\b", src)
    assert not private_access, (
        "Focus `FocusScreen` must not read `ChatView._messages` directly; "
        f"route through the shared public API. Found: {private_access}"
    )


@pytest.mark.asyncio
async def test_up_down_keys_do_not_move_selection_when_input_bar_has_focus() -> None:
    from openminion.cli.tui.widgets.input_bar import ChatInputBar

    class _ChatWithInput(App[None]):
        def __init__(self) -> None:
            super().__init__()
            self.chat = ChatView()
            self.input_bar = ChatInputBar()

        def compose(self) -> ComposeResult:
            yield self.chat
            yield self.input_bar

    app = _ChatWithInput()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.chat.set_messages([_user("a"), _agent("b"), _user("c")])
        await pilot.pause()
        app.input_bar.focus_input()
        await pilot.pause()
        assert app.chat.selected_message_id is None
        await pilot.press("up")
        await pilot.pause()
        assert app.chat.selected_message_id is None, (
            "Up pressed while ChatInputBar had focus moved ChatView selection; "
            "binding dispatch is leaking across widgets."
        )
        await pilot.press("down")
        await pilot.pause()
        assert app.chat.selected_message_id is None, (
            "Down pressed while ChatInputBar had focus moved ChatView selection."
        )


@pytest.mark.asyncio
async def test_up_down_keys_move_selection_when_chat_view_has_focus() -> None:
    app = _ChatHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.chat.set_messages([_user("a"), _agent("b"), _user("c")])
        await pilot.pause()
        app.chat.focus()
        await pilot.pause()
        ids = [m.msg_id for m in app.chat._messages]

        await pilot.press("down")
        await pilot.pause()
        assert app.chat.selected_message_id == ids[0]
        await pilot.press("down")
        await pilot.pause()
        assert app.chat.selected_message_id == ids[1]
        await pilot.press("up")
        await pilot.pause()
        assert app.chat.selected_message_id == ids[0]
        await pilot.press("escape")
        await pilot.pause()
        assert app.chat.selected_message_id is None


def test_chat_view_selection_api_is_shared() -> None:
    assert hasattr(ChatView, "select_message")
    assert hasattr(ChatView, "select_next_message")
    assert hasattr(ChatView, "select_previous_message")
    assert hasattr(ChatView, "select_first_message")
    assert hasattr(ChatView, "select_last_message")
    assert hasattr(ChatView, "clear_selection")
    assert hasattr(ChatView, "copy_selected_message")
    assert hasattr(ChatView, "copy_last_copyable_message")
