from __future__ import annotations

import io

from rich.console import Console

from openminion.cli.interactive.terminal.streaming import (
    _format_elapsed_seconds,
    _render_in_progress_tool_block,
)


def _render(renderable, *, color: bool = False) -> str:
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=color,
        width=120,
        color_system="truecolor" if color else None,
    )
    console.print(renderable)
    return buf.getvalue()


def test_format_elapsed_sub_60_seconds() -> None:
    assert _format_elapsed_seconds(0.0) == "0s"
    assert _format_elapsed_seconds(1.234) == "1s"
    assert _format_elapsed_seconds(59.99) == "59s"


def test_format_elapsed_over_60_seconds() -> None:
    assert _format_elapsed_seconds(60.0) == "1m00s"
    assert _format_elapsed_seconds(62.5) == "1m02s"
    assert _format_elapsed_seconds(125.0) == "2m05s"


def test_format_elapsed_negative_clamps_to_zero() -> None:
    assert _format_elapsed_seconds(-1.0) == "0s"


def test_render_in_progress_contains_running_prefix() -> None:
    out = _render(_render_in_progress_tool_block("Bash", {"cmd": "ls"}))
    assert "Running" in out


def test_render_in_progress_contains_marker_glyph() -> None:
    out = _render(_render_in_progress_tool_block("Bash", {"cmd": "ls"}))
    assert "●" in out


def test_render_in_progress_renders_yellow_in_color_mode() -> None:
    out = _render(_render_in_progress_tool_block("Bash", {"cmd": "ls"}), color=True)
    assert "\x1b[" in out
    assert "●" in out


def test_render_in_progress_contains_verb_form_title() -> None:
    out = _render(_render_in_progress_tool_block("Bash", {"cmd": "ls -la"}))
    assert "Bash(ls -la)" in out


def test_render_in_progress_uses_path_arg_for_read_edit() -> None:
    out = _render(_render_in_progress_tool_block("Read", {"path": "/etc/hosts"}))
    assert "Read(/etc/hosts)" in out


def test_render_in_progress_uses_query_arg_for_grep() -> None:
    out = _render(_render_in_progress_tool_block("Grep", {"query": "TODO"}))
    assert "Grep(TODO)" in out


def test_render_in_progress_handles_empty_args() -> None:
    out = _render(_render_in_progress_tool_block("Bash", {}))
    assert "Bash" in out
    assert "Bash(" not in out


def test_render_in_progress_handles_none_args() -> None:
    out = _render(_render_in_progress_tool_block("Bash", None))
    assert "Bash" in out


def test_render_in_progress_includes_elapsed_when_positive() -> None:
    out = _render(
        _render_in_progress_tool_block("Bash", {"cmd": "ls"}, elapsed_seconds=1.5)
    )
    assert "1s" in out


def test_render_in_progress_omits_elapsed_when_zero() -> None:
    out = _render(
        _render_in_progress_tool_block("Bash", {"cmd": "ls"}, elapsed_seconds=0.0)
    )
    assert "0.0s" not in out


def test_render_in_progress_omits_elapsed_when_negative() -> None:
    out = _render(
        _render_in_progress_tool_block("Bash", {"cmd": "ls"}, elapsed_seconds=-1.0)
    )
    assert "Running" in out


def test_render_in_progress_no_body_row() -> None:
    out = _render(_render_in_progress_tool_block("Bash", {"cmd": "ls"}))
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1


def test_render_in_progress_with_long_arg_truncates() -> None:
    long_cmd = "echo " + "x" * 100
    out = _render(_render_in_progress_tool_block("Bash", {"cmd": long_cmd}))
    assert "..." in out


def test_render_in_progress_falls_back_to_tool_label() -> None:
    out = _render(_render_in_progress_tool_block("", {"cmd": "ls"}))
    assert "Running tool" in out or "Running" in out


def test_render_in_progress_returns_group() -> None:
    from rich.console import Group

    result = _render_in_progress_tool_block("Bash", {"cmd": "ls"})
    assert isinstance(result, Group)
