from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteSessionStore:
    db_path = tmp_path / "phase2-char.db"
    session_store = SQLiteSessionStore(db_path)
    try:
        yield session_store
    finally:
        session_store.close()


# Constructor / bootstrap surface


def test_construction_initializes_full_helper_graph(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "init-graph.db")
    try:
        # Every helper documented in the spec's "current live seams" list.
        for attr in (
            "_event_store",
            "_slice_queries",
            "_event_writer",
            "_cron_store",
            "_state_store",
            "_summary_store",
            "_context_store",
            "_run_store",
            "_session_helper",
            "_replay_helper",
            "_slice_store",
        ):
            assert getattr(store, attr) is not None, f"missing helper {attr!r}"
    finally:
        store.close()


def test_construction_with_memory_path_succeeds() -> None:
    store = SQLiteSessionStore(":memory:")
    try:
        assert store.database_path == Path(":memory:")
        assert store._hybrid_store is not None
        assert store._temp_root is not None
    finally:
        store.close()


def test_construction_idempotent_on_existing_db(tmp_path: Path) -> None:
    db_path = tmp_path / "reopen.db"
    first = SQLiteSessionStore(db_path)
    first_session_id = first.create_session(
        initial_agent_id="agent.a", profile_version="pv1"
    )
    first.close()

    second = SQLiteSessionStore(db_path)
    try:
        sessions = second.list_sessions()
        assert any(item["session_id"] == first_session_id for item in sessions)
    finally:
        second.close()


def test_record_store_schema_bootstrap_is_idempotent(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(initial_agent_id="agent.a", profile_version="pv1")
    store._bootstrap_record_store_schema()
    store._bootstrap_record_store_schema()
    sessions = store.list_sessions()
    assert any(item["session_id"] == session_id for item in sessions)


# list_turns(...) characterization


def test_list_turns_returns_ascending_by_ts(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    store.append_turn(session_id, role="user", content="first")
    time.sleep(0.01)
    store.append_turn(session_id, role="assistant", content="second")
    time.sleep(0.01)
    store.append_turn(session_id, role="user", content="third")

    turns = store.list_turns(session_id)
    contents = [item["content"] for item in turns]
    assert contents == ["first", "second", "third"]


def test_list_turns_respects_limit(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    for idx in range(5):
        store.append_turn(session_id, role="user", content=f"msg-{idx}")
        time.sleep(0.005)

    turns = store.list_turns(session_id, limit=2)
    assert len(turns) == 2
    # limit picks the *most recent* two but they are returned ascending.
    contents = [item["content"] for item in turns]
    assert contents == ["msg-3", "msg-4"]


def test_list_turns_before_ts_excludes_at_or_after(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    store.append_turn(session_id, role="user", content="early")
    time.sleep(0.01)
    store.append_turn(session_id, role="assistant", content="middle")
    time.sleep(0.01)
    store.append_turn(session_id, role="user", content="late")

    all_turns = store.list_turns(session_id)
    middle_ts = next(item["ts"] for item in all_turns if item["content"] == "middle")

    earlier = store.list_turns(session_id, before_ts=middle_ts)
    contents = [item["content"] for item in earlier]
    # Strict less-than filter; "middle" itself is excluded.
    assert "middle" not in contents
    assert contents == ["early"]


def test_list_turns_returns_empty_for_unknown_session(
    store: SQLiteSessionStore,
) -> None:
    assert store.list_turns("does-not-exist") == []


def test_list_turns_raises_on_closed_connection(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    store._conn.close()
    with pytest.raises(sqlite3.Error):
        store.list_turns(session_id)


# list_events(...) characterization


def test_list_events_returns_ascending_by_seq(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    store.append_event(session_id, event_type="task.opened", payload={"n": 1})
    store.append_event(session_id, event_type="task.opened", payload={"n": 2})
    store.append_event(session_id, event_type="task.opened", payload={"n": 3})

    # Filter to the test events only; create_session emits extra bookkeeping
    # events (e.g. ``agent.bound``) which would otherwise interleave.
    events = store.list_events(session_id, event_type="task.opened")
    ns = [event["payload"]["n"] for event in events]
    assert ns == [1, 2, 3]


def test_list_events_filters_by_event_type(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    store.append_event(session_id, event_type="task.opened", payload={"k": "a"})
    store.append_event(session_id, event_type="task.closed", payload={"k": "b"})
    store.append_event(session_id, event_type="task.opened", payload={"k": "c"})

    opened = store.list_events(session_id, event_type="task.opened")
    assert [event["payload"]["k"] for event in opened] == ["a", "c"]


def test_list_events_filters_by_trace_and_agent(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    store.append_event(
        session_id,
        event_type="llm.request.started",
        payload={"v": 1},
        trace_id="trace-1",
        actor_type="agent",
        actor_id="agent.alpha",
    )
    store.append_event(
        session_id,
        event_type="llm.request.started",
        payload={"v": 2},
        trace_id="trace-2",
        actor_type="agent",
        actor_id="agent.beta",
    )

    by_trace = store.list_events(session_id, trace_id="trace-1")
    assert [event["payload"]["v"] for event in by_trace] == [1]

    by_agent = store.list_events(session_id, agent_id="agent.beta")
    assert [event["payload"]["v"] for event in by_agent] == [2]


def test_list_events_filters_by_status_post_query(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    store.append_event(
        session_id,
        event_type="llm.request.started",
        payload={"v": 1},
        status="started",
    )
    store.append_event(
        session_id,
        event_type="llm.request.completed",
        payload={"v": 2},
        status="ok",
    )
    store.append_event(
        session_id,
        event_type="llm.request.completed",
        payload={"v": 3},
        status="ok",
    )

    ok_events = store.list_events(session_id, status="ok")
    vs = [event["payload"]["v"] for event in ok_events]
    assert vs == [2, 3]


def test_list_events_limit_applied_after_status_filter(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    for n in range(5):
        store.append_event(
            session_id,
            event_type="llm.request.completed",
            payload={"n": n},
            status="ok",
        )

    tail = store.list_events(session_id, status="ok", limit=2)
    assert [event["payload"]["n"] for event in tail] == [3, 4]


def test_list_events_default_limit_is_100(store: SQLiteSessionStore) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="pv1"
    )
    for n in range(120):
        store.append_event(session_id, event_type="task.opened", payload={"n": n})

    default = store.list_events(session_id, event_type="task.opened")
    assert len(default) == 100
    # default returns the most recent 100, ascending
    assert default[0]["payload"]["n"] == 20
    assert default[-1]["payload"]["n"] == 119


def test_list_events_returns_empty_for_unknown_session(
    store: SQLiteSessionStore,
) -> None:
    assert store.list_events("nope") == []
