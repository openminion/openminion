from __future__ import annotations

import asyncio
import io
import time

from rich.console import Console

from openminion.cli.interactive.terminal.shell import _run_agent_turn
from openminion.cli.interactive.terminal.shell.progress import (
    tick_turn_status_line as _tick_turn_status_line,
)
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.streaming import TerminalTurnHandle
from openminion.cli.interactive.terminal.transcript import TerminalTranscript


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


def test_shell_routes_phase_status_to_footer_without_status_word_duplication() -> None:
    transcript, _ = _make_transcript()
    status_line = TerminalStatusLine()
    status_line.set_state(agent="alpha", model="openai/test", cwd="/tmp")

    runtime = _SlowStreamingRuntime(["hello ", "world"])

    state_history: list[str] = []
    turn_status_history: list[str] = []
    original_set_state = status_line.set_state

    def _spy_set_state(**segments):
        if "state" in segments and segments["state"] is not None:
            state_history.append(str(segments["state"]))
        if "turn_status" in segments and segments["turn_status"]:
            turn_status_history.append(str(segments["turn_status"]))
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

    assert "responding" in state_history
    assert any("Working" in label for label in turn_status_history)
    assert status_line.state == "idle"


def test_turn_status_ticker_refreshes_footer_elapsed_counter() -> None:
    line = TerminalStatusLine()
    line.set_state(state="responding", turn_status="Analyzing request...")
    invalidations = 0

    def _invalidate() -> None:
        nonlocal invalidations
        invalidations += 1

    async def _run() -> None:
        task = asyncio.create_task(
            _tick_turn_status_line(
                status_line=line,
                invalidate_prompt=_invalidate,
            )
        )
        try:
            await asyncio.sleep(1.05)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())

    assert line.elapsed_seconds >= 1.0
    assert invalidations >= 2
    assert "1s" not in line.live_turn_footer()
    assert "Status:" not in line.live_turn_footer()
    assert "1s" in line.active_status()


def test_turn_status_ticker_seeds_missing_label_so_footer_stays_visible() -> None:
    line = TerminalStatusLine()
    line.set_state(state="responding")
    invalidations = 0

    def _invalidate() -> None:
        nonlocal invalidations
        invalidations += 1

    async def _run() -> None:
        task = asyncio.create_task(
            _tick_turn_status_line(
                status_line=line,
                invalidate_prompt=_invalidate,
            )
        )
        try:
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())

    assert line.turn_status_label == "Working on it..."
    assert "Status: Working on it..." in line.active_status()
    assert invalidations >= 1


def test_bottom_toolbar_contains_no_responding_text_during_idle_mode() -> None:
    line = TerminalStatusLine()
    line.set_state(agent="alpha", model="openai/test", cwd="/tmp", state="idle")
    text = line.bottom_toolbar()
    assert "responding" not in text
    assert "Esc cancel" not in text
    assert "alpha" in text
    assert "openai/test" in text


def test_active_status_and_bottom_toolbar_stay_separate_during_turns() -> None:
    line = TerminalStatusLine()
    line.set_state(
        state="responding",
        elapsed_seconds=5.0,
        agent="alpha",
        model="openai/test",
        cwd="/tmp",
        turn_status="Analyzing request...",
    )
    status = line.active_status()
    footer = line.bottom_toolbar()
    assert "Status: Analyzing request..." in status
    assert "5s" in status
    assert "queue:" not in status
    assert "Status:" not in footer
    assert "responding" not in footer
    assert "Esc cancel" not in footer
    assert "5.0s" not in footer
    assert "alpha" in footer
    assert "openai/test" in footer


def test_live_turn_footer_omits_active_timer_and_hint() -> None:
    line = TerminalStatusLine()
    line.set_state(
        state="responding",
        elapsed_seconds=5.0,
        agent="alpha",
        model="openai/test",
        cwd="/tmp",
        turn_status="Analyzing request...",
    )
    text = line.live_turn_footer()
    rows = text.splitlines()
    assert len(rows) == 1
    assert "Status:" not in text
    assert "Analyzing request..." not in text
    assert "5s" not in text
    assert "status:" not in text
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
    assert output.count("esc interrupts") == 1
    assert output.count("Type to queue while the current turn runs") == 1
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
