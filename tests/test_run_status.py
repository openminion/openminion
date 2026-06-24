from openminion.services.runtime.run_status import (
    RUN_STATE_COMPLETED,
    RUN_STATE_QUEUED,
    RUN_STATE_RESPONDING,
    RUN_STATE_RUNNING,
    append_run_state_event,
    list_session_run_events,
    list_session_runs,
)
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database


def _build_sessions(tmp_path):
    database_path = tmp_path / "state" / "openminion.db"
    migrate_database(database_path)
    connection = connect_database(database_path)
    sessions = SessionStore(connection)
    session = sessions.resolve_session(
        agent_id="main",
        channel="console",
        target="run-status",
    )
    return connection, sessions, session


def test_list_session_runs_returns_latest_state_and_terminal_time(tmp_path) -> None:
    connection, sessions, session = _build_sessions(tmp_path)
    try:
        run_id = "run-1"
        for state, step in (
            (RUN_STATE_QUEUED, "turn.accepted"),
            (RUN_STATE_RUNNING, "agent.generate"),
            (RUN_STATE_RESPONDING, "channel.send"),
            (RUN_STATE_COMPLETED, "turn.completed"),
        ):
            append_run_state_event(
                sessions,
                session_id=session.id,
                run_id=run_id,
                state=state,
                current_step=step,
            )

        runs = list_session_runs(sessions, session_id=session.id, limit=10)
    finally:
        connection.close()

    assert len(runs) == 1
    summary = runs[0]
    assert summary.run_id == run_id
    assert summary.state == RUN_STATE_COMPLETED
    assert summary.current_step == "turn.completed"
    assert summary.event_count == 4
    assert summary.started_at
    assert summary.ended_at


def test_list_session_run_events_filters_run_id(tmp_path) -> None:
    connection, sessions, session = _build_sessions(tmp_path)
    try:
        append_run_state_event(
            sessions,
            session_id=session.id,
            run_id="run-a",
            state=RUN_STATE_QUEUED,
            current_step="turn.accepted",
        )
        append_run_state_event(
            sessions,
            session_id=session.id,
            run_id="run-b",
            state=RUN_STATE_QUEUED,
            current_step="turn.accepted",
        )
        append_run_state_event(
            sessions,
            session_id=session.id,
            run_id="run-a",
            state=RUN_STATE_RUNNING,
            current_step="agent.generate",
        )

        events = list_session_run_events(
            sessions,
            session_id=session.id,
            run_id="run-a",
            limit=10,
        )
    finally:
        connection.close()

    assert len(events) == 2
    assert [event.run_id for event in events] == ["run-a", "run-a"]
    assert [event.state for event in events] == [RUN_STATE_QUEUED, RUN_STATE_RUNNING]


def test_list_session_run_events_limit_returns_latest_n_events(tmp_path) -> None:
    connection, sessions, session = _build_sessions(tmp_path)
    try:
        run_id = "run-limit"
        for index in range(5):
            append_run_state_event(
                sessions,
                session_id=session.id,
                run_id=run_id,
                state=RUN_STATE_RUNNING,
                current_step=f"step-{index}",
                payload={"index": index},
            )

        events = list_session_run_events(
            sessions,
            session_id=session.id,
            run_id=run_id,
            limit=2,
        )
    finally:
        connection.close()

    assert len(events) == 2
    assert events[0].current_step == "step-3"
    assert events[1].current_step == "step-4"
