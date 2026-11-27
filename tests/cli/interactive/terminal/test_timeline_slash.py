from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from rich.console import Console

from openminion.cli.interactive.terminal.shell import _handle_slash
from openminion.cli.interactive.terminal.shell.slash_output import (
    PROMPT_SAFE_OUTPUT_SLASHES,
)
from openminion.cli.presentation.slash_commands import terminal_slash_commands
from openminion.modules.storage.runtime.session_store import (
    EventRecord,
    MessageRecord,
)

TS = "2026-03-22T10:00:00+00:00"


class _Runtime:
    def __init__(
        self,
        *,
        messages: dict[str, list[MessageRecord]] | None = None,
        events: dict[str, list[EventRecord]] | None = None,
        session_id: str = "current",
        existing: tuple[str, ...] = ("current",),
        list_error: Exception | None = None,
    ) -> None:
        self.session_id = session_id
        self._messages = messages or {}
        self._events = events or {}
        self._existing = set(existing)
        self._list_error = list_error
        self.calls: list[tuple[str, ...]] = []

    def session_exists(self, session_id: str) -> bool:
        self.calls.append(("exists", session_id))
        return session_id in self._existing

    def list_session_messages(
        self, session_id: str, *, limit: int = 500
    ) -> list[MessageRecord]:
        self.calls.append(("messages", session_id, str(limit)))
        if self._list_error is not None:
            raise self._list_error
        return list(self._messages.get(session_id, []))

    def list_session_events(
        self, session_id: str, *, limit: int = 500
    ) -> list[EventRecord]:
        self.calls.append(("events", session_id, str(limit)))
        if self._list_error is not None:
            raise self._list_error
        return list(self._events.get(session_id, []))


def _message(role: str, body: str, message_id: str = "m1") -> MessageRecord:
    return MessageRecord(
        id=message_id,
        session_id="current",
        conversation_id="",
        thread_id="",
        attach_id="",
        role=role,
        body=body,
        metadata={},
        created_at=TS,
        rowid=1,
    )


def _event(event_id: int, text: str) -> EventRecord:
    return EventRecord(
        id=event_id,
        session_id="current",
        event_type="agent.bound",
        payload={"agent_id": text},
        created_at=TS,
    )


def _dispatch(text: str, runtime: Any) -> str:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=160)
    asyncio.run(
        _handle_slash(
            text,
            runtime=runtime,
            console=console,
            transcript=MagicMock(),
            overlay=MagicMock(),
            status_line=MagicMock(),
            working_dir="/tmp",
        )
    )
    return buffer.getvalue()


def test_timeline_is_cataloged_and_prompt_safe() -> None:
    assert "/timeline" in terminal_slash_commands()
    assert "/timeline" in PROMPT_SAFE_OUTPUT_SLASHES


def test_timeline_uses_current_session_messages_and_events() -> None:
    runtime = _Runtime(
        messages={"current": [_message("inbound", "hello")]},
        events={"current": [_event(1, "a")]},
    )

    out = _dispatch("/timeline", runtime)

    assert "Session: current" in out
    assert "message   user: hello" in out
    assert "event     agent bound a" in out
    assert ("messages", "current", "500") in runtime.calls
    assert ("events", "current", "500") in runtime.calls


def test_timeline_uses_explicit_session() -> None:
    runtime = _Runtime(
        messages={"other": [_message("inbound", "other message")]},
        existing=("current", "other"),
    )

    out = _dispatch("/timeline other", runtime)

    assert "Session: other" in out
    assert "other message" in out
    assert ("messages", "other", "500") in runtime.calls
    assert ("events", "other", "500") in runtime.calls


def test_timeline_reports_unknown_session() -> None:
    runtime = _Runtime(
        messages={"other": [_message("inbound", "other message")]},
    )

    out = _dispatch("/timeline other", runtime)

    assert "(/timeline: unknown session 'other')" in out
    assert not [call for call in runtime.calls if call[0] == "messages"]


def test_timeline_requires_an_active_session() -> None:
    runtime = _Runtime(
        messages={"current": [_message("inbound", "hello")]},
        session_id="",
    )

    out = _dispatch("/timeline", runtime)

    assert "(/timeline: no active session)" in out


def test_timeline_rejects_extra_arguments() -> None:
    runtime = _Runtime(messages={"current": [_message("inbound", "hello")]})

    out = _dispatch("/timeline one two", runtime)

    assert "(usage: /timeline [<session-id>])" in out
    assert not [call for call in runtime.calls if call[0] == "messages"]


def test_timeline_reports_empty_session() -> None:
    runtime = _Runtime(existing=("current",))

    out = _dispatch("/timeline", runtime)

    assert "(no timeline entries for session 'current')" in out


def test_timeline_reports_storage_error() -> None:
    runtime = _Runtime(list_error=RuntimeError("boom"))

    out = _dispatch("/timeline", runtime)

    assert "(/timeline: could not read timeline: boom)" in out


def test_timeline_reports_missing_runtime_capability() -> None:
    out = _dispatch("/timeline", SimpleNamespace(session_id="current"))

    assert "(/timeline: runtime does not expose session messages and events)" in out


def test_timeline_prints_rich_message_brackets_literally() -> None:
    runtime = _Runtime(
        messages={"current": [_message("inbound", "[red]literal[/red]")]}
    )

    out = _dispatch("/timeline", runtime)

    assert "[red]literal[/red]" in out
    assert "literal" in out


def test_timeline_orders_messages_and_events_by_timestamp() -> None:
    runtime = _Runtime(
        messages={
            "current": [
                _message("inbound", "first", message_id="m1"),
                _message("inbound", "second", message_id="m2"),
            ]
        },
        events={"current": [_event(1, "third")]},
    )

    out = _dispatch("/timeline", runtime)

    assert out.index("first") < out.index("second") < out.index("agent bound third")


def test_timeline_shows_only_the_latest_500_entries() -> None:
    runtime = _Runtime(
        messages={
            "current": [
                _message("inbound", f"message-{index}", message_id=f"m{index}")
                for index in range(600)
            ]
        }
    )

    out = _dispatch("/timeline", runtime)

    assert "Events: 600 (latest 500)" in out
    assert "message-99" not in out
    assert "message-100" in out
    assert "message-599" in out


def test_timeline_escapes_control_characters_in_unknown_session() -> None:
    runtime = _Runtime(messages={"current": [_message("inbound", "hello")]})

    out = _dispatch("/timeline \x1b[31mevil", runtime)

    assert "\x1b" not in out
    assert "\\x1b" in out
