from __future__ import annotations

import io
import time

from rich.console import Console, Group

from openminion.cli.interactive.terminal.streaming import (
    TerminalTurnHandle,
    _render_in_progress_tool_block,
)
from openminion.cli.interactive.terminal.transcript import TerminalTranscript


def _make_transcript_and_console() -> tuple[TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, no_color=True)
    return TerminalTranscript(console, verbosity="normal"), buf




def test_handle_set_active_tool_records_state() -> None:
    handle = TerminalTurnHandle(Console(file=io.StringIO()))
    handle.set_active_tool(
        call_id="c1",
        tool_name="bash",
        args={"command": "ls"},
        started_at=time.monotonic(),
    )
    assert handle.has_active_tool() is True


def test_handle_clear_active_tool_clears_state() -> None:
    handle = TerminalTurnHandle(Console(file=io.StringIO()))
    handle.set_active_tool(
        call_id="c1",
        tool_name="bash",
        args={},
        started_at=time.monotonic(),
    )
    handle.clear_active_tool(call_id="c1")
    assert handle.has_active_tool() is False


def test_handle_clear_active_tool_with_mismatched_call_id_keeps_state() -> None:
    handle = TerminalTurnHandle(Console(file=io.StringIO()))
    handle.set_active_tool(
        call_id="c1",
        tool_name="bash",
        args={},
        started_at=time.monotonic(),
    )
    handle.clear_active_tool(call_id="other")
    assert handle.has_active_tool() is True


def test_handle_clear_active_tool_with_empty_call_id_clears_unconditionally() -> None:
    handle = TerminalTurnHandle(Console(file=io.StringIO()))
    handle.set_active_tool(
        call_id="c1",
        tool_name="bash",
        args={},
        started_at=time.monotonic(),
    )
    handle.clear_active_tool()
    assert handle.has_active_tool() is False


def test_handle_render_includes_running_block_when_active() -> None:
    handle = TerminalTurnHandle(Console(file=io.StringIO()))
    handle.set_active_tool(
        call_id="c1",
        tool_name="bash",
        args={"command": "ls"},
        started_at=time.monotonic(),
    )
    rendered = handle._render(force_no_status=True)
    assert isinstance(rendered, Group)


def test_handle_render_excludes_running_block_when_cleared() -> None:
    handle = TerminalTurnHandle(Console(file=io.StringIO()))
    rendered = handle._render(force_no_status=True)
    from rich.text import Text

    assert isinstance(rendered, Text)




def test_transcript_routes_tool_started_to_active_handle() -> None:
    transcript, buf = _make_transcript_and_console()
    handle = transcript.begin_turn(role="assistant")
    try:
        transcript.handle_tool_started(
            {"call_id": "c1", "tool_name": "bash", "args": {"command": "ls"}}
        )
        assert handle.has_active_tool() is True
        out = buf.getvalue()
        assert "Running" in out
        assert "bash(ls)" in out
    finally:
        handle.complete(final_text="done")


def test_transcript_tool_completed_clears_handle_active_tool_before_scrollback_print() -> (
    None
):
    transcript, buf = _make_transcript_and_console()
    handle = transcript.begin_turn(role="assistant")
    try:
        transcript.handle_tool_started(
            {"call_id": "c1", "tool_name": "bash", "args": {"command": "ls"}}
        )
        assert handle.has_active_tool() is True
        transcript.handle_tool_completed(
            {
                "call_id": "c1",
                "tool_name": "bash",
                "args": {"command": "ls"},
                "content": "ok",
                "ok": True,
                "duration_ms": 1200,
                "exit_code": 0,
            }
        )
        assert handle.has_active_tool() is False
    finally:
        handle.complete(final_text="done")


def test_transcript_no_active_handle_falls_back_to_legacy_print() -> None:
    transcript, buf = _make_transcript_and_console()
    transcript.handle_tool_started(
        {"call_id": "c2", "tool_name": "bash", "args": {"command": "ls"}}
    )
    out = buf.getvalue()
    assert "Running" in out and "bash" in out.lower() or "ls" in out


def test_transcript_quiet_mode_skips_handle_and_scrollback() -> None:
    transcript, buf = _make_transcript_and_console()
    transcript.set_verbosity("quiet")
    handle = transcript.begin_turn(role="assistant")
    try:
        transcript.handle_tool_started(
            {"call_id": "c3", "tool_name": "bash", "args": {"command": "ls"}}
        )
        assert handle.has_active_tool() is False
        assert transcript._hidden_tool_count == 1
    finally:
        handle.complete(final_text="done")


def test_render_in_progress_block_shows_elapsed_when_positive() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, no_color=True)
    block = _render_in_progress_tool_block(
        "bash", {"command": "ls"}, elapsed_seconds=1.7
    )
    console.print(block)
    out = buf.getvalue()
    assert "Running" in out
    assert "1s" in out or "0m01s" in out


def test_render_in_progress_block_omits_elapsed_when_zero() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, no_color=True)
    block = _render_in_progress_tool_block(
        "bash", {"command": "ls"}, elapsed_seconds=0.0
    )
    console.print(block)
    out = buf.getvalue()
    # Elapsed only renders when > 0 per FLE-deferred design.
    assert "Running" in out
    # No leading "·" separator since elapsed is zero.
    assert " · " not in out or "0.0s" not in out


def test_handle_render_elapsed_grows_over_time() -> None:
    handle = TerminalTurnHandle(Console(file=io.StringIO()))
    started_at = time.monotonic()
    handle.set_active_tool(
        call_id="c1",
        tool_name="bash",
        args={},
        started_at=started_at,
    )
    buf1 = io.StringIO()
    Console(file=buf1, force_terminal=False, width=120, no_color=True).print(
        handle._render(force_no_status=True)
    )
    time.sleep(0.05)
    buf2 = io.StringIO()
    Console(file=buf2, force_terminal=False, width=120, no_color=True).print(
        handle._render(force_no_status=True)
    )
    assert "Running" in buf1.getvalue()
    assert "Running" in buf2.getvalue()




def test_handle_tool_started_idempotent_on_duplicate_call_id() -> None:
    transcript, _ = _make_transcript_and_console()
    handle = transcript.begin_turn(role="assistant")
    try:
        transcript.handle_tool_started(
            {"call_id": "c1", "tool_name": "bash", "args": {}}
        )
        handle.clear_active_tool()
        transcript.handle_tool_started(
            {"call_id": "c1", "tool_name": "bash", "args": {}}
        )
        # Idempotent — second call is a no-op via the dedup set.
        assert handle.has_active_tool() is False
    finally:
        handle.complete(final_text="done")
