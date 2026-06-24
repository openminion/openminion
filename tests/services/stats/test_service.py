from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.storage.runtime.session_store import (
    SessionStore as RuntimeSessionStore,
)
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.sqlite import connect_database
from openminion.services.stats import StatsService


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteSessionStore:
    session_store = SQLiteSessionStore(tmp_path / "stats.db")
    yield session_store
    session_store.close()


def test_llm_call_completed_backfills_run_record_tokens(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1"
    )
    run_id = store.create_run_record(session_id, run_type="llm", run_id="run-1")

    store.append_event(
        session_id=session_id,
        event_type="llm.call.completed",
        payload={
            "run_id": run_id,
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 5,
                "cache_read_tokens": 3,
            },
        },
    )
    store.finish_run_record(run_id, status="completed")

    record = store.get_run_record(run_id)
    assert record is not None
    assert int(record["input_tokens"]) == 12
    assert int(record["output_tokens"]) == 5


def test_run_stats_can_fall_back_to_request_trace_when_events_lack_run_id(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1"
    )
    run_id = store.create_run_record(
        session_id,
        run_type="llm",
        run_id="run-trace",
        meta={"request_id": "req-trace"},
    )
    store.finish_run_record(
        run_id,
        status="completed",
        input_tokens=7,
        output_tokens=4,
    )
    store.append_event(
        session_id=session_id,
        event_type="llm.call.completed",
        payload={"usage": {"prompt_tokens": 7, "completion_tokens": 4}},
        trace_id="req-trace",
    )
    store.append_event(
        session_id=session_id,
        event_type="tool.request",
        payload={"tool_name": "web.search"},
        trace_id="req-trace",
    )
    store.append_event(
        session_id=session_id,
        event_type="tool.completed",
        payload={"tool_name": "web.search", "status": "success"},
        trace_id="req-trace",
    )

    summary = StatsService(store).get_run_stats(run_id)

    assert summary is not None
    assert summary.stats.input_tokens == 7
    assert summary.stats.output_tokens == 4
    assert summary.stats.llm_calls == 1
    assert summary.stats.tool_calls == 1
    assert summary.stats.tool_errors == 0


def test_session_stats_summary_uses_always_on_session_events(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1"
    )
    first_run = store.create_run_record(session_id, run_type="llm", run_id="run-a")
    second_run = store.create_run_record(session_id, run_type="llm", run_id="run-b")
    store.finish_run_record(
        first_run, status="completed", input_tokens=10, output_tokens=5
    )
    store.finish_run_record(
        second_run, status="completed", input_tokens=2, output_tokens=1
    )
    store.append_event(
        session_id=session_id,
        event_type="turn.assistant",
        payload={"text": "first"},
    )
    store.append_event(
        session_id=session_id,
        event_type="llm.call.completed",
        payload={
            "run_id": first_run,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )
    store.append_event(
        session_id=session_id,
        event_type="tool.requested",
        payload={"request": {"tool_name": "web.search"}},
    )
    store.append_event(
        session_id=session_id,
        event_type="tool.failed",
        payload={"tool_name": "web.search", "error": {"message": "boom"}},
    )
    store.append_event(
        session_id=session_id,
        event_type="turn.assistant",
        payload={"text": "second"},
    )
    store.append_event(
        session_id=session_id,
        event_type="llm.call.completed",
        payload={
            "run_id": second_run,
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        },
    )

    summary = StatsService(store).get_session_stats(session_id)

    assert summary.turn_count == 2
    assert summary.stats.input_tokens == 12
    assert summary.stats.output_tokens == 6
    assert summary.stats.llm_calls == 2
    assert summary.stats.tool_calls == 1
    assert summary.stats.tool_errors == 1
    assert summary.top_tools[0].name == "web.search"
    assert summary.top_tools[0].calls == 1


def test_session_stats_can_fall_back_to_persisted_outbound_message_stats(
    store: SQLiteSessionStore,
) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        database_path = Path(tmp_dir) / "state" / "openminion.db"
        migrate_database(database_path)
        connection = connect_database(database_path)
        runtime_store = RuntimeSessionStore(connection)
        session = runtime_store.resolve_session(
            agent_id="agent.main",
            channel="console",
            target="cli-chat",
            session_id="runtime-session-1",
        )
        runtime_store.append_message(
            session_id=session.id,
            role="outbound",
            body="hello",
            metadata={
                "run_stats_json": (
                    '{"input_tokens":9,"output_tokens":2,"cache_read_tokens":0,'
                    '"llm_calls":1,"tool_calls":0,"tool_errors":0,"duration_ms":1250}'
                )
            },
        )

        summary = StatsService(runtime_store).get_session_stats(session.id)

        assert summary.turn_count == 1
        assert summary.stats.input_tokens == 9
        assert summary.stats.output_tokens == 2
        assert summary.stats.llm_calls == 1
        connection.close()
