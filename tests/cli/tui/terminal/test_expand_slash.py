from __future__ import annotations

import io

from rich.console import Console

from openminion.cli.tui.terminal.transcript import TerminalTranscript
from openminion.cli.tui.presentation.models import (
    ChatMessage,
    MessageKind,
    ToolEvent,
)


def _make_transcript() -> tuple[TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    return TerminalTranscript(console), buf


def _push_long_tool(
    transcript: TerminalTranscript, *, tool_name: str, lines: int, exit_code: int = 0
) -> None:
    body = "\n".join(f"{tool_name}-line {i}" for i in range(1, lines + 1))
    event = ToolEvent(
        tool_name=tool_name,
        args={"cmd": f"echo {tool_name}"},
        content=body,
        full_content=body,
        exit_code=exit_code,
    )
    transcript.push_message(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender=f"tool:{tool_name}",
            body="",
            tool_event=event,
        )
    )


def test_expand_after_truncated_block_shows_full_body() -> None:
    t, buf = _make_transcript()
    _push_long_tool(t, tool_name="Bash", lines=30)
    pre_len = len(buf.getvalue())
    t.expand_block(1)
    expanded_output = buf.getvalue()[pre_len:]
    # All 30 lines visible.
    assert "Bash-line 1" in expanded_output
    assert "Bash-line 15" in expanded_output
    assert "Bash-line 30" in expanded_output
    # No truncation summary in the expanded re-render.
    assert "/expand" not in expanded_output
    assert "… +" not in expanded_output


def test_expand_with_no_truncated_blocks_emits_dim_hint() -> None:
    t, buf = _make_transcript()
    pre_len = len(buf.getvalue())
    t.expand_block()
    output = buf.getvalue()[pre_len:]
    assert "no truncated tool blocks" in output


def test_expand_short_block_not_in_truncated_list() -> None:
    t, buf = _make_transcript()
    _push_long_tool(t, tool_name="Echo", lines=3)
    assert len(t._truncated_blocks) == 0
    pre_len = len(buf.getvalue())
    t.expand_block()
    output = buf.getvalue()[pre_len:]
    assert "no truncated tool blocks" in output


def test_expand_index_2_returns_second_most_recent() -> None:
    t, buf = _make_transcript()
    _push_long_tool(t, tool_name="First", lines=20)
    _push_long_tool(t, tool_name="Second", lines=20)
    assert len(t._truncated_blocks) == 2
    pre_len = len(buf.getvalue())
    t.expand_block(2)
    output = buf.getvalue()[pre_len:]
    # Index 2 is the OLDER (First) block.
    assert "First-line 1" in output
    # Second's body should NOT appear in this single expand call.
    # (It might appear earlier in the buffer from the original
    # render, but not in the slice from `pre_len` onward.)
    assert "Second-line" not in output


def test_expand_index_0_lists_all_truncated_blocks() -> None:
    t, buf = _make_transcript()
    _push_long_tool(t, tool_name="Bash", lines=20)
    _push_long_tool(t, tool_name="Read", lines=20)
    pre_len = len(buf.getvalue())
    t.expand_block(0)
    output = buf.getvalue()[pre_len:]
    assert "Truncated tool blocks" in output
    # Most-recent-first: Read is index 1, Bash is index 2.
    assert "1." in output
    assert "Read" in output
    assert "2." in output
    assert "Bash" in output


def test_expand_out_of_range_index_emits_red_error() -> None:
    t, buf = _make_transcript()
    _push_long_tool(t, tool_name="Bash", lines=20)
    pre_len = len(buf.getvalue())
    t.expand_block(99)
    output = buf.getvalue()[pre_len:]
    assert "no truncated block at index 99" in output


def test_expand_negative_index_emits_red_error() -> None:
    t, buf = _make_transcript()
    _push_long_tool(t, tool_name="Bash", lines=20)
    pre_len = len(buf.getvalue())
    t.expand_block(-1)
    output = buf.getvalue()[pre_len:]
    assert "no truncated block at index -1" in output


def test_expand_block_returns_true_on_success() -> None:
    t, _ = _make_transcript()
    _push_long_tool(t, tool_name="Bash", lines=20)
    assert t.expand_block(1) is True


def test_expand_block_returns_false_on_no_blocks() -> None:
    t, _ = _make_transcript()
    assert t.expand_block(1) is False


def test_expand_block_returns_false_on_out_of_range() -> None:
    t, _ = _make_transcript()
    _push_long_tool(t, tool_name="Bash", lines=20)
    assert t.expand_block(99) is False


def test_truncated_blocks_only_grow_on_actual_truncation() -> None:
    t, _ = _make_transcript()
    _push_long_tool(t, tool_name="Short", lines=2)
    _push_long_tool(t, tool_name="Long", lines=20)
    _push_long_tool(t, tool_name="AlsoShort", lines=4)
    _push_long_tool(t, tool_name="LongAgain", lines=15)
    # Only Long + LongAgain (>6 lines each) tracked.
    assert len(t._truncated_blocks) == 2
    names = [e.tool_name for e in t._truncated_blocks]
    assert "Long" in names
    assert "LongAgain" in names
    assert "Short" not in names


def test_expand_slash_registered_in_shell_catalog() -> None:
    from openminion.cli.tui.terminal.shell import _SLASH_COMMANDS

    assert "/expand" in _SLASH_COMMANDS
