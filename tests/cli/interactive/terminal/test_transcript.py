from __future__ import annotations

import io

from rich.console import Console

from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.cli.presentation.contracts import TranscriptSink
from openminion.cli.presentation.models import (
    ChatMessage,
    MessageKind,
    ToolEvent,
)


def _make_transcript() -> tuple[TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)
    return TerminalTranscript(console), buf


def test_transcript_satisfies_protocol() -> None:
    t, _ = _make_transcript()
    assert isinstance(t, TranscriptSink)


def test_push_user_message_renders_with_prefix() -> None:
    t, buf = _make_transcript()
    t.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body="hi"))
    output = buf.getvalue()
    assert "hi" in output
    assert ">" in output  # user prefix


def test_push_agent_message_plain_text() -> None:
    t, buf = _make_transcript()
    t.push_message(ChatMessage(kind=MessageKind.AGENT, sender="agent", body="reply"))
    assert "reply" in buf.getvalue()


def test_push_agent_markdown_renders_via_markdown() -> None:
    t, buf = _make_transcript()
    body = "# heading\n\n- item one\n- item two"
    t.push_message(ChatMessage(kind=MessageKind.AGENT, sender="agent", body=body))
    output = buf.getvalue()
    # Markdown rendering produces the heading text + list bullets
    # (Rich uses different glyphs than `-`).
    assert "heading" in output
    assert "item one" in output


def test_push_system_message_styled() -> None:
    t, buf = _make_transcript()
    t.push_message(ChatMessage(kind=MessageKind.SYSTEM, sender="system", body="info"))
    assert "info" in buf.getvalue()


def test_push_error_message_styled() -> None:
    t, buf = _make_transcript()
    t.push_message(ChatMessage(kind=MessageKind.ERROR, sender="error", body="bad"))
    assert "bad" in buf.getvalue()


def test_push_tool_message_renders_block() -> None:
    t, buf = _make_transcript()
    event = ToolEvent(
        tool_name="bash",
        args={"cmd": "ls"},
        content="file1\nfile2",
        full_content="file1\nfile2",
        exit_code=0,
    )
    t.push_message(
        ChatMessage(kind=MessageKind.TOOL, sender="bash", body="", tool_event=event)
    )
    output = buf.getvalue()
    assert "bash" in output
    assert "file1" in output


def test_set_messages_resets_in_memory_list() -> None:
    t, _ = _make_transcript()
    t.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body="a"))
    t.push_message(ChatMessage(kind=MessageKind.AGENT, sender="agent", body="b"))
    t.set_messages([ChatMessage(kind=MessageKind.USER, sender="you", body="c")])
    # Only the new message survives in the in-memory list.
    assert len(t._messages) == 1
    assert t._messages[0].body == "c"


def test_clear_messages_drops_list_and_prints_divider() -> None:
    t, buf = _make_transcript()
    t.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body="x"))
    t.clear_messages()
    assert t._messages == []
    assert "─" in buf.getvalue()  # divider


def test_reset_session_state_clears_live_render_state() -> None:
    t, _ = _make_transcript()
    event = ToolEvent(
        tool_name="bash",
        args={"cmd": "ls"},
        content="file1\nfile2\nfile3\nfile4\nfile5\nfile6\nfile7",
        full_content="file1\nfile2\nfile3\nfile4\nfile5\nfile6\nfile7",
        exit_code=0,
        call_id="call-1",
    )
    t._truncated_blocks = [event]
    t._live_narrated_call_ids = {"call-1"}
    t._hidden_tool_count = 2
    t._hidden_failed_count = 1

    t.reset_session_state()

    assert t._truncated_blocks == []
    assert t._live_narrated_call_ids == set()
    assert t._hidden_tool_count == 0
    assert t._hidden_failed_count == 0


def test_filter_messages_is_no_op_with_hint() -> None:
    t, buf = _make_transcript()
    t.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body="x"))
    pre = buf.getvalue()
    t.filter_messages("query")
    post = buf.getvalue()
    # Filter prints a hint; in-memory list unchanged.
    assert len(t._messages) == 1
    assert "filter" in post.lower() or post == pre


def test_copy_selected_message_returns_body() -> None:
    t, _ = _make_transcript()
    t.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body="copy me"))
    assert t.copy_selected_message() == "copy me"


def test_copy_last_copyable_message_falls_back() -> None:
    t, _ = _make_transcript()
    t.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body="first"))
    t.push_message(ChatMessage(kind=MessageKind.AGENT, sender="agent", body="last"))
    assert t.copy_last_copyable_message() == "last"


def test_drop_message_removes_from_list() -> None:
    t, _ = _make_transcript()
    t.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body="a"))
    msg_id = t._messages[0].msg_id
    assert t.drop_message(msg_id) is True
    assert t._messages == []


def test_drop_message_returns_false_when_not_found() -> None:
    t, _ = _make_transcript()
    assert t.drop_message("nonexistent") is False
