from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteSessionStore:
    db_path = tmp_path / "sess-refactor.db"
    session_store = SQLiteSessionStore(db_path)
    yield session_store
    session_store.close()


def test_create_list_get_session(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.alpha",
        profile_version="pv1",
        title="Test Session",
        tags=["smoke"],
    )

    sessions = store.list_sessions()
    ids = [item["session_id"] for item in sessions]
    assert session_id in ids

    session = store.get_session(session_id)
    assert session is not None
    assert session["active_agent_id"] == "agent.alpha"
    assert session["active_profile_version"] == "pv1"
    assert session["title"] == "Test Session"
    assert "smoke" in session.get("tags", [])


def test_status_updates_and_archive(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    store.set_status(session_id, "paused")
    assert store.get_session(session_id)["status"] == "paused"

    store.update_session_status(session_id, "active")
    assert store.get_session(session_id)["status"] == "active"

    store.archive_session(session_id)
    assert store.get_session(session_id)["status"] == "archived"


def test_status_update_missing_session_raises(store: SQLiteSessionStore) -> None:
    with pytest.raises(ValueError):
        store.update_session_status("missing-session", "paused")


def test_bind_agent_updates_session(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.alpha", profile_version="pv1"
    )

    store.bind_agent(
        session_id,
        agent_id="agent.beta",
        profile_version="pv2",
        reason="handoff",
    )

    session = store.get_session(session_id)
    assert session is not None
    assert session["active_agent_id"] == "agent.beta"
    assert session["active_profile_version"] == "pv2"

    events = store.get_events(session_id, types=["agent.bound"])
    assert events
    assert events[-1]["event_type"] == "agent.bound"


def test_append_and_list_turns(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    store.append_turn(session_id, role="user", content="hello")
    store.append_turn(session_id, role="assistant", content="hi")

    turns = store.list_turns(session_id)
    roles = [item["role"] for item in turns]
    assert roles == ["user", "assistant"]

    assert all(turn["session_id"] == session_id for turn in turns)
