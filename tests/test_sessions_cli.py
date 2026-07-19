from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from openminion.cli.commands import sessions as sessions_command
from openminion.cli.commands.sessions import _build_rows, _age_label, _print_table
from openminion.modules.storage.runtime.session_store import (
    SessionRecord,
    build_session_key,
)


def _make_session(
    *,
    session_id: str = "sess-abc",
    agent_id: str = "default",
    channel: str = "cli",
    target: str = "tui",
    status: str = "active",
    name: str = "",
) -> SessionRecord:
    key = build_session_key(agent_id=agent_id, channel=channel, target=target)
    return SessionRecord(
        id=session_id,
        session_key=key,
        channel=channel,
        target=target,
        metadata={"name": name} if name else {},
        created_at="2026-03-22T10:00:00+00:00",
        updated_at="2026-03-22T10:00:00+00:00",
        status=status,
        last_activity_at="2026-03-22T10:00:00+00:00",
        closed_at=None,
        expires_at=None,
    )


def _make_runtime(sessions: list[SessionRecord]) -> MagicMock:
    runtime = MagicMock()
    runtime.sessions.list_sessions.return_value = sessions
    runtime.sessions.count_messages.return_value = 5
    runtime.close.return_value = None
    return runtime


# ── _build_rows unit tests ────────────────────────────────────────────────────


def test_build_rows_returns_all_sessions() -> None:
    sessions = [
        _make_session(session_id=f"sess-{i:03d}", agent_id="default") for i in range(3)
    ]
    runtime = _make_runtime(sessions)
    rows = _build_rows(runtime, agent_filter="", limit=10)
    assert len(rows) == 3


def test_build_rows_agent_filter() -> None:
    sessions = [
        _make_session(session_id="sess-001", agent_id="default"),
        _make_session(session_id="sess-002", agent_id="agent-02"),
        _make_session(session_id="sess-003", agent_id="agent-02"),
    ]
    runtime = _make_runtime(sessions)
    rows = _build_rows(runtime, agent_filter="agent-02", limit=10)
    assert len(rows) == 2
    assert all(r["agent"] == "agent-02" for r in rows)
    runtime.sessions.list_sessions.assert_called_once_with(
        limit=10,
        agent_id="agent-02",
        status=None,
        channel=None,
    )


def test_build_rows_limit_respected() -> None:
    sessions = [_make_session(session_id=f"sess-{i:03d}") for i in range(20)]
    runtime = _make_runtime(sessions)
    rows = _build_rows(runtime, agent_filter="", limit=5)
    assert len(rows) <= 5


def test_build_rows_includes_name_from_metadata() -> None:
    sessions = [_make_session(session_id="sess-named", name="My Work Session")]
    runtime = _make_runtime(sessions)
    rows = _build_rows(runtime, agent_filter="", limit=10)
    assert rows[0]["name"] == "My Work Session"


def test_build_rows_passes_status_and_channel_filters_to_runtime() -> None:
    sessions = [_make_session(session_id="sess-001", agent_id="default")]
    runtime = _make_runtime(sessions)
    rows = _build_rows(
        runtime,
        agent_filter="default",
        status_filter="closed",
        channel_filter="cli",
        limit=3,
    )
    assert len(rows) == 1
    runtime.sessions.list_sessions.assert_called_once_with(
        limit=3,
        agent_id="default",
        status="closed",
        channel="cli",
    )


def test_build_rows_row_fields() -> None:
    sessions = [_make_session()]
    runtime = _make_runtime(sessions)
    rows = _build_rows(runtime, agent_filter="", limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert "id" in row
    assert "name" in row
    assert "agent" in row
    assert "channel" in row
    assert "turns" in row
    assert "age" in row
    assert "status" in row


# ── _age_label tests ──────────────────────────────────────────────────────────


def test_age_label_invalid() -> None:
    assert _age_label("not-a-date") == "—"


# ── _print_table tests ────────────────────────────────────────────────────────


def test_print_table_no_sessions(capsys) -> None:
    _print_table([])
    out = capsys.readouterr().out
    assert "No sessions found" in out


def test_print_table_with_sessions(capsys) -> None:
    rows = [
        {
            "id": "sess-abc",
            "name": "Test",
            "agent": "default",
            "channel": "cli",
            "turns": 3,
            "age": "2h",
            "status": "active",
        }
    ]
    _print_table(rows)
    out = capsys.readouterr().out
    assert "sess-abc" in out
    assert "default" in out
    assert "Test" in out


# ── run_sessions_list integration via mock ────────────────────────────────────


def test_run_sessions_list_json_output(capsys) -> None:
    sessions = [_make_session(name="My Session")]
    runtime = _make_runtime(sessions)
    current = sys.modules.get("openminion.cli.commands.sessions", sessions_command)

    args = SimpleNamespace(
        agent=None,
        status=None,
        channel=None,
        limit=10,
        output_json=True,
        config=None,
        home_root=None,
        data_root=None,
    )

    with patch.object(current, "APIRuntime") as MockRuntime:
        MockRuntime.from_config_path.return_value = runtime
        result = current.run_sessions_list(args)

    assert result == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["name"] == "My Session"


def test_run_sessions_list_table_output(capsys) -> None:
    sessions = [_make_session()]
    runtime = _make_runtime(sessions)
    current = sys.modules.get("openminion.cli.commands.sessions", sessions_command)

    args = SimpleNamespace(
        agent=None,
        status=None,
        channel=None,
        limit=10,
        output_json=False,
        config=None,
        home_root=None,
        data_root=None,
    )

    with patch.object(current, "APIRuntime") as MockRuntime:
        MockRuntime.from_config_path.return_value = runtime
        result = current.run_sessions_list(args)

    assert result == 0
    out = capsys.readouterr().out
    assert "ID" in out
    assert "AGENT" in out


def test_run_sessions_list_startup_error(capsys) -> None:
    current = sys.modules.get("openminion.cli.commands.sessions", sessions_command)
    args = SimpleNamespace(
        agent=None,
        status=None,
        channel=None,
        limit=10,
        output_json=False,
        config="/nonexistent.json",
        home_root=None,
        data_root=None,
    )

    with patch.object(current, "APIRuntime") as MockRuntime:
        MockRuntime.from_config_path.side_effect = RuntimeError("bad config")
        result = current.run_sessions_list(args)

    assert result == 1
    err = capsys.readouterr().err
    assert "startup error" in err


def test_run_sessions_delete_yes_flag(capsys) -> None:
    runtime = MagicMock()
    runtime.sessions.delete_session.return_value = True
    runtime.close.return_value = None
    current = sys.modules.get("openminion.cli.commands.sessions", sessions_command)
    args = SimpleNamespace(
        session_id="sess-abc",
        yes=True,
        config=None,
        home_root=None,
        data_root=None,
    )

    with patch.object(current, "APIRuntime") as MockRuntime:
        MockRuntime.from_config_path.return_value = runtime
        result = current.run_sessions_delete(args)

    assert result == 0
    runtime.sessions.delete_session.assert_called_once_with("sess-abc")
    assert "Deleted session sess-abc" in capsys.readouterr().out


def test_run_sessions_delete_prompts_and_can_cancel(capsys) -> None:
    current = sys.modules.get("openminion.cli.commands.sessions", sessions_command)
    args = SimpleNamespace(
        session_id="sess-abc",
        yes=False,
        config=None,
        home_root=None,
        data_root=None,
    )

    with patch("builtins.input", return_value="n"):
        with patch.object(current, "APIRuntime") as MockRuntime:
            result = current.run_sessions_delete(args)

    assert result == 1
    MockRuntime.from_config_path.assert_not_called()
    assert "Cancelled." in capsys.readouterr().out


def test_run_sessions_delete_missing_session(capsys) -> None:
    runtime = MagicMock()
    runtime.sessions.delete_session.return_value = False
    runtime.close.return_value = None
    current = sys.modules.get("openminion.cli.commands.sessions", sessions_command)
    args = SimpleNamespace(
        session_id="missing",
        yes=True,
        config=None,
        home_root=None,
        data_root=None,
    )

    with patch.object(current, "APIRuntime") as MockRuntime:
        MockRuntime.from_config_path.return_value = runtime
        result = current.run_sessions_delete(args)

    assert result == 1
    assert "session not found" in capsys.readouterr().err


def test_session_cli_share_retention_and_branch_commands(tmp_path, capsys) -> None:
    from openminion.modules.session.cli import main

    db = tmp_path / "sessions.db"
    assert main(["create-session", "--db", str(db), "--session-id", "cli-src", "--title", "source"]) == 0
    assert main(["create-session", "--db", str(db), "--session-id", "cli-target", "--title", "target"]) == 0
    assert main(["update-summary", "--db", str(db), "--session-id", "cli-src", "--summary-short", "summary", "--based-on-seq", "1"]) == 0
    assert main(["share-create", "--db", str(db), "--session-id", "cli-src", "--ttl-seconds", "60"]) == 0
    output = capsys.readouterr().out
    assert '"token_return_policy": "returned_once_at_creation"' in output

    assert main(["share-list", "--db", str(db), "--session-id", "cli-src"]) == 0
    output = capsys.readouterr().out
    assert '"token"' not in output
    assert '"token_hint"' in output

    assert main(["branch-carry-forward", "--db", str(db), "--source-session-id", "cli-src", "--target-parent-session-id", "cli-target", "--fields-json", '["summary"]']) == 0
    output = capsys.readouterr().out
    assert '"schema_version": "session_branch_carry_forward.v1"' in output

    assert main(["retention-dry-run", "--db", str(db), "--inactivity-ttl-seconds", "999999999"]) == 0
    output = capsys.readouterr().out
    assert '"schema_version": "session_retention_plan.v1"' in output
