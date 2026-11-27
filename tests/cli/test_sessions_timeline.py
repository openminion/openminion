from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.base.config.env import EnvironmentConfig
from openminion.cli.commands import sessions as sessions_command
from openminion.cli.commands.sessions import run_sessions_list, run_sessions_timeline
from openminion.cli.parser.base import build_parser
from openminion.modules.storage import build_runtime_storage
from openminion.modules.storage.runtime.session_store import (
    EventRecord,
    MessageRecord,
)

TS = "2026-03-22T10:00:00+00:00"


def _message(
    role: str,
    body: str,
    *,
    metadata: dict[str, Any] | None = None,
    message_id: str = "msg-1",
    rowid: int = 1,
    created_at: str = TS,
) -> MessageRecord:
    return MessageRecord(
        id=message_id,
        session_id="sess-1",
        conversation_id="",
        thread_id="",
        attach_id="",
        role=role,
        body=body,
        metadata=dict(metadata or {}),
        created_at=created_at,
        rowid=rowid,
    )


def _event(
    event_id: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    created_at: str = TS,
) -> EventRecord:
    return EventRecord(
        id=event_id,
        session_id="sess-1",
        event_type=event_type,
        payload=dict(payload or {}),
        created_at=created_at,
    )


def _args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "session_id": "sess-1",
        "limit": 500,
        "output_json": False,
        "config": None,
        "home_root": None,
        "data_root": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _runtime(
    messages: list[MessageRecord] | None = None,
    events: list[EventRecord] | None = None,
    *,
    exists: bool = True,
    read_error: Exception | None = None,
) -> MagicMock:
    runtime = MagicMock()
    runtime.sessions.get_session.return_value = MagicMock() if exists else None
    runtime.sessions.count_events.return_value = 0
    runtime.sessions.list_recent_messages.return_value = list(messages or [])
    runtime.sessions.list_events.return_value = list(events or [])
    if read_error is not None:
        runtime.sessions.list_recent_messages.side_effect = read_error
    runtime.close.return_value = None
    return runtime


def _run(runtime: MagicMock, args: SimpleNamespace) -> int:
    with patch.object(
        sessions_command,
        "_load_session_storage",
        return_value=runtime,
    ):
        return run_sessions_timeline(args)


def test_timeline_renders_messages_and_tools(capsys) -> None:
    runtime = _runtime(
        [
            _message("inbound", "hello"),
            _message(
                "outbound",
                "done",
                metadata={
                    "tool_results": [
                        {"tool_name": "probe_tool", "ok": True, "content": "ok"}
                    ]
                },
                message_id="msg-2",
                rowid=2,
            ),
        ]
    )

    assert _run(runtime, _args()) == 0

    out = capsys.readouterr().out
    assert "Session: sess-1" in out
    assert "Events: 3" in out
    assert "message   user: hello" in out
    assert "tool      tool probe_tool ok" in out
    assert "message   assistant: done" in out
    assert out.index("tool      tool probe_tool") < out.index("assistant: done")
    runtime.sessions.list_recent_messages.assert_called_once_with(
        session_id="sess-1", limit=500
    )
    runtime.sessions.list_events.assert_called_once_with(
        session_id="sess-1", limit=500, newest_first=True
    )
    runtime.close.assert_called_once_with()


def test_timeline_reports_unknown_session(capsys) -> None:
    runtime = _runtime(exists=False)

    assert _run(runtime, _args()) == 1

    assert "unknown session 'sess-1'" in capsys.readouterr().err
    runtime.sessions.list_recent_messages.assert_not_called()
    runtime.sessions.list_events.assert_not_called()
    runtime.close.assert_called_once_with()


def test_timeline_empty_session_succeeds(capsys) -> None:
    runtime = _runtime()

    assert _run(runtime, _args()) == 0

    assert "No events for session 'sess-1'." in capsys.readouterr().out


def test_timeline_json_payload_has_no_raw_metadata(capsys) -> None:
    runtime = _runtime(
        [
            _message(
                "outbound",
                "hello",
                metadata={
                    "api_key": "sk-live-secret",
                    "tool_results": [
                        {"tool_name": "probe_tool", "ok": True, "content": "ok"}
                    ],
                },
            )
        ]
    )

    assert _run(runtime, _args(output_json=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["session_id"] == "sess-1"
    assert payload["event_count"] == 2
    assert payload["shown_count"] == 2
    encoded = json.dumps(payload)
    assert "sk-live-secret" not in encoded
    assert "api_key" not in encoded
    assert "tool_results" not in encoded


def test_timeline_text_omits_raw_metadata(capsys) -> None:
    runtime = _runtime(
        [
            _message(
                "inbound",
                "hello",
                metadata={"api_key": "sk-live-secret"},
            )
        ]
    )

    assert _run(runtime, _args()) == 0

    assert "sk-live-secret" not in capsys.readouterr().out


def test_timeline_shows_only_the_latest_limit(capsys) -> None:
    runtime = _runtime(
        [
            _message("inbound", "one", message_id="m1"),
            _message("inbound", "two", message_id="m2", rowid=2),
            _message("inbound", "three", message_id="m3", rowid=3),
        ]
    )

    assert _run(runtime, _args(limit=1)) == 0

    out = capsys.readouterr().out
    assert "Events: 3 (latest 1)" in out
    assert "three" in out
    assert "one" not in out


def test_timeline_limit_is_clamped(capsys) -> None:
    runtime = _runtime([_message("inbound", "hello")])

    assert _run(runtime, _args(limit=0)) == 0
    assert _run(runtime, _args(limit=-5)) == 0
    assert _run(runtime, _args(limit=9999)) == 0

    limits = [
        call.kwargs["limit"]
        for call in runtime.sessions.list_recent_messages.call_args_list
    ]
    assert limits == [1, 1, 5000]
    assert capsys.readouterr().err == ""


def test_parser_rejects_a_nonnumeric_limit() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["sessions", "timeline", "sess-1", "--limit", "bogus"])


def test_timeline_missing_session_id_exits_two(capsys) -> None:
    runtime = _runtime()

    assert _run(runtime, _args(session_id="")) == 2
    assert _run(runtime, _args(session_id=None)) == 2

    assert "missing session id" in capsys.readouterr().err
    runtime.sessions.get_session.assert_not_called()


def test_timeline_storage_startup_error_exits_one(capsys) -> None:
    with patch.object(
        sessions_command,
        "_load_session_storage",
        side_effect=RuntimeError("bad storage"),
    ):
        assert run_sessions_timeline(_args()) == 1

    assert "bad storage" in capsys.readouterr().err


def test_timeline_storage_read_error_exits_one(capsys) -> None:
    runtime = _runtime(read_error=RuntimeError("boom"))

    assert _run(runtime, _args()) == 1

    assert "could not read timeline: boom" in capsys.readouterr().err
    runtime.close.assert_called_once_with()


def test_timeline_storage_read_error_catches_sqlite_database_error(capsys) -> None:
    runtime = _runtime(read_error=sqlite3.DatabaseError("boom"))

    assert _run(runtime, _args()) == 1

    assert "could not read timeline" in capsys.readouterr().err


def test_parser_accepts_the_session_alias_for_the_whole_group() -> None:
    parser = build_parser()

    for name in ("sessions", "session"):
        args = parser.parse_args([name, "timeline", "sess-9", "--limit", "5", "--json"])
        assert args.command == name
        assert args.sessions_command == "timeline"
        assert args.session_id == "sess-9"
        assert args.limit == 5
        assert args.output_json is True
        assert args.handler is run_sessions_timeline

    assert parser.parse_args(["session", "list"]).handler is run_sessions_list
    defaults = parser.parse_args(["sessions", "timeline", "sess-9"])
    assert defaults.limit == 500
    assert defaults.output_json is False


def test_timeline_reads_the_configured_messages_and_events(tmp_path, capsys) -> None:
    data_root = tmp_path / ".openminion"
    config = OpenMinionConfig()
    config.storage.path = str(data_root / "state" / "openminion.db")
    env = EnvironmentConfig(
        values={
            "OPENMINION_HOME": str(tmp_path),
            "OPENMINION_DATA_ROOT": str(data_root),
        }
    )
    storage = build_runtime_storage(
        config.storage.path,
        env=env,
        record_backend=config.storage.record_backend(),
        record_backend_options=config.storage.record_backend_options(),
    )
    try:
        session = storage.sessions.resolve_session(
            agent_id="agent",
            channel="cli",
            target="tests",
            session_id="sess-real",
        )
        storage.sessions.append_message(
            session_id=session.id,
            role="inbound",
            body="hello from store",
        )
        storage.sessions.append_event(
            session_id=session.id,
            event_type="run.waiting_tool",
            payload={"run_id": "run-real"},
        )
        storage.sessions.append_message(
            session_id=session.id,
            role="outbound",
            body="done from store",
            metadata={
                "run_id": "run-real",
                "tool_results": json.dumps(
                    [{"tool_name": "probe_tool", "ok": True, "content": "ok"}]
                ),
            },
        )
    finally:
        storage.close()

    reopened = build_runtime_storage(
        config.storage.path,
        env=env,
        record_backend=config.storage.record_backend(),
        record_backend_options=config.storage.record_backend_options(),
    )
    try:
        assert _run(reopened, _args(session_id="sess-real")) == 0
    finally:
        reopened.close()

    out = capsys.readouterr().out
    assert "Session: sess-real" in out
    assert "message   user: hello from store" in out
    assert "tool      tool probe_tool ok" in out
    assert "message   assistant: done from store" in out
    assert out.index("hello from store") < out.index("tool probe_tool")
    assert out.index("tool probe_tool") < out.index("done from store")


def test_timeline_unknown_session_escapes_control_characters(capsys) -> None:
    runtime = _runtime(exists=False)

    assert _run(runtime, _args(session_id="\x1b[31mevil")) == 1
    err = capsys.readouterr().err
    assert "\x1b" not in err
    assert "\\x1b" in err
