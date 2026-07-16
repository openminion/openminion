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


def _event(
    body: str,
    *,
    tool_name: str = "Edit",
    exit_code: int | None = 0,
) -> ToolEvent:
    return ToolEvent(
        tool_name=tool_name,
        args={"path": "foo.py"},
        content=body,
        full_content=body,
        exit_code=exit_code,
    )


def _render_to_string(renderable) -> str:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    console.print(renderable)
    return buf.getvalue()


def test_empty_body_falls_back_to_no_output_marker() -> None:
    out = _render_to_string(_render_tool_block(_event("")))
    assert "(no output)" in out


def test_partial_diff_header_only_falls_through() -> None:
    body = """@@ -1,5 +1,7 @@
 ctx
more ctx
"""
    out = _render_to_string(_render_tool_block(_event(body)))
    assert "ctx" in out


def test_only_file_headers_no_hunk_falls_through() -> None:
    body = """--- a/foo.py
+++ b/foo.py
"""
    out = _render_to_string(_render_tool_block(_event(body)))
    assert "a/foo.py" in out


def test_git_diff_stat_falls_through() -> None:
    body = """ foo.py | 5 +++--
 bar.py | 3 ++-
 2 files changed, 6 insertions(+), 2 deletions(-)
"""
    out = _render_to_string(_render_tool_block(_event(body)))
    assert "foo.py" in out


def test_malformed_hunk_non_numeric_falls_through() -> None:
    body = """--- a/foo.py
+++ b/foo.py
@@ -abc +xyz @@
 ctx
-old
+new
"""
    out = _render_to_string(_render_tool_block(_event(body)))
    assert "old" in out
    assert "new" in out


def test_read_tool_with_diff_body_falls_through() -> None:
    body = """--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,3 @@
 ctx
-old
+new
"""
    out = _render_to_string(_render_tool_block(_event(body, tool_name="Read")))
    assert "old" in out
    assert "new" in out


def test_bash_with_git_diff_output_falls_through() -> None:
    body = """diff --git a/foo.py b/foo.py
index 1234567..abcdefg 100644
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,3 @@
 ctx
-old
+new
"""
    out = _render_to_string(_render_tool_block(_event(body, tool_name="Bash")))
    assert "Bash" in out
    assert "… +" in out
    assert "/expand" in out
    assert "diff --git" in out


def test_write_tool_with_plaintext_falls_through() -> None:
    body = """def hello():
    return 1

print(hello())
"""
    out = _render_to_string(_render_tool_block(_event(body, tool_name="Write")))
    assert "def hello" in out
    assert "@@" not in out


def test_truncated_blocks_populated_for_normal_diff_block() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    t = TerminalTranscript(console, verbosity="normal")
    body = "\n".join(
        ["--- a/foo.py", "+++ b/foo.py", "@@ -1,15 +1,15 @@"]
        + [f"-old-{i}" for i in range(15)]
        + [f"+new-{i}" for i in range(15)]
    )
    event = ToolEvent(
        tool_name="Edit",
        args={"path": "foo.py"},
        content=body,
        full_content=body,
        exit_code=0,
    )
    t.push_message(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:Edit",
            body="",
            tool_event=event,
        )
    )
    assert len(t._truncated_blocks) == 1


def test_truncated_blocks_populated_for_quiet_diff_block() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    t = TerminalTranscript(console, verbosity="quiet")
    body = """--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,3 @@
 ctx
-old
+new
"""
    event = ToolEvent(
        tool_name="Edit",
        args={"path": "foo.py"},
        content=body,
        full_content=body,
        exit_code=0,
    )
    t.push_message(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:Edit",
            body="",
            tool_event=event,
        )
    )
    assert len(t._truncated_blocks) == 1


def test_truncated_blocks_NOT_populated_for_short_diff_in_normal() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    t = TerminalTranscript(console, verbosity="normal")
    body = """--- a/foo.py
+++ b/foo.py
@@ -1,1 +1,1 @@
-old
+new
"""
    event = ToolEvent(
        tool_name="Edit",
        args={"path": "foo.py"},
        content=body,
        full_content=body,
        exit_code=0,
    )
    t.push_message(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:Edit",
            body="",
            tool_event=event,
        )
    )
    assert len(t._truncated_blocks) == 0


def test_render_full_tool_block_falls_through_for_non_diff() -> None:
    body = "def hello():\n    return 1\n"
    out = _render_to_string(_render_full_tool_block(_event(body), cap=None))
    assert "def hello" in out
    assert "@@" not in out


def test_render_full_tool_block_falls_through_for_read_with_diff() -> None:
    body = """--- a/foo.py
+++ b/foo.py
@@ -1,1 +1,1 @@
-old
+new
"""
    out = _render_to_string(
        _render_full_tool_block(_event(body, tool_name="Read"), cap=None)
    )
    assert "old" in out
    assert "new" in out


def test_no_tryexcept_in_render_tool_block_dispatch() -> None:
    import inspect

    from openminion.cli.interactive.terminal import streaming

    src = inspect.getsource(streaming._render_tool_block)
    dispatch_lines = [line for line in src.split("\n") if "_render_diff_block" in line]
    assert len(dispatch_lines) >= 1
    assert "try:\n        return _render_diff_block" not in src
    assert "try:\n            return _render_diff_block" not in src


def test_no_tryexcept_in_render_full_tool_block_dispatch() -> None:
    import inspect

    from openminion.cli.interactive.terminal import streaming

    src = inspect.getsource(streaming._render_full_tool_block)
    assert "try:\n        return _render_diff_block" not in src
    assert "try:\n            return _render_diff_block" not in src
