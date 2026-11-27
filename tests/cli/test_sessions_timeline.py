from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch
import sqlite3

from openminion.base.config import OpenMinionConfig
from openminion.base.config.env import EnvironmentConfig
from openminion.cli.commands import sessions as sessions_command
from openminion.cli.commands.sessions import run_sessions_timeline
from openminion.cli.parser.base import build_parser
from openminion.modules.storage import build_runtime_storage
from openminion.modules.storage.runtime.session_store import EventRecord


def _event(
    event_id: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> EventRecord:
    return EventRecord(
        id=event_id,
        session_id="sess-1",
        event_type=event_type,
        payload=dict(payload or {}),
        created_at="2026-03-22T10:00:00+00:00",
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
    events: list[EventRecord] | None = None,
    *,
    exists: bool = True,
    total: int | None = None,
    read_error: Exception | None = None,
) -> MagicMock:
    runtime = MagicMock()
    runtime.sessions.get_session.return_value = MagicMock() if exists else None
    runtime.sessions.count_events.return_value = (
        len(events or []) if total is None else total
    )
    if read_error is not None:
        runtime.sessions.count_events.side_effect = read_error
    runtime.sessions.list_events.return_value = list(events or [])
    runtime.close.return_value = None
    return runtime


def _run(runtime: MagicMock, args: SimpleNamespace) -> int:
    with patch.object(
        sessions_command,
        "_load_session_storage",
        return_value=runtime,
    ):
        return run_sessions_timeline(args)


def test_timeline_renders_known_session(capsys) -> None:
    runtime = _runtime(
        [
            _event(1, "turn.user", {"text": "hello"}),
            _event(2, "tool.call", {"tool_name": "shell", "status": "started"}),
        ],
        total=2,
    )

    assert _run(runtime, _args()) == 0

    out = capsys.readouterr().out
    assert "Session: sess-1" in out
    assert "Events: 2" in out
    assert "message   user: hello" in out
    assert "tool      tool shell started" in out
    runtime.sessions.list_events.assert_called_once_with(
        session_id="sess-1", limit=500, newest_first=True
    )
    runtime.close.assert_called_once_with()


def test_timeline_reports_unknown_session(capsys) -> None:
    runtime = _runtime(exists=False)

    assert _run(runtime, _args()) == 1

    assert "unknown session 'sess-1'" in capsys.readouterr().err
    runtime.sessions.count_events.assert_not_called()
    runtime.sessions.list_events.assert_not_called()
    runtime.close.assert_called_once_with()


def test_timeline_empty_session_succeeds(capsys) -> None:
    runtime = _runtime([], total=0)

    assert _run(runtime, _args()) == 0

    assert "No events for session 'sess-1'." in capsys.readouterr().out


def test_timeline_json_payload_has_no_raw_event_payload(capsys) -> None:
    runtime = _runtime(
        [
            _event(
                1,
                "turn.user",
                {"text": "hello", "api_key": "sk-live-secret"},
            )
        ],
        total=1,
    )

    assert _run(runtime, _args(output_json=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["session_id"] == "sess-1"
    assert payload["event_count"] == 1
    assert payload["shown_count"] == 1
    assert payload["entries"][0]["summary"] == "hello"
    assert "sk-live-secret" not in json.dumps(payload)


def test_timeline_text_omits_raw_event_payload(capsys) -> None:
    runtime = _runtime(
        [_event(1, "turn.user", {"text": "hello", "api_key": "sk-live-secret"})]
    )

    assert _run(runtime, _args()) == 0

    assert "sk-live-secret" not in capsys.readouterr().out


def test_timeline_header_reports_older_total(capsys) -> None:
    runtime = _runtime([_event(9, "turn.user", {"text": "latest"})], total=40)

    assert _run(runtime, _args(limit=1)) == 0

    out = capsys.readouterr().out
    assert "Events: 1 (latest 1 of 40)" in out
    assert "latest" in out


def test_timeline_limit_is_clamped(capsys) -> None:
    runtime = _runtime([_event(1, "turn.user", {"text": "hello"})])

    assert _run(runtime, _args(limit=0)) == 0
    assert _run(runtime, _args(limit=-5)) == 0
    assert _run(runtime, _args(limit=9999)) == 0
    assert _run(runtime, _args(limit=None)) == 0

    limits = [
        call.kwargs["limit"] for call in runtime.sessions.list_events.call_args_list
    ]
    assert limits == [1, 1, 5000, 500]


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

    assert "could not read events: boom" in capsys.readouterr().err
    runtime.close.assert_called_once_with()


def test_parser_accepts_canonical_and_alias_timeline_commands() -> None:
    parser = build_parser()

    for name in ("sessions", "session"):
        args = parser.parse_args([name, "timeline", "sess-9", "--limit", "5", "--json"])
        assert args.command == name
        assert args.sessions_command == "timeline"
        assert args.session_id == "sess-9"
        assert args.limit == 5
        assert args.output_json is True
        assert args.handler is run_sessions_timeline

    defaults = parser.parse_args(["sessions", "timeline", "sess-9"])
    assert defaults.limit == 500
    assert defaults.output_json is False


def test_timeline_reads_the_configured_session_store(tmp_path, capsys) -> None:
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
        storage.sessions.append_event(
            session_id=session.id,
            event_type="turn.user",
            payload={"text": "hello from store"},
        )
        storage.sessions.append_event(
            session_id=session.id,
            event_type="tool.call",
            payload={"tool_name": "shell", "status": "started"},
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
    assert "Events: 2" in out
    assert out.index("hello from store") < out.index("tool shell started")


def test_timeline_storage_read_error_catches_sqlite_database_error(capsys) -> None:
    runtime = _runtime(
        [_event(1, "turn.user", {"text": "hello"})],
        read_error=sqlite3.DatabaseError("boom"),
    )

    assert _run(runtime, _args()) == 1
    assert "could not read events" in capsys.readouterr().err


def test_timeline_non_numeric_limit_defaults_to_500(capsys) -> None:
    runtime = _runtime([_event(1, "turn.user", {"text": "hello"})])

    assert _run(runtime, _args(limit="bogus")) == 0
    assert runtime.sessions.list_events.call_args.kwargs["limit"] == 500


def test_timeline_unknown_session_escapes_control_characters(capsys) -> None:
    runtime = _runtime(exists=False)

    assert _run(runtime, _args(session_id="\x1b[31mevil")) == 1
    err = capsys.readouterr().err
    assert "\x1b" not in err
    assert "\\x1b" in err
