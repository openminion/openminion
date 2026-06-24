from __future__ import annotations

import io

from rich.console import Console

from openminion.cli.tui.terminal.transcript import TerminalTranscript
from openminion.cli.tui.presentation.models import (
    ChatMessage,
    MessageKind,
    ToolEvent,
)


def _make(verbosity: str = "quiet") -> tuple[TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    return TerminalTranscript(console, verbosity=verbosity), buf


def _push_tool(t: TerminalTranscript, *, lines: int = 5, exit_code: int = 0) -> None:
    body = "\n".join(f"line {i}" for i in range(1, lines + 1))
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "echo"},
        content=body,
        full_content=body,
        exit_code=exit_code,
    )
    t.push_message(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:Bash",
            body="",
            tool_event=event,
        )
    )


def _push_user(t: TerminalTranscript, body: str = "hi") -> None:
    t.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body=body))


def _push_agent(t: TerminalTranscript, body: str = "ok") -> None:
    t.push_message(ChatMessage(kind=MessageKind.AGENT, sender="agent", body=body))


def test_quiet_three_tools_summary_appears() -> None:
    t, buf = _make("quiet")
    _push_user(t)
    _push_tool(t)
    _push_tool(t)
    _push_tool(t)
    _push_agent(t, "done")
    output = buf.getvalue()
    assert "(3 tool calls hidden" in output
    assert "/verbose to show" in output
    assert "/expand 0 to list" in output


def test_quiet_one_tool_singular_form() -> None:
    t, buf = _make("quiet")
    _push_user(t)
    _push_tool(t)
    _push_agent(t)
    assert "(1 tool call hidden" in buf.getvalue()


def test_quiet_no_tools_no_summary() -> None:
    t, buf = _make("quiet")
    _push_user(t)
    _push_agent(t, "no tools needed")
    output = buf.getvalue()
    assert "tool call" not in output
    assert "hidden" not in output


def test_quiet_with_failure_includes_failed_count() -> None:
    t, buf = _make("quiet")
    _push_user(t)
    _push_tool(t, exit_code=0)
    _push_tool(t, exit_code=1)
    _push_agent(t)
    output = buf.getvalue()
    assert "(2 tool calls hidden" in output
    assert "1 failed" in output


def test_quiet_with_multiple_failures_counts_them() -> None:
    t, buf = _make("quiet")
    _push_user(t)
    _push_tool(t, exit_code=1)
    _push_tool(t, exit_code=137)
    _push_tool(t, exit_code=0)
    _push_agent(t)
    assert "2 failed" in buf.getvalue()


def test_quiet_no_failures_omits_failed_clause() -> None:
    t, buf = _make("quiet")
    _push_user(t)
    _push_tool(t, exit_code=0)
    _push_agent(t)
    output = buf.getvalue()
    assert "failed" not in output


def test_counter_resets_on_new_user_turn() -> None:
    t, buf = _make("quiet")
    # Turn 1: 2 hidden + agent reply.
    _push_user(t, "first")
    _push_tool(t)
    _push_tool(t)
    _push_agent(t, "first reply")
    # Turn 2: 1 hidden + agent reply.
    _push_user(t, "second")
    _push_tool(t)
    _push_agent(t, "second reply")
    output = buf.getvalue()
    assert "(2 tool calls hidden" in output
    assert "(1 tool call hidden" in output
    assert "(3 tool calls hidden" not in output


def test_idempotent_within_same_turn() -> None:
    t, buf = _make("quiet")
    _push_user(t)
    _push_tool(t)
    _push_agent(t)  # Triggers summary once.
    pre_len = len(buf.getvalue())
    t._maybe_print_hidden_tool_summary()
    assert len(buf.getvalue()) == pre_len


def test_normal_mode_does_not_print_summary() -> None:
    t, buf = _make("normal")
    _push_user(t)
    _push_tool(t)
    _push_agent(t)
    output = buf.getvalue()
    assert "hidden" not in output


def test_verbose_mode_does_not_print_summary() -> None:
    t, buf = _make("verbose")
    _push_user(t)
    _push_tool(t)
    _push_agent(t)
    output = buf.getvalue()
    assert "hidden" not in output


def test_streaming_complete_triggers_summary_in_quiet() -> None:
    t, buf = _make("quiet")
    _push_user(t)
    _push_tool(t)
    _push_tool(t)
    handle = t.begin_turn(role="assistant")
    handle.append_token("done")
    handle.complete()
    assert "(2 tool calls hidden" in buf.getvalue()


def test_streaming_complete_no_tools_no_summary() -> None:
    t, buf = _make("quiet")
    _push_user(t)
    handle = t.begin_turn(role="assistant")
    handle.append_token("just text")
    handle.complete()
    assert "hidden" not in buf.getvalue()


def test_singular_exact_format() -> None:
    t, buf = _make("quiet")
    _push_user(t)
    _push_tool(t)
    _push_agent(t)
    assert (
        "(1 tool call hidden — /verbose to show, /expand 0 to list)" in buf.getvalue()
    )


def test_plural_exact_format() -> None:
    t, buf = _make("quiet")
    _push_user(t)
    _push_tool(t)
    _push_tool(t)
    _push_agent(t)
    assert (
        "(2 tool calls hidden — /verbose to show, /expand 0 to list)" in buf.getvalue()
    )


def test_with_failure_exact_format() -> None:
    t, buf = _make("quiet")
    _push_user(t)
    _push_tool(t, exit_code=1)
    _push_agent(t)
    assert (
        "(1 tool call hidden — 1 failed; /verbose to show, /expand 0 to list)"
        in buf.getvalue()
    )
