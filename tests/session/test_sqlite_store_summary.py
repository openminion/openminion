from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteSessionStore:
    db_path = tmp_path / "sess-summary.db"
    session_store = SQLiteSessionStore(db_path)
    yield session_store
    session_store.close()


def test_working_state_roundtrip(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    version = store.put_working_state(session_id, state_inline={"cursor": 1})
    assert version == 1

    latest = store.get_latest_working_state(session_id)
    assert latest is not None
    assert latest["version"] == 1
    assert latest["state_inline"]["cursor"] == 1

    active = store.get_active_state(session_id)
    assert active["cursor"] == 1


def test_summary_base_and_deltas(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    store.set_summary_base(session_id, "base-ref")
    store.append_summary_delta(session_id, "delta-1")
    store.append_summary_delta(session_id, "delta-2")

    summaries = store.get_summaries(session_id)
    assert summaries["base_ref"] == "base-ref"
    assert summaries["delta_refs"] == ["delta-1", "delta-2"]


def test_update_summary_and_needs_update(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    store.append_event(session_id, event_type="task.created", payload={"task_id": "t1"})
    assert store.needs_summary_update(session_id, threshold_events=1)

    store.update_summary(
        session_id, summary_short="short", summary_long="long", based_on_seq=2
    )
    assert store.get_summary(session_id, variant="short") == "short"
    assert store.get_summary(session_id, variant="long") == "long"
    assert store.needs_summary_update(session_id, threshold_events=1)
    assert not store.needs_summary_update(session_id, threshold_events=2)


def test_create_snapshot_uses_summary_and_state(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )

    store.put_working_state(session_id, state_inline={"cursor": 2})
    store.update_summary(
        session_id, summary_short="s", summary_long="l", based_on_seq=0
    )

    snapshot_id = store.create_snapshot(session_id)
    assert snapshot_id
