from __future__ import annotations

import io
import time

from rich.console import Console

from openminion.cli.interactive.terminal.spinner import THINKING_VERB
from openminion.cli.interactive.terminal.streaming import TerminalTurnHandle


def _make_console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=120)
    return console, buffer


def test_start_sets_thinking_frame_state() -> None:
    console, _ = _make_console()
    handle = TerminalTurnHandle(console).start()
    assert handle._in_thinking_frame is True
    handle.complete()


def test_first_append_token_flips_out_of_thinking() -> None:
    console, _ = _make_console()
    handle = TerminalTurnHandle(console).start()
    assert handle._in_thinking_frame is True
    handle.append_token("first chunk")
    assert handle._in_thinking_frame is False
    handle.complete()


def test_thinking_frame_renderable_uses_thinking_verb() -> None:
    console, _ = _make_console()
    handle = TerminalTurnHandle(console).start()
    renderable = handle._render()
    assert renderable is not None
    from rich.console import Group

    assert isinstance(renderable, Group)
    rows = list(renderable.renderables)
    assert len(rows) == 3
    status_row_text = rows[1].plain
    assert THINKING_VERB in status_row_text
    assert rows[2].plain == "Type to queue while the current turn runs"
    handle.complete()


def test_thinking_frame_can_show_runtime_status_label() -> None:
    console, _ = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle.set_status_label("Analyzing request...")
    renderable = handle._render()
    assert renderable is not None
    from rich.console import Group

    assert isinstance(renderable, Group)
    rows = list(renderable.renderables)
    status_row_text = rows[1].plain
    assert "Analyzing request..." in status_row_text
    assert THINKING_VERB not in status_row_text
    handle.complete()


def test_streaming_layout_after_first_token_uses_rotating_verb() -> None:
    console, _ = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle.append_token("body")
    assert handle._in_thinking_frame is False
    handle.complete()


def test_bounded_fallback_skips_thinking_frame_in_committed_render() -> None:
    console, buffer = _make_console()
    handle = TerminalTurnHandle(console).start()
    handle.append_token("instant")
    handle.complete()  # < 50 ms
    output = buffer.getvalue()
    assert "instant" in output
    assert "Thinking" not in output


def test_slow_turn_with_no_tokens_still_completes() -> None:
    console, _ = _make_console()
    handle = TerminalTurnHandle(console).start()
    time.sleep(0.1)  # > 50 ms; not bounded fallback
    handle.complete()  # no tokens received


def test_transition_does_not_emit_double_open() -> None:
    console, _ = _make_console()
    handle = TerminalTurnHandle(console).start()
    live_before = handle._live
    assert live_before is not None
    handle.append_token("token")
    live_after = handle._live
    assert live_after is live_before, (
        "FTR-03: transition from Thinking to streaming must reuse the same "
        "Live region — no double-open, no flash."
    )
    handle.complete()
