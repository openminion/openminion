from __future__ import annotations

import asyncio
import io
import os
from contextlib import redirect_stdout

import pytest
from rich.console import Console

from openminion.cli.status import TokenUsageSnapshot
from openminion.cli.tui.terminal import shell as terminal_shell
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


class _BlockingRuntime:
    def __init__(self) -> None:
        self.partial_sent = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def send_message(self, text, *, progress_callback=None):
        del text, progress_callback
        try:
            yield "partial reply"
            self.partial_sent.set()
            await self.release.wait()
            yield "should not render"
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class _ApprovalRuntime:
    def __init__(self) -> None:
        self.callback_seen = False
        self.approved = False

    async def send_message(
        self, text, *, progress_callback=None, approval_callback=None
    ):
        del text, progress_callback
        self.callback_seen = approval_callback is not None
        if approval_callback is not None:
            self.approved = bool(
                await approval_callback("file.write", {"path": "scratch.txt"}, "call-1")
            )
        yield "approval ok"
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


def test_agent_turn_passes_terminal_approval_callback() -> None:
    transcript, buf = _make_transcript()
    runtime = _ApprovalRuntime()

    async def approve(*args):
        del args
        return True

    asyncio.run(
        _run_agent_turn(
            text="write file",
            runtime=runtime,
            transcript=transcript,
            status_line=None,
            approval_callback=approve,
        )
    )

    assert runtime.callback_seen
    assert runtime.approved
    assert "approval ok" in buf.getvalue()


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


def test_escape_interrupt_cancels_terminal_turn_and_preserves_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _BlockingRuntime()
    transcript, _ = _make_transcript()
    cleanup_calls: list[str] = []

    def _fake_watcher(turn_task: asyncio.Task[None]):
        async def _cancel_after_partial() -> None:
            await runtime.partial_sent.wait()
            turn_task.cancel()

        asyncio.create_task(_cancel_after_partial())
        return terminal_shell._EscapeInterruptWatcher(
            stop=lambda: cleanup_calls.append("stop"),
            interrupted=lambda: True,
        )

    monkeypatch.setattr(
        terminal_shell,
        "_start_escape_interrupt_watcher",
        _fake_watcher,
    )

    asyncio.run(
        terminal_shell._run_interruptible_agent_turn(
            text="please run",
            runtime=runtime,
            transcript=transcript,
            status_line=None,
        )
    )

    agents = [m for m in transcript._messages if m.kind == MessageKind.AGENT]
    systems = [m for m in transcript._messages if m.kind == MessageKind.SYSTEM]
    assert runtime.cancelled is True
    assert cleanup_calls == ["stop"]
    assert agents
    assert agents[-1].body == "partial reply"
    assert systems
    assert systems[-1].body == "Interrupted current turn."


@pytest.mark.skipif(
    not callable(getattr(os, "openpty", None)) or terminal_shell.termios is None,
    reason="terminal Escape watcher requires a POSIX pseudo-terminal",
)
def test_escape_interrupt_watcher_cancels_task_from_tty_byte() -> None:
    async def _run() -> None:
        async def _never_finishes() -> None:
            await asyncio.Future()

        turn_task = asyncio.create_task(_never_finishes())
        master_fd, slave_fd = os.openpty()
        watcher = None
        with os.fdopen(slave_fd, "rb", buffering=0) as slave:
            try:
                watcher = terminal_shell._start_escape_interrupt_watcher(
                    turn_task,
                    stdin=slave,
                )
                assert watcher is not None
                os.write(master_fd, b"\x1b")
                with pytest.raises(asyncio.CancelledError):
                    await turn_task
                assert watcher.interrupted() is True
            finally:
                if watcher is not None:
                    watcher.stop()
                os.close(master_fd)

    asyncio.run(_run())


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


def test_progress_callback_keeps_plain_output_free_of_status_echo() -> None:
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
    assert "\x1b[" not in output
    assert "progress ok" in output
    assert "Loading session history..." not in output
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
            status_line=None,
        )
    )

    assert labels
    assert labels[0] == "Working..."
    assert any("Loading session history" in label for label in labels)


def test_progress_callback_updates_prompt_toolbar_status_during_interactive_turn() -> (
    None
):
    transcript, _ = _make_transcript()
    runtime = _ProgressRuntime()
    status_line = TerminalStatusLine()

    asyncio.run(
        _run_agent_turn(
            text="hi",
            runtime=runtime,
            transcript=transcript,
            status_line=status_line,
        )
    )

    assert status_line.state == "idle"
    assert "total 1m 22s" in status_line.usage_summary
