from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


class _RecordingArtifactCtl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def ref_add(self, owner_type: str, owner_id: str, ref_or_sha: str) -> None:
        self.calls.append((owner_type, owner_id, ref_or_sha))


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteSessionStore:
    db_path = tmp_path / "sess-events.db"
    session_store = SQLiteSessionStore(db_path)
    yield session_store
    session_store.close()


def test_append_event_and_get_events(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    event_id = store.append_event(
        session_id,
        event_type="task.opened",
        payload={"task_id": "t1", "title": "demo"},
        trace_id="trace-1",
    )

    events = store.get_events(session_id)
    assert any(event["event_id"] == event_id for event in events)
    assert any(event["event_type"] == "task.opened" for event in events)


def test_get_recent_turns_includes_turn_events(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    store.append_turn(session_id, role="user", content="hello")
    store.append_turn(session_id, role="assistant", content="hi")

    turns = store.get_recent_turns(session_id, limit_messages=10)
    roles = [item["role"] for item in turns]
    assert roles == ["user", "assistant"]


def test_append_event_raises_on_closed_connection(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    store._conn.close()
    with pytest.raises(sqlite3.Error):
        store.append_event(
            session_id,
            event_type="llm.request.started",
            payload={"purpose": "act"},
        )


def test_append_turn_raises_on_closed_connection(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    store._conn.close()
    with pytest.raises(sqlite3.Error):
        store.append_turn(session_id, role="user", content="hello")


def test_list_events_raises_on_closed_connection(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    store._conn.close()
    with pytest.raises(sqlite3.Error):
        store.list_events(session_id)


def test_append_event_adds_artifact_edges_and_ignores_non_artifacts(
    tmp_path: Path,
) -> None:
    artifactctl = _RecordingArtifactCtl()
    session_store = SQLiteSessionStore(
        tmp_path / "sess-events-artifacts.db",
        artifactctl=artifactctl,
    )
    try:
        session_id = session_store.create_session(
            initial_agent_id="agent.main",
            profile_version="pv1",
        )
        valid_ref = f"artifact://sha256/{'a' * 64}"
        raw_sha = "b" * 64

        session_store.append_event(
            session_id,
            event_type="tool.call.completed",
            payload={"status": "ok"},
            artifact_refs=[valid_ref, "mem://skip", raw_sha, valid_ref],
        )

        assert artifactctl.calls == [
            ("session", session_id, valid_ref),
            ("session", session_id, raw_sha),
        ]
    finally:
        session_store.close()


def test_append_event_does_not_add_artifact_edges_when_persist_fails(
    tmp_path: Path,
) -> None:
    artifactctl = _RecordingArtifactCtl()
    session_store = SQLiteSessionStore(
        tmp_path / "sess-events-failure.db",
        artifactctl=artifactctl,
    )
    try:
        valid_ref = f"artifact://sha256/{'c' * 64}"

        with pytest.raises(ValueError, match="session not found"):
            session_store.append_event(
                "missing-session",
                event_type="tool.call.completed",
                payload={"status": "ok"},
                artifact_refs=[valid_ref],
            )

        assert artifactctl.calls == []
    finally:
        session_store.close()
