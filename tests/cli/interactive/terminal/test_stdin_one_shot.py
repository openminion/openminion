from __future__ import annotations

import asyncio
import io

from rich.console import Console

from openminion.cli.interactive.terminal.shell import _run_one_shot_stdin
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.cli.presentation.models import ChatMessage, MessageKind


class _FakeRuntime:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.received_text: str | None = None

    async def send_message(self, text, *, progress_callback=None):
        del progress_callback
        self.received_text = text
        yield self._reply


def test_stdin_one_shot_reads_runs_turn_returns_zero(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("summarize this prompt\n"))
    runtime = _FakeRuntime("here is the summary")
    buf = io.StringIO()
    transcript = TerminalTranscript(Console(file=buf, force_terminal=False, width=80))
    rc = asyncio.run(
        _run_one_shot_stdin(
            runtime=runtime,
            console=transcript._console,
            transcript=transcript,
            working_dir="/tmp/wd",
        )
    )
    assert rc == 0
    assert runtime.received_text == "summarize this prompt"
    output = buf.getvalue()
    assert "summarize this prompt" in output  # user echo
    assert "here is the summary" in output  # assistant body


def test_stdin_one_shot_empty_stdin_returns_one(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("   \n"))
    runtime = _FakeRuntime("unused")
    buf = io.StringIO()
    transcript = TerminalTranscript(Console(file=buf, force_terminal=False, width=80))
    rc = asyncio.run(
        _run_one_shot_stdin(
            runtime=runtime,
            console=transcript._console,
            transcript=transcript,
            working_dir="/tmp/wd",
        )
    )
    assert rc == 1
    output = buf.getvalue()
    assert "empty stdin" in output


def test_stdin_one_shot_runtime_error_returns_one(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("a question"))

    class _Boom:
        async def send_message(self, text, *, progress_callback=None):
            del text, progress_callback
            raise RuntimeError("provider unreachable")
            yield  # pragma: no cover — make this an async-gen

    runtime = _Boom()
    buf = io.StringIO()
    transcript = TerminalTranscript(Console(file=buf, force_terminal=False, width=80))
    rc = asyncio.run(
        _run_one_shot_stdin(
            runtime=runtime,
            console=transcript._console,
            transcript=transcript,
            working_dir="/tmp/wd",
        )
    )
    # _run_agent_turn catches the error and emits ERROR; the wrapper
    # returns 0 because the error was handled inline. Either return
    # code is acceptable as long as the error is surfaced.
    assert rc in (0, 1)
    errors = [m for m in transcript._messages if m.kind == MessageKind.ERROR]
    assert errors, "runtime error must surface as an inline ERROR message"


def test_transcript_can_record_without_rendering() -> None:
    buf = io.StringIO()
    transcript = TerminalTranscript(Console(file=buf, force_terminal=False, width=80))

    transcript.push_message(
        ChatMessage(kind=MessageKind.USER, sender="you", body="hello"),
        render=False,
    )

    assert transcript._messages[-1].body == "hello"
    assert buf.getvalue() == ""
