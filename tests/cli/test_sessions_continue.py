from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

from openminion.cli.commands.sessions import run_sessions_continue
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


class _Runtime:
    def __init__(self, store: SQLiteSessionStore) -> None:
        self.session_continuation_store = store
        self.config = SimpleNamespace(gateway=SimpleNamespace(host="127.0.0.1"))

    def close(self) -> None:
        pass


def _args(**updates) -> Namespace:
    values = {
        "source_session_id": "source",
        "target_session": None,
        "agent": "agent-a",
        "dry_run": False,
        "output_json": False,
        "expires_in_seconds": 86_400,
        "config": None,
        "home_root": None,
        "data_root": None,
    }
    values.update(updates)
    return Namespace(**values)


def _seed(store: SQLiteSessionStore) -> None:
    store.create_session(session_id="source", initial_agent_id="agent-a")
    store.put_working_state(
        "source",
        state_inline={"session_work_summary": "Continue from the verified step."},
    )


def test_cli_creates_target_and_applies(monkeypatch, tmp_path, capsys) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    _seed(store)
    runtime = _Runtime(store)
    monkeypatch.setattr(
        "openminion.cli.commands.sessions.APIRuntime.from_config_path",
        lambda *args, **kwargs: runtime,
    )

    assert run_sessions_continue(_args()) == 0

    output = capsys.readouterr().out
    assert "Continued source into" in output
    applied = store.get_events_by_parent_and_type(
        store.get_events("source", types=["session.continuation.packet_created"])[0][
            "event_id"
        ],
        "session.continuation.applied",
    )
    assert len(applied) == 1


def test_cli_dry_run_writes_nothing(monkeypatch, tmp_path, capsys) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    _seed(store)
    runtime = _Runtime(store)
    monkeypatch.setattr(
        "openminion.cli.commands.sessions.APIRuntime.from_config_path",
        lambda *args, **kwargs: runtime,
    )
    sessions_before = len(store.list_sessions())

    assert run_sessions_continue(_args(dry_run=True)) == 0

    assert "Continuation preview" in capsys.readouterr().out
    assert len(store.list_sessions()) == sessions_before
    assert (
        store.get_events("source", types=["session.continuation.packet_created"]) == []
    )
