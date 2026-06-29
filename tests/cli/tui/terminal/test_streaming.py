from __future__ import annotations

import io
import time

from rich.console import Console

from openminion.cli.tui.terminal.streaming import (
    _BOUNDED_FALLBACK_THRESHOLD_S,
    TerminalTurnHandle,
)
from openminion.cli.tui.presentation.models import ToolEvent


def _make_console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=80)
    return console, buffer


def test_handle_implements_protocol() -> None:
    from openminion.cli.tui.presentation.contracts import TurnHandleProtocol

    console, _ = _make_console()
    h = TerminalTurnHandle(console)
    assert isinstance(h, TurnHandleProtocol)


def test_append_token_builds_body_incrementally() -> None:
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle.append_token("Hello")
    handle.append_token(", ")
    handle.append_token("world!")
    handle.complete()
    output = buffer.getvalue()
    # The final body must be in the captured output (transient=False).
    assert "Hello, world!" in output


def test_complete_with_explicit_final_text() -> None:
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle.append_token("partial")
    handle.complete(final_text="full body")
    output = buffer.getvalue()
    assert "full body" in output


def test_complete_shows_muted_whole_second_response_time() -> None:
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle._started_at = time.monotonic() - 3.4
    handle.append_token("timed reply")
    handle.complete()
    output = buffer.getvalue()
    assert "Done in 3s" in output
    assert output.endswith("Done in 3s\n\n")
    assert "3.4s" not in output


def test_complete_can_hide_response_time() -> None:
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console, show_response_time=False).start()
    handle._started_at = time.monotonic() - 3.4
    handle.append_token("untimed reply")
    handle.complete()
    assert "Done in" not in buffer.getvalue()


def test_bounded_fallback_under_50ms_threshold() -> None:
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle.append_token("instant reply")
    handle.complete()
    output = buffer.getvalue()
    assert "instant reply" in output
    # No cursor in the final output (the final render is force_no_cursor).
    assert "▍" not in output


def test_streaming_above_50ms_still_finalizes_without_cursor() -> None:
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle.append_token("slow ")
    time.sleep(_BOUNDED_FALLBACK_THRESHOLD_S * 1.5)
    handle.append_token("stream")
    handle.complete()
    output = buffer.getvalue()
    assert "slow stream" in output
    # Final committed text never carries the cursor.
    final_lines = output.rstrip().splitlines()[-3:]
    assert not any("▍" in line for line in final_lines)


def test_append_tool_block_renders_inline() -> None:
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle.append_token("before tool")
    handle.append_tool_block(
        ToolEvent(
            tool_name="bash",
            args={"cmd": "ls"},
            content="file1\nfile2",
            full_content="file1\nfile2",
            exit_code=0,
        )
    )
    handle.append_token("after tool")
    handle.complete()
    output = buffer.getvalue()
    assert "bash" in output
    assert "└ file1" in output
    assert "file1" in output
    assert "after tool" in output


def test_complete_is_idempotent() -> None:
    console, _ = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle.complete(final_text="once")
    handle.complete(final_text="twice")  # should be a no-op
