from __future__ import annotations

import io

from rich.console import Console

from openminion.cli.tui.terminal.transcript import TerminalTranscript
from openminion.cli.tui.presentation.models import (
    ChatMessage,
    MessageKind,
    ToolEvent,
)


def _make_transcript(
    verbosity: str = "normal",
) -> tuple[TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=160,
        color_system="truecolor",
    )
    return TerminalTranscript(console, verbosity=verbosity), buf


def _diff_body(num_changes: int = 1) -> str:
    lines = ["--- a/foo.py", "+++ b/foo.py", f"@@ -1,{num_changes} +1,{num_changes} @@"]
    for i in range(num_changes):
        lines.append(f"-old line {i}")
        lines.append(f"+new line {i}")
    return "\n".join(lines)


def _push_edit_diff(
    transcript: TerminalTranscript,
    *,
    num_changes: int = 1,
    exit_code: int = 0,
    tool_name: str = "Edit",
) -> None:
    body = _diff_body(num_changes=num_changes)
    event = ToolEvent(
        tool_name=tool_name,
        args={"path": "foo.py"},
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


def _push_user(t: TerminalTranscript, body: str = "edit foo") -> None:
    t.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body=body))


def _push_agent(t: TerminalTranscript, body: str = "done") -> None:
    t.push_message(ChatMessage(kind=MessageKind.AGENT, sender="agent", body=body))


def test_quiet_hides_edit_diff_block() -> None:
    t, buf = _make_transcript("quiet")
    _push_user(t)
    _push_edit_diff(t, num_changes=5)
    _push_agent(t)
    out = buf.getvalue()
    assert "old line 0" not in out
    assert "new line 0" not in out
    assert "1 tool call hidden" in out


def test_quiet_failed_edit_diff_counted_in_summary() -> None:
    t, buf = _make_transcript("quiet")
    _push_user(t)
    _push_edit_diff(t, num_changes=3, exit_code=1)
    _push_agent(t)
    out = buf.getvalue()
    assert "1 tool call hidden" in out
    assert "1 failed" in out


def test_quiet_diff_inspectable_via_expand() -> None:
    t, buf = _make_transcript("quiet")
    _push_user(t)
    _push_edit_diff(t, num_changes=5)
    pre_len = len(buf.getvalue())
    assert t.expand_block(1) is True
    expanded = buf.getvalue()[pre_len:]
    assert "old line 0" in expanded
    assert "new line 4" in expanded
    assert "\x1b[" in expanded


def test_normal_truncates_long_diff_at_six_lines() -> None:
    t, buf = _make_transcript("normal")
    _push_user(t)
    _push_edit_diff(t, num_changes=15)  # ~30+ lines body
    out = buf.getvalue()
    assert "--- a/foo.py" in out
    assert "+++ b/foo.py" in out
    assert "… +" in out
    assert "/expand" in out


def test_normal_short_diff_no_summary() -> None:
    t, buf = _make_transcript("normal")
    _push_user(t)
    _push_edit_diff(t, num_changes=1)  # ~5 lines body
    out = buf.getvalue()
    assert "old line 0" in out
    assert "new line 0" in out
    assert "… +" not in out


def test_normal_expand_re_renders_full_colored_diff() -> None:
    t, buf = _make_transcript("normal")
    _push_user(t)
    _push_edit_diff(t, num_changes=15)
    pre_len = len(buf.getvalue())
    t.expand_block(1)
    expanded = buf.getvalue()[pre_len:]
    assert "new line 14" in expanded
    assert "… +" not in expanded


def test_verbose_shows_full_50_line_diff_no_summary() -> None:
    t, buf = _make_transcript("verbose")
    _push_user(t)
    _push_edit_diff(t, num_changes=24)  # 24*2 + 3 headers ≈ 51 lines
    out = buf.getvalue()
    assert "old line 23" in out
    assert "new line 23" in out
    assert "… +" not in out


def test_verbose_caps_at_200_lines_for_300_line_diff() -> None:
    t, buf = _make_transcript("verbose")
    _push_user(t)
    _push_edit_diff(t, num_changes=150)  # 150*2 + 3 headers = 303 lines
    out = buf.getvalue()
    assert "old line 0" in out
    assert "new line 0" in out
    assert "old line 100" not in out
    assert "… +" in out
    assert "/expand" in out


def test_verbose_failure_diff_shows_suffix_and_full_body() -> None:
    t, buf = _make_transcript("verbose")
    _push_user(t)
    _push_edit_diff(t, num_changes=5, exit_code=137)
    out = buf.getvalue()
    assert "✗ (exit 137)" in out
    assert "old line 0" in out
    assert "new line 4" in out


def test_expand_in_verbose_mode_shows_past_200_cap() -> None:
    t, buf = _make_transcript("verbose")
    _push_user(t)
    _push_edit_diff(t, num_changes=150)  # 303 lines
    pre_len = len(buf.getvalue())
    t.expand_block(1)
    expanded = buf.getvalue()[pre_len:]
    assert "new line 130" in expanded
    assert "old line 130" in expanded
    assert "… +" not in expanded


def test_write_tool_with_diff_body_routes_to_diff_renderer() -> None:
    t, buf = _make_transcript("normal")
    _push_user(t)
    _push_edit_diff(t, num_changes=1, tool_name="Write")
    out = buf.getvalue()
    # Diff coloring fires (ANSI escapes present).
    assert "\x1b[" in out
    assert "old line 0" in out
    assert "new line 0" in out


def test_quiet_two_diff_blocks_one_failure_summary() -> None:
    t, buf = _make_transcript("quiet")
    _push_user(t)
    _push_edit_diff(t, num_changes=1, exit_code=0)
    _push_edit_diff(t, num_changes=1, exit_code=1)
    _push_agent(t)
    out = buf.getvalue()
    assert "2 tool calls hidden" in out
    assert "1 failed" in out


def test_normal_two_diff_blocks_no_hidden_summary() -> None:
    t, buf = _make_transcript("normal")
    _push_user(t)
    _push_edit_diff(t, num_changes=1)
    _push_edit_diff(t, num_changes=1)
    _push_agent(t)
    out = buf.getvalue()
    assert "hidden" not in out
