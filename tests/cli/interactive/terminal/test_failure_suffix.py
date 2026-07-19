from __future__ import annotations

import io

from rich.console import Console

from openminion.cli.interactive.terminal.streaming import (
    _render_full_tool_block,
    _render_tool_block,
)
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.cli.presentation.models import (
    ChatMessage,
    MessageKind,
    ToolEvent,
)


def _event(*, exit_code: int | None, lines: int = 5, tool: str = "Bash") -> ToolEvent:
    body = "\n".join(f"line {i}" for i in range(1, lines + 1))
    return ToolEvent(
        tool_name=tool,
        args={"cmd": "echo"},
        content=body,
        full_content=body,
        exit_code=exit_code,
    )


def _render_to_string(renderable) -> str:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    console.print(renderable)
    return buf.getvalue()


def test_failure_suffix_exit_1() -> None:
    out = _render_to_string(_render_tool_block(_event(exit_code=1)))
    assert "✗ (exit 1)" in out


def test_no_suffix_on_success() -> None:
    out = _render_to_string(_render_tool_block(_event(exit_code=0)))
    assert "✗" not in out
    assert "exit" not in out


def test_no_suffix_on_none_exit_code() -> None:
    out = _render_to_string(_render_tool_block(_event(exit_code=None)))
    assert "✗" not in out


def test_failure_suffix_sigkill_137() -> None:
    out = _render_to_string(_render_tool_block(_event(exit_code=137)))
    assert "✗ (exit 137)" in out


def test_failure_suffix_signal_negative() -> None:
    out = _render_to_string(_render_tool_block(_event(exit_code=-9)))
    assert "✗ (exit -9)" in out


def test_failure_suffix_in_full_render_block() -> None:
    out = _render_to_string(_render_full_tool_block(_event(exit_code=2)))
    assert "✗ (exit 2)" in out


def test_no_suffix_in_full_render_on_success() -> None:
    out = _render_to_string(_render_full_tool_block(_event(exit_code=0)))
    assert "✗" not in out


def _push_through_transcript(verbosity: str, exit_code: int) -> str:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    t = TerminalTranscript(console, verbosity=verbosity)
    t.push_message(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:Bash",
            body="",
            tool_event=_event(exit_code=exit_code, lines=10),
        )
    )
    return buf.getvalue()


def test_normal_mode_shows_suffix_on_failure() -> None:
    out = _push_through_transcript("normal", exit_code=1)
    assert "✗ (exit 1)" in out
    assert "line 1" in out


def test_normal_mode_truncation_still_works_with_failure() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    t = TerminalTranscript(console, verbosity="normal")
    t.push_message(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:Bash",
            body="",
            tool_event=_event(exit_code=1, lines=20),
        )
    )
    out = buf.getvalue()
    assert "✗ (exit 1)" in out
    assert "… +" in out  # truncation summary present


def test_verbose_mode_shows_suffix_with_full_body() -> None:
    out = _push_through_transcript("verbose", exit_code=1)
    assert "✗ (exit 1)" in out
    assert "line 1" in out
    assert "line 10" in out
    assert "… +" not in out  # no truncation in verbose under cap


def test_quiet_mode_hides_block_entirely() -> None:
    out = _push_through_transcript("quiet", exit_code=1)
    assert "✗" not in out
    assert "exit" not in out
    assert "line 1" not in out


def test_expand_path_shows_suffix() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    t = TerminalTranscript(console, verbosity="normal")
    t.push_message(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:Bash",
            body="",
            tool_event=_event(exit_code=1, lines=20),
        )
    )
    pre_len = len(buf.getvalue())
    t.expand_block(1)
    expanded = buf.getvalue()[pre_len:]
    assert "✗ (exit 1)" in expanded


def test_marker_still_red_on_failure() -> None:
    buf = io.StringIO()
    console = Console(
        file=buf, force_terminal=True, width=120, color_system="truecolor"
    )
    console.print(_render_tool_block(_event(exit_code=1)))
    out = buf.getvalue()
    assert "\x1b[" in out
    assert "●" in out


def test_marker_still_green_on_success() -> None:
    buf = io.StringIO()
    console = Console(
        file=buf, force_terminal=True, width=120, color_system="truecolor"
    )
    console.print(_render_tool_block(_event(exit_code=0)))
    out = buf.getvalue()
    assert "●" in out
    assert "✗" not in out
