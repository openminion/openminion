from __future__ import annotations

import asyncio
import io
import time

from rich.console import Console

from openminion.cli.tui.terminal.shell import _run_agent_turn
from openminion.cli.tui.terminal.status_line import TerminalStatusLine
from openminion.cli.tui.terminal.streaming import TerminalTurnHandle
from openminion.cli.tui.terminal.transcript import TerminalTranscript


class _SlowStreamingRuntime:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = list(chunks)
        self.captured_state_during_stream: list[str] = []
        self.captured_toolbar_during_stream: list[str] = []

    async def send_message(self, text, *, progress_callback=None, **kwargs):
        del text, kwargs
        for chunk in self._chunks:
            await asyncio.sleep(0.02)  # > 0 to let auto_refresh tick
            if progress_callback:
                progress_callback({"phase": "respond", "label": "Working..."})
            yield chunk


def _make_transcript() -> tuple[TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    return TerminalTranscript(Console(file=buf, force_terminal=False, width=120)), buf


def test_shell_does_not_route_active_turn_state_into_status_line() -> None:
    transcript, _ = _make_transcript()
    status_line = TerminalStatusLine()
    status_line.set_state(agent="alpha", model="openai/test", cwd="/tmp")

    runtime = _SlowStreamingRuntime(["hello ", "world"])

    state_history: list[str] = []
    original_set_state = status_line.set_state

    def _spy_set_state(**segments):
        if "state" in segments and segments["state"] is not None:
            state_history.append(str(segments["state"]))
        return original_set_state(**segments)

    status_line.set_state = _spy_set_state  # type: ignore[method-assign]

    asyncio.run(
        _run_agent_turn(
            text="hi",
            runtime=runtime,
            transcript=transcript,
            status_line=status_line,
        )
    )

    assert "responding" not in state_history, (
        "interactive terminal-flow should leave active-turn progress to the "
        "inline streaming handle instead of duplicating it through the shared "
        f"status line; got state history: {state_history}"
    )
    assert status_line.state == "idle"


def test_bottom_toolbar_contains_no_responding_text_during_idle_mode() -> None:
    line = TerminalStatusLine()
    line.set_state(agent="alpha", model="openai/test", cwd="/tmp", state="idle")
    text = line.bottom_toolbar()
    assert "responding" not in text
    assert "Esc cancel" not in text
    assert "alpha" in text
    assert "openai/test" in text


def test_bottom_toolbar_stays_identity_only_during_active_turns() -> None:
    line = TerminalStatusLine()
    line.set_state(
        state="responding",
        elapsed_seconds=5.0,
        agent="alpha",
        model="openai/test",
        cwd="/tmp",
    )
    text = line.bottom_toolbar()
    assert "responding" not in text
    assert "Esc cancel" not in text
    assert "alpha" in text
    assert "openai/test" in text


def test_live_turn_footer_omits_active_timer_and_hint() -> None:
    line = TerminalStatusLine()
    line.set_state(
        state="responding",
        elapsed_seconds=5.0,
        agent="alpha",
        model="openai/test",
        cwd="/tmp",
    )
    text = line.live_turn_footer()
    assert "responding" not in text
    assert "Esc cancel" not in text
    assert "5.0s" not in text
    assert "alpha" in text


def test_streaming_render_exposes_only_one_elapsed_counter() -> None:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=140)
    line = TerminalStatusLine()
    line.set_state(
        agent="minimax-m2-7",
        model="openai/MiniMax-M2.7",
        cwd="/repo/openminion",
        tokens="12.4k / 8k",
    )

    handle = TerminalTurnHandle(
        console,
        footer_provider=line.live_turn_footer,
    ).start()
    handle.set_status_label("responding")
    time.sleep(0.2)
    console.print(handle._render())
    handle.complete(final_text="hello")

    output = buffer.getvalue()
    assert output.count("0.2s") <= 1
    assert output.count("esc to interrupt") == 1
    assert output.count("responding") == 1


def test_streaming_output_carries_the_inline_spinner_verb() -> None:
    transcript, buf = _make_transcript()
    status_line = TerminalStatusLine()
    runtime = _SlowStreamingRuntime(["a", "b", "c"])
    asyncio.run(
        _run_agent_turn(
            text="hi",
            runtime=runtime,
            transcript=transcript,
            status_line=status_line,
        )
    )
    output = buf.getvalue()
    assert "abc" in output
