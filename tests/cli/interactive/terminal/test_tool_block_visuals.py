from __future__ import annotations

import io

from rich.console import Console

from openminion.cli.interactive.terminal.streaming import (
    _render_full_tool_block,
    _render_tool_block,
    is_truncated,
)
from openminion.cli.presentation.models import ToolEvent


def _capture(renderable) -> str:
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, width=120).print(renderable)
    return buf.getvalue()


def test_green_marker_when_exit_code_zero() -> None:
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "ls"},
        content="ok",
        exit_code=0,
    )
    output = _capture(_render_tool_block(event))
    assert "●" in output


def test_green_marker_when_exit_code_none() -> None:
    event = ToolEvent(
        tool_name="Read",
        args={"path": "x.py"},
        content="contents",
        exit_code=None,
    )
    output = _capture(_render_tool_block(event))
    assert "●" in output


def test_red_marker_when_exit_code_nonzero() -> None:
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "false"},
        content="error output",
        exit_code=1,
    )
    output = _capture(_render_tool_block(event))
    assert "●" in output


def test_body_lines_render_under_tool_stem() -> None:
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "echo"},
        content="line1\nline2\nline3",
        full_content="line1\nline2\nline3",
        exit_code=0,
    )
    output = _capture(_render_tool_block(event))
    assert "  └ line1" in output
    assert "    line2" in output
    assert "    line3" in output


def test_truncates_to_six_lines_with_summary() -> None:
    body = "\n".join(f"line {i}" for i in range(1, 31))
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "ls"},
        content=body,
        full_content=body,
        exit_code=0,
    )
    output = _capture(_render_tool_block(event))
    assert "line 1" in output
    assert "line 6" in output
    assert "line 7" not in output
    assert "line 30" not in output
    assert "… +24 lines" in output
    assert "/expand" in output


def test_no_truncation_summary_for_short_output() -> None:
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "echo"},
        content="line1\nline2",
        full_content="line1\nline2",
        exit_code=0,
    )
    output = _capture(_render_tool_block(event))
    assert "line1" in output
    assert "line2" in output
    assert "+0 lines" not in output
    assert "/expand" not in output


def test_verb_form_title_for_bash_picks_cmd_arg() -> None:
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "ls -la"},
        content="output",
        exit_code=0,
    )
    output = _capture(_render_tool_block(event))
    assert "Bash(ls -la)" in output
    assert "cmd=ls -la" not in output


def test_verb_form_title_for_read_picks_path_arg() -> None:
    event = ToolEvent(
        tool_name="Read",
        args={"path": "src/main.py"},
        content="contents",
        exit_code=0,
    )
    output = _capture(_render_tool_block(event))
    assert "Read(src/main.py)" in output


def test_verb_form_title_for_grep_picks_query_arg() -> None:
    event = ToolEvent(
        tool_name="Grep",
        args={"query": "TODO"},
        content="found 3",
        exit_code=0,
    )
    output = _capture(_render_tool_block(event))
    assert "Grep(TODO)" in output


def test_verb_form_title_long_arg_is_ellipsized() -> None:
    long_cmd = "x" * 100
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": long_cmd},
        content="ok",
        exit_code=0,
    )
    output = _capture(_render_tool_block(event))
    assert "..." in output


def test_verb_form_title_no_args_just_verb() -> None:
    event = ToolEvent(
        tool_name="ListAgents",
        args={},
        content="alpha\nbeta",
        exit_code=0,
    )
    output = _capture(_render_tool_block(event))
    assert "ListAgents" in output
    assert "ListAgents()" not in output


def test_empty_body_renders_no_output_placeholder() -> None:
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "true"},
        content="",
        exit_code=0,
    )
    output = _capture(_render_tool_block(event))
    assert "(no output)" in output


def test_is_truncated_returns_true_for_long_body() -> None:
    body = "\n".join(f"l{i}" for i in range(20))
    event = ToolEvent(
        tool_name="Bash", args={"cmd": "ls"}, content=body, full_content=body
    )
    assert is_truncated(event) is True


def test_is_truncated_returns_false_for_short_body() -> None:
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "echo"},
        content="line1\nline2",
        full_content="line1\nline2",
    )
    assert is_truncated(event) is False


def test_render_full_tool_block_drops_truncation_cap() -> None:
    body = "\n".join(f"line {i}" for i in range(1, 31))
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "ls"},
        content=body,
        full_content=body,
        exit_code=0,
    )
    output = _capture(_render_full_tool_block(event))
    assert "line 1" in output
    assert "line 15" in output
    assert "line 30" in output
    assert "… +" not in output
    assert "/expand" not in output
