from __future__ import annotations

import asyncio
import io

from rich.console import Console

from openminion.cli.tui.terminal.shell import _run_agent_turn
from openminion.cli.tui.terminal.status_line import TerminalStatusLine
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


def test_footer_stays_idle_during_active_turn() -> None:
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
        f"footer must not be pushed to 'responding' during the spinner's "
        f"lifetime; got state history: {state_history}"
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


def test_footer_responding_state_still_works_for_explicit_callers() -> None:
    line = TerminalStatusLine()
    line.set_state(state="responding", elapsed_seconds=5.0)
    text = line.bottom_toolbar()
    assert "responding" in text
    assert "Esc cancel" in text


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
