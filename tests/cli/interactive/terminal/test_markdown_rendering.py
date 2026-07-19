from __future__ import annotations

import io

from rich.console import Console

from openminion.cli.interactive.terminal.streaming import _looks_like_markdown
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.cli.presentation.models import ChatMessage, MessageKind


def _make_transcript(
    *, force_terminal: bool = True
) -> tuple[TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=force_terminal,
        force_interactive=False,
        width=80,
        color_system="truecolor",
        no_color=False,
    )
    return TerminalTranscript(console), buf


def test_python_code_block_renders_with_syntax_highlighting() -> None:
    t, buf = _make_transcript()
    body = "Here is some code:\n\n```python\ndef hello():\n    return 42\n```\n"
    t.push_message(ChatMessage(kind=MessageKind.AGENT, sender="agent", body=body))
    output = buf.getvalue()
    # Body content rendered.
    assert "hello" in output
    assert "return" in output
    # Markdown rendering produced ANSI escapes (color sequences).
    assert "\x1b[" in output


def test_diff_block_renders_with_per_line_color() -> None:
    t, buf = _make_transcript()
    body = "Changes:\n\n```diff\n+ added line\n- removed line\n```\n"
    t.push_message(ChatMessage(kind=MessageKind.AGENT, sender="agent", body=body))
    output = buf.getvalue()
    assert "added line" in output
    assert "removed line" in output
    # ANSI escapes present (color highlighting fired).
    assert "\x1b[" in output


def test_plain_text_body_has_no_syntax_highlighting() -> None:
    t, buf = _make_transcript()
    body = "this is just a plain sentence with no code"
    t.push_message(ChatMessage(kind=MessageKind.AGENT, sender="agent", body=body))
    output = buf.getvalue()
    assert "this is just a plain sentence" in output


def test_inline_code_in_markdown_renders() -> None:
    t, buf = _make_transcript()
    body = "use the `os.environ` variable to read env"
    body = "Like this: `os.environ` and:\n\n```python\nx = 1\n```\n"
    t.push_message(ChatMessage(kind=MessageKind.AGENT, sender="agent", body=body))
    output = buf.getvalue()
    assert "os.environ" in output


def test_streaming_complete_with_markdown_buffer_renders_markdown() -> None:
    import time
    from openminion.cli.interactive.terminal.streaming import TerminalTurnHandle

    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        force_interactive=False,
        width=80,
        color_system="truecolor",
        no_color=False,
    )
    handle = TerminalTurnHandle(console).start()
    handle.append_token("# Heading\n\n")
    time.sleep(0.06)  # > 50 ms threshold so markdown commits
    handle.append_token("```python\nx = 1\n```\n")
    handle.complete()
    output = buf.getvalue()
    assert "Heading" in output
    # Pygments wraps each token (name, op, number) in its own ANSI
    # escape, so `x = 1` isn't contiguous — check each token instead.
    assert "x" in output
    assert "1" in output
    # Markdown rendered → ANSI escapes present.
    assert "\x1b[" in output


def test_looks_like_markdown_helper_detects_fences() -> None:
    assert _looks_like_markdown("```python\nx=1\n```")
    assert _looks_like_markdown("# heading")
    assert _looks_like_markdown("- item")


def test_looks_like_markdown_helper_rejects_plain_text() -> None:
    assert not _looks_like_markdown("just a plain sentence")
    assert not _looks_like_markdown("no markdown here")
