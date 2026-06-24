from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass

from rich.console import Console

from openminion.cli.tui.terminal.shell import _SLASH_COMMANDS, _handle_slash
from openminion.cli.tui.terminal.status_line import TerminalStatusLine
from openminion.cli.tui.terminal.transcript import TerminalTranscript
from openminion.cli.tui.presentation.models import ChatMessage, MessageKind, ToolEvent


@dataclass
class _SessionRecord:
    id: str
    label: str
    message_count: int = 0


class _FakeRuntime:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.bound: list[str] = []
        self._next_session_id = "focus-new-001"
        self._directory_sessions = [
            _SessionRecord(id="focus-empty", label="focus-empty", message_count=0),
            _SessionRecord(id="focus-live", label="focus-live", message_count=3),
        ]
        self._history = [
            ChatMessage(kind=MessageKind.USER, sender="you", body="earlier"),
            ChatMessage(kind=MessageKind.AGENT, sender="agent", body="done"),
        ]

    def create_new_session(self) -> str:
        self.created.append(self._next_session_id)
        return self._next_session_id

    def list_directory_sessions(self, *, limit: int = 50):
        return list(self._directory_sessions[:limit])

    def bind_session(self, session_id: str) -> None:
        self.bound.append(session_id)

    def get_current_history(self):
        return list(self._history)


class _StubOverlay:
    def __init__(self, chosen: str | None) -> None:
        self._chosen = chosen
        self.presented: list[list[str]] = []

    def present_resume_picker(self, sessions):
        self.presented.append([str(getattr(item, "id", "")) for item in sessions])
        return self._chosen


def _make_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    return console, buf


async def _dispatch(
    text: str,
    *,
    runtime: _FakeRuntime,
    overlay: _StubOverlay,
    transcript: TerminalTranscript | None = None,
):
    console, buf = _make_console()
    transcript = transcript or TerminalTranscript(console)
    result = await _handle_slash(
        text,
        runtime=runtime,
        console=console,
        transcript=transcript,
        overlay=overlay,  # type: ignore[arg-type]
        status_line=TerminalStatusLine(),
        working_dir="/tmp/project",
    )
    return transcript, buf.getvalue(), result


def test_new_and_resume_added_to_catalog() -> None:
    assert "/new" in _SLASH_COMMANDS
    assert "/resume" in _SLASH_COMMANDS


def test_new_starts_session_and_clears_transcript() -> None:
    runtime = _FakeRuntime()
    overlay = _StubOverlay(None)
    console, _ = _make_console()
    transcript = TerminalTranscript(console)
    transcript.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body="x"))
    transcript._truncated_blocks = [
        ToolEvent(tool_name="bash", args={}, content="x", call_id="call-1")
    ]
    transcript._live_narrated_call_ids = {"call-1"}

    transcript, out, should_exit = asyncio.run(
        _dispatch("/new", runtime=runtime, overlay=overlay, transcript=transcript)
    )

    assert should_exit is False
    assert runtime.created == ["focus-new-001"]
    assert transcript._messages == []
    assert transcript._truncated_blocks == []
    assert transcript._live_narrated_call_ids == set()
    assert "started new session" in out


def test_resume_filters_to_non_empty_sessions_and_reloads_history() -> None:
    runtime = _FakeRuntime()
    overlay = _StubOverlay("focus-live")

    transcript, out, should_exit = asyncio.run(
        _dispatch("/resume", runtime=runtime, overlay=overlay)
    )

    assert should_exit is False
    assert overlay.presented == [["focus-live"]]
    assert runtime.bound == ["focus-live"]
    assert [msg.body for msg in transcript._messages] == ["earlier", "done"]
    assert "resumed session: focus-live" in out


def test_resume_with_no_non_empty_sessions_surfaces_guidance() -> None:
    runtime = _FakeRuntime()
    runtime._directory_sessions = [
        _SessionRecord(id="focus-empty", label="empty", message_count=0)
    ]
    overlay = _StubOverlay("focus-empty")

    _, out, _ = asyncio.run(_dispatch("/resume", runtime=runtime, overlay=overlay))

    assert "no prior sessions with messages" in out.lower()
    assert overlay.presented == []
    assert runtime.bound == []


def test_resume_cancel_keeps_existing_state() -> None:
    runtime = _FakeRuntime()
    overlay = _StubOverlay(None)
    console, _ = _make_console()
    transcript = TerminalTranscript(console)
    transcript.push_message(
        ChatMessage(kind=MessageKind.USER, sender="you", body="keep")
    )

    transcript, out, _ = asyncio.run(
        _dispatch("/resume", runtime=runtime, overlay=overlay, transcript=transcript)
    )

    assert runtime.bound == []
    assert [msg.body for msg in transcript._messages] == ["keep"]
    assert "resumed session" not in out.lower()
