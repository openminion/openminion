from __future__ import annotations

import asyncio
import io
from contextlib import redirect_stdout

from rich.console import Console

from openminion.cli.status import TokenUsageSnapshot
from openminion.cli.tui.terminal.shell import _run_agent_turn
from openminion.cli.tui.terminal.status_line import TerminalStatusLine
from openminion.cli.tui.terminal.transcript import TerminalTranscript
from openminion.cli.tui.presentation.models import MessageKind


class _StreamingRuntime:
    def __init__(self, chunks, raise_after=None):
        self._chunks = list(chunks)
        self._raise_after = raise_after

    async def send_message(self, text, *, progress_callback=None):
        del text, progress_callback
        for i, chunk in enumerate(self._chunks):
            if self._raise_after is not None and i >= self._raise_after:
                raise RuntimeError("simulated mid-stream failure")
            yield chunk
            await asyncio.sleep(0)


class _TTYStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class _ProgressRuntime:
    def token_usage_snapshot(self) -> TokenUsageSnapshot:
        return TokenUsageSnapshot(
            turn_total_tokens=1500,
            session_total_tokens=1500,
            turn_elapsed_seconds=82.0,
            updated_at_monotonic=100.0,
        )

    async def send_message(self, text, *, progress_callback=None):
        del text
        if progress_callback is not None:
            progress_callback(
                {
                    "trace_id": "focus-terminal-progress",
                    "status_key": "analyzing",
                    "label": "Loading session history...",
                }
            )
        yield "progress ok"
        await asyncio.sleep(0)


def _make_transcript() -> tuple[TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    return TerminalTranscript(Console(file=buf, force_terminal=False, width=80)), buf


def test_five_chunk_stream_lands_in_order_in_scrollback() -> None:
    transcript, buf = _make_transcript()
    runtime = _StreamingRuntime(["Hel", "lo", ", ", "stream", "!"])
    asyncio.run(
        _run_agent_turn(
            text="hi",
            runtime=runtime,
            transcript=transcript,
            status_line=None,
        )
    )
    output = buf.getvalue()
    assert "Hello, stream!" in output
    # Final assistant message stored in transcript with the full body.
    agents = [m for m in transcript._messages if m.kind == MessageKind.AGENT]
    assert agents
    assert agents[-1].body == "Hello, stream!"


def test_single_chunk_takes_bounded_fallback() -> None:
    transcript, buf = _make_transcript()
    runtime = _StreamingRuntime(["one shot"])
    asyncio.run(
        _run_agent_turn(
            text="hi",
            runtime=runtime,
            transcript=transcript,
            status_line=None,
        )
    )
    output = buf.getvalue()
    assert "one shot" in output


def test_mid_stream_error_preserves_partial_and_emits_error() -> None:
    transcript, buf = _make_transcript()
    runtime = _StreamingRuntime(
        ["partial-1 ", "partial-2 ", "after-error"], raise_after=2
    )
    asyncio.run(
        _run_agent_turn(
            text="hi",
            runtime=runtime,
            transcript=transcript,
            status_line=None,
        )
    )
    # Partial body landed.
    agents = [m for m in transcript._messages if m.kind == MessageKind.AGENT]
    assert agents
    assert "partial-1" in agents[-1].body
    assert "partial-2" in agents[-1].body
    # Error message rendered.
    errors = [m for m in transcript._messages if m.kind == MessageKind.ERROR]
    assert errors
    assert "simulated mid-stream failure" in errors[-1].body


def test_empty_stream_drops_placeholder_message() -> None:
    transcript, _ = _make_transcript()
    runtime = _StreamingRuntime([])
    asyncio.run(
        _run_agent_turn(
            text="hi",
            runtime=runtime,
            transcript=transcript,
            status_line=None,
        )
    )
    agents = [m for m in transcript._messages if m.kind == MessageKind.AGENT]
    # Empty turn should drop the placeholder; no agent message remains.
    assert not agents or all(not m.body for m in agents)


def test_progress_callback_does_not_print_status_lines_before_reply() -> None:
    buf = _TTYStringIO()
    transcript = TerminalTranscript(Console(file=buf, force_terminal=False, width=80))
    runtime = _ProgressRuntime()
    status_line = TerminalStatusLine()

    with redirect_stdout(buf):
        asyncio.run(
            _run_agent_turn(
                text="hi",
                runtime=runtime,
                transcript=transcript,
                status_line=status_line,
            )
        )

    output = buf.getvalue()
    assert "Loading session history..." not in output
    assert "\x1b[" not in output
    assert "progress ok" in output
    assert "total 1m 22s" in status_line.usage_summary


def test_progress_callback_updates_live_turn_status_label() -> None:
    transcript, _ = _make_transcript()
    runtime = _ProgressRuntime()
    labels: list[str] = []
    original_begin_turn = transcript.begin_turn

    def _capturing_begin_turn(*args, **kwargs):
        handle = original_begin_turn(*args, **kwargs)
        original_set_status_label = handle.set_status_label

        def _capture(label: str) -> None:
            labels.append(label)
            original_set_status_label(label)

        handle.set_status_label = _capture  # type: ignore[method-assign]
        return handle

    transcript.begin_turn = _capturing_begin_turn  # type: ignore[method-assign]
    asyncio.run(
        _run_agent_turn(
            text="hi",
            runtime=runtime,
            transcript=transcript,
            status_line=TerminalStatusLine(),
        )
    )

    assert labels
    assert labels[0] == "Working..."
    assert any("Loading session history" in label for label in labels)
