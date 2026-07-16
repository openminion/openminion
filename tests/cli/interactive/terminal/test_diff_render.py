from __future__ import annotations

import io

from rich.console import Console

from openminion.cli.interactive.terminal.streaming import (
    _TOOL_BLOCK_TRUNCATE_LINES,
    _TOOL_BLOCK_VERBOSE_MAX_LINES,
    _diff_line_style,
    _render_diff_block,
)
from openminion.cli.presentation.models import ToolEvent


def _make_event(
    body: str,
    *,
    tool_name: str = "Edit",
    exit_code: int | None = 0,
    path: str = "foo.py",
) -> ToolEvent:
    return ToolEvent(
        tool_name=tool_name,
        args={"path": path},
        content=body,
        full_content=body,
        exit_code=exit_code,
    )


def _render_to_string(renderable, *, color: bool = False) -> str:
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=color,
        width=120,
        color_system="truecolor" if color else None,
    )
    console.print(renderable)
    return buf.getvalue()


_SHORT_DIFF = """--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,3 @@
 ctx
-old
+new
"""

_THIRTY_LINE_DIFF = "\n".join(
    [
        "--- a/foo.py",
        "+++ b/foo.py",
        "@@ -1,15 +1,15 @@",
    ]
    + [f" ctx-line {i}" for i in range(1, 13)]
    + ["-removed", "+added"]
    + [f" trailing-{i}" for i in range(1, 13)]
)


def test_diff_line_style_addition() -> None:
    assert _diff_line_style("+added") == "green"


def test_diff_line_style_deletion() -> None:
    assert _diff_line_style("-removed") == "red"


def test_diff_line_style_hunk_header() -> None:
    assert _diff_line_style("@@ -1,5 +1,7 @@") == "cyan"


def test_diff_line_style_file_header_plus() -> None:
    assert _diff_line_style("+++ b/foo.py") == "bold"


def test_diff_line_style_file_header_minus() -> None:
    assert _diff_line_style("--- a/foo.py") == "bold"


def test_diff_line_style_context_default() -> None:
    assert _diff_line_style(" context line") == ""


def test_diff_line_style_blank_line_default() -> None:
    assert _diff_line_style("") == ""


def test_diff_line_style_git_diff_extra() -> None:
    assert _diff_line_style("diff --git a/foo b/foo") == "dim"


def test_diff_line_style_index_extra() -> None:
    assert _diff_line_style("index 1234567..abcdefg 100644") == "dim"


def test_short_diff_renders_all_lines() -> None:
    out = _render_to_string(_render_diff_block(_make_event(_SHORT_DIFF)))
    assert "Edit" in out
    assert "--- a/foo.py" in out
    assert "+++ b/foo.py" in out
    assert "@@ -1,2 +1,3 @@" in out
    assert "ctx" in out
    assert "old" in out
    assert "new" in out
    assert "… +" not in out


def test_long_diff_truncated_at_default_cap() -> None:
    out = _render_to_string(_render_diff_block(_make_event(_THIRTY_LINE_DIFF)))
    assert "--- a/foo.py" in out
    assert "+++ b/foo.py" in out
    assert "@@ -1,15 +1,15 @@" in out
    assert "… +" in out
    assert "/expand" in out


def test_diff_with_cap_none_shows_everything() -> None:
    out = _render_to_string(
        _render_diff_block(_make_event(_THIRTY_LINE_DIFF), cap=None)
    )
    assert "trailing-12" in out
    assert "… +" not in out


def test_diff_with_cap_200_verbose_mode() -> None:
    out = _render_to_string(_render_diff_block(_make_event(_THIRTY_LINE_DIFF), cap=200))
    assert "trailing-12" in out
    assert "… +" not in out


def test_diff_300_lines_with_cap_200_truncates() -> None:
    body = "\n".join(
        ["--- a/foo.py", "+++ b/foo.py", "@@ -1,300 +1,300 @@"]
        + [f"+line-{i}" for i in range(1, 301)]
    )
    out = _render_to_string(
        _render_diff_block(_make_event(body), cap=_TOOL_BLOCK_VERBOSE_MAX_LINES)
    )
    assert "line-1" in out
    assert "line-201" not in out
    assert "… +" in out


def test_addition_renders_green_ansi() -> None:
    out = _render_to_string(_render_diff_block(_make_event(_SHORT_DIFF)), color=True)
    assert "\x1b[" in out
    assert "new" in out


def test_marker_styled_in_color_mode() -> None:
    out = _render_to_string(_render_diff_block(_make_event(_SHORT_DIFF)), color=True)
    assert "●" in out
    assert "\x1b[" in out


def test_failure_suffix_appears_on_nonzero_exit() -> None:
    out = _render_to_string(_render_diff_block(_make_event(_SHORT_DIFF, exit_code=1)))
    assert "✗ (exit 1)" in out


def test_no_suffix_on_zero_exit() -> None:
    out = _render_to_string(_render_diff_block(_make_event(_SHORT_DIFF, exit_code=0)))
    assert "✗" not in out


def test_no_suffix_on_none_exit() -> None:
    out = _render_to_string(
        _render_diff_block(_make_event(_SHORT_DIFF, exit_code=None))
    )
    assert "✗" not in out


def test_failure_suffix_in_verbose_mode() -> None:
    out = _render_to_string(
        _render_diff_block(_make_event(_SHORT_DIFF, exit_code=137), cap=200)
    )
    assert "✗ (exit 137)" in out
    assert "new" in out


def test_title_uses_path_arg_for_edit() -> None:
    out = _render_to_string(
        _render_diff_block(_make_event(_SHORT_DIFF, path="src/foo.py"))
    )
    assert "Edit" in out
    assert "src/foo.py" in out


def test_title_uses_write_when_tool_is_write() -> None:
    out = _render_to_string(
        _render_diff_block(_make_event(_SHORT_DIFF, tool_name="Write"))
    )
    assert "Write" in out


def test_empty_body_renders_no_output_marker() -> None:
    event = ToolEvent(
        tool_name="Edit",
        args={"path": "foo.py"},
        content="",
        full_content="",
        exit_code=0,
    )
    out = _render_to_string(_render_diff_block(event))
    assert "(no output)" in out


def test_default_cap_is_truncate_lines() -> None:
    import inspect

    sig = inspect.signature(_render_diff_block)
    assert sig.parameters["cap"].default == _TOOL_BLOCK_TRUNCATE_LINES


def test_render_tool_block_routes_edit_diff_to_diff_renderer() -> None:
    from openminion.cli.interactive.terminal.streaming import _render_tool_block

    event = _make_event(_SHORT_DIFF, tool_name="Edit")
    out = _render_to_string(_render_tool_block(event), color=True)
    assert "new" in out
    assert "old" in out
    assert "\x1b[" in out


def test_render_tool_block_falls_through_for_non_diff_edit_body() -> None:
    from openminion.cli.interactive.terminal.streaming import _render_tool_block

    event = _make_event(
        "def hello():\n    return 1\n",
        tool_name="Edit",
    )
    out = _render_to_string(_render_tool_block(event))
    assert "def hello" in out
    assert "@@" not in out


def test_render_tool_block_falls_through_for_read_with_diff_body() -> None:
    from openminion.cli.interactive.terminal.streaming import _render_tool_block

    event = _make_event(_SHORT_DIFF, tool_name="Read")
    out = _render_to_string(_render_tool_block(event))
    assert "old" in out
    assert "new" in out


def test_render_tool_block_falls_through_for_bash_with_git_diff_output() -> None:
    from openminion.cli.interactive.terminal.streaming import _render_tool_block

    event = _make_event(_SHORT_DIFF, tool_name="Bash")
    out = _render_to_string(_render_tool_block(event))
    assert "old" in out
    assert "new" in out


def test_render_tool_block_malformed_at_routes_via_detection_miss() -> None:
    from openminion.cli.interactive.terminal.streaming import _render_tool_block

    body = """--- a/foo.py
+++ b/foo.py
@@ -abc +xyz @@
 ctx
-old
+new
"""
    event = _make_event(body, tool_name="Edit")
    out = _render_to_string(_render_tool_block(event))
    assert "old" in out
    assert "new" in out


def test_render_full_tool_block_routes_edit_diff_with_cap_none() -> None:
    from openminion.cli.interactive.terminal.streaming import (
        _render_full_tool_block,
    )

    event = _make_event(_THIRTY_LINE_DIFF, tool_name="Edit")
    out = _render_to_string(_render_full_tool_block(event, cap=None))
    assert "trailing-12" in out
    assert "… +" not in out


def test_render_full_tool_block_routes_edit_diff_with_cap_200() -> None:
    from openminion.cli.interactive.terminal.streaming import (
        _render_full_tool_block,
    )

    event = _make_event(_THIRTY_LINE_DIFF, tool_name="Edit")
    out = _render_to_string(_render_full_tool_block(event, cap=200))
    assert "trailing-12" in out
    assert "… +" not in out


def test_render_full_tool_block_falls_through_for_non_diff_edit() -> None:
    from openminion.cli.interactive.terminal.streaming import (
        _render_full_tool_block,
    )

    event = _make_event(
        "def hello():\n    return 1\n",
        tool_name="Edit",
    )
    out = _render_to_string(_render_full_tool_block(event, cap=None))
    assert "def hello" in out
    assert "@@" not in out
