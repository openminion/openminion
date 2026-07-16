from __future__ import annotations

import io
import time

from rich.console import Console

from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.streaming import TerminalTurnHandle
from openminion.cli.presentation.models import ToolEvent


def _make_console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=120)
    return console, buffer


def test_streaming_renders_two_row_layout() -> None:
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle.append_token("hello ")
    time.sleep(0.06)  # > 50 ms threshold; commit to streaming render
    handle.append_token("world")
    handle.complete()
    output = buffer.getvalue()
    assert "⏺" in output
    assert "hello world" in output


def test_complete_strips_cursor_and_status_row() -> None:
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle.append_token("body text")
    handle.complete()
    output = buffer.getvalue()
    assert "▍" not in output
    final_lines = output.rstrip().splitlines()[-3:]
    assert not any("esc interrupts" in line for line in final_lines)


def test_plain_mode_drops_verb_keeps_elapsed_and_hint() -> None:
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console, plain=True).start()
    handle.append_token("plain")
    assert handle._spinner is not None
    assert handle._spinner.current_verb(time.monotonic() + 5.0) == ""
    handle.complete()


def test_bounded_fallback_under_50ms_renders_body_only() -> None:
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle.append_token("instant reply")
    handle.complete()  # well under 50 ms
    output = buffer.getvalue()
    assert "instant reply" in output


def test_streaming_above_50ms_keeps_marker_in_final_render() -> None:
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle.append_token("slower ")
    time.sleep(0.06)  # > 50 ms threshold
    handle.append_token("stream")
    handle.complete()
    output = buffer.getvalue()
    assert "slower stream" in output
    assert "⏺" in output


def test_cursor_present_during_streaming_dropped_on_complete() -> None:
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle.append_token("partial")
    handle.complete()
    output = buffer.getvalue()
    # Final committed text has no cursor.
    assert "▍" not in output


def test_wait_state_cadence_via_auto_refresh() -> None:
    console, _ = _make_console()
    handle = TerminalTurnHandle(console).start()
    start = handle._started_at
    time.sleep(0.3)
    elapsed_now = time.monotonic() - start
    label = handle._spinner.elapsed_label(handle._started_at + elapsed_now)
    assert label  # non-empty
    verb_at_3s = handle._spinner.current_verb(start + 3.0)
    verb_at_0s = handle._spinner.current_verb(start)
    assert verb_at_0s != verb_at_3s, (
        "verb must rotate over a 3 s gap (rotate_seconds=3.0 default)"
    )
    handle.complete()


def test_wait_state_refreshes_without_token_arrival() -> None:
    console, _ = _make_console()
    handle = TerminalTurnHandle(console)
    refresh_count = 0
    original_refresh = handle._refresh_live

    def _counting_refresh() -> None:
        nonlocal refresh_count
        refresh_count += 1
        original_refresh()

    handle._refresh_live = _counting_refresh  # type: ignore[method-assign]
    handle.start()
    deadline = time.monotonic() + 1.0
    while refresh_count < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    handle.complete()

    assert refresh_count >= 2


def test_wait_state_renders_footer_while_prompt_toolbar_is_suspended() -> None:
    line = TerminalStatusLine()
    line.set_state(
        agent="minimax-m2-7",
        model="openai/MiniMax-M2.7",
        cwd="/repo/openminion",
        tokens="1200/8000",
    )
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console, footer_provider=line.live_turn_footer).start()
    console.print(handle._render())
    handle.complete()

    output = buffer.getvalue()
    assert "minimax-m2-7" in output
    assert "openai/MiniMax-M2.7" in output
    assert "/repo/openminion" in output
    assert "1200/8000" in output


def test_wait_state_footer_parses_ansi_without_escape_garbage() -> None:
    line = TerminalStatusLine()
    line.set_state(
        agent="minimax-m2-7",
        model="openai/MiniMax-M2.7",
        cwd="/repo/openminion",
    )
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console, footer_provider=line.live_turn_footer).start()
    console.print(handle._render())
    handle.complete()

    output = buffer.getvalue()
    assert "\x1b[" not in output
    assert "[38;" not in output


def test_wait_state_suppresses_empty_assistant_row_before_first_token() -> None:
    line = TerminalStatusLine()
    line.set_state(
        agent="minimax-m2-7",
        model="openai/MiniMax-M2.7",
        cwd="/repo/openminion",
    )
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console, footer_provider=line.live_turn_footer).start()
    console.print(handle._render())
    handle.complete()

    output = buffer.getvalue()
    assert "⏺" not in output
    assert "▍" not in output
    assert "minimax-m2-7" in output
    assert "esc interrupts" in output
    assert "Type to queue while the current turn runs" in output
    assert "esc interrupts · type to queue" not in output


def test_pre_stream_thinking_frame_state() -> None:
    console, _ = _make_console()
    handle = TerminalTurnHandle(console).start()
    assert handle._in_thinking_frame is True
    handle.append_token("first")
    assert handle._in_thinking_frame is False
    handle.complete()


def test_append_tool_block_uses_new_renderer() -> None:
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console).start()
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "ls -la"},
        content="file1\nfile2",
        full_content="file1\nfile2",
        exit_code=0,
    )
    handle.append_tool_block(event)
    handle.complete()
    output = buffer.getvalue()
    assert "●" in output
    assert "Bash" in output
    assert "ls -la" in output
    assert "file1" in output


def test_complete_is_idempotent() -> None:
    console, _ = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle.complete(final_text="once")
    handle.complete(final_text="twice")  # no crash, no double print
