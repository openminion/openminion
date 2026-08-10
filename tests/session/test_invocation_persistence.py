from __future__ import annotations

from openminion.modules.session.storage.store import SQLiteSessionStore


def test_run_invocation_and_thread_survive_restart(tmp_path) -> None:
    db_path = tmp_path / "session.db"
    store = SQLiteSessionStore(db_path)
    session_id = store.create_session(initial_agent_id="agent-1")
    store.create_run_record(
        session_id,
        run_id="run-1",
        invocation_id="invocation-1",
        thread_id="thread-1",
    )
    store.create_run_record(
        session_id,
        run_id="run-2",
        invocation_id="invocation-1",
        thread_id="thread-1",
    )
    store.close()

    reopened = SQLiteSessionStore(db_path)
    assert reopened.get_run_record("run-1")["invocation_id"] == "invocation-1"
    assert [
        row["run_id"]
        for row in reopened.list_run_records_by_thread(session_id, "thread-1")
    ] == ["run-2", "run-1"]
    assert [
        row["run_id"] for row in reopened.list_run_records_by_invocation("invocation-1")
    ] == ["run-1", "run-2"]
    reopened.close()


def test_legacy_run_correlation_remains_nullable(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "session.db")
    session_id = store.create_session(initial_agent_id="agent-1")
    store.create_run_record(session_id, run_id="legacy-run")

    record = store.get_run_record("legacy-run")
    assert record["invocation_id"] is None
    assert record["thread_id"] is None
    store.close()
