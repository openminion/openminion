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
from openminion.services.stats import (
    StatsService,
    TokenUsageRecord,
    summary_to_json_payload,
)


def test_service_stats_surface_reexports_canonical_owners() -> None:
    from openminion.cli.status.stats import (
        format_run_stats_footer as canonical_format,
    )
    from openminion.modules.telemetry.usage import (
        RunStats as canonical_run_stats,
        StatsService as canonical_service,
        TokenUsageRecord as canonical_record,
    )
    from openminion.services.stats import (
        RunStats as compatibility_run_stats,
        StatsService as compatibility_service,
        TokenUsageRecord as compatibility_record,
        format_run_stats_footer as compatibility_format,
    )

    assert compatibility_run_stats is canonical_run_stats
    assert compatibility_service is canonical_service
    assert compatibility_record is canonical_record
    assert compatibility_format is canonical_format


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


def test_token_usage_record_coerces_malformed_values() -> None:
    record = TokenUsageRecord(
        session_id="session-1",
        surface="llm_prompt",
        input_tokens="bad",
        output_tokens=-2,
        cache_read_tokens="4",
        cache_write_tokens=None,
        estimated_tokens="7",
    )

    assert record.input_tokens == 0
    assert record.output_tokens == 0
    assert record.cache_read_tokens == 4
    assert record.cache_write_tokens == 0
    assert record.estimated_tokens == 7


def test_run_token_usage_normalizes_llm_surfaces_from_events(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1"
    )
    run_id = store.create_run_record(session_id, run_type="llm", run_id="run-usage")
    store.finish_run_record(run_id, status="completed")
    store.append_event(
        session_id=session_id,
        event_type="llm.call.completed",
        payload={
            "run_id": run_id,
            "llm_call_id": "call-1",
            "provider": "anthropic",
            "model": "claude-test",
            "prompt": "ignore this text 9999; usage fields are authoritative",
            "usage": {
                "prompt_tokens": "12",
                "completion_tokens": 5,
                "cached_tokens": "3",
                "cache_creation_tokens": 2,
            },
        },
    )

    summary = StatsService(store).get_run_token_usage(run_id)

    assert summary is not None
    assert summary.total_input_tokens == 12
    assert summary.total_output_tokens == 5
    assert summary.total_cache_read_tokens == 3
    assert summary.total_cache_write_tokens == 2
    assert summary.totals_by_surface == {
        "llm_prompt": 12,
        "llm_output": 5,
        "llm_cache_read": 3,
        "llm_cache_write": 2,
        "llm_total": 17,
    }
    assert {record.surface for record in summary.records} == {
        "llm_prompt",
        "llm_output",
        "llm_cache_read",
        "llm_cache_write",
        "llm_total",
    }
    assert all(record.llm_call_id == "call-1" for record in summary.records)


def test_run_token_usage_malformed_usage_values_become_zero(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1"
    )
    run_id = store.create_run_record(session_id, run_type="llm", run_id="run-bad")
    store.finish_run_record(run_id, status="completed")
    store.append_event(
        session_id=session_id,
        event_type="llm.call.completed",
        payload={
            "run_id": run_id,
            "usage": {
                "prompt_tokens": "not-an-int",
                "completion_tokens": -9,
                "cached_tokens": None,
                "cache_creation_tokens": "also-bad",
            },
        },
    )

    summary = StatsService(store).get_run_token_usage(run_id)

    assert summary is not None
    assert summary.records == ()
    assert summary.total_input_tokens == 0
    assert summary.total_output_tokens == 0
    assert summary.total_cache_read_tokens == 0
    assert summary.total_cache_write_tokens == 0


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


def test_run_token_usage_can_fall_back_to_request_trace(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1"
    )
    run_id = store.create_run_record(
        session_id,
        run_type="llm",
        run_id="run-token-trace",
        meta={"request_id": "req-token-trace"},
    )
    store.finish_run_record(run_id, status="completed")
    store.append_event(
        session_id=session_id,
        event_type="llm.call.completed",
        payload={"usage": {"prompt_tokens": 6, "completion_tokens": 4}},
        trace_id="req-token-trace",
    )

    summary = StatsService(store).get_run_token_usage(run_id)

    assert summary is not None
    assert summary.total_input_tokens == 6
    assert summary.total_output_tokens == 4


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


def test_session_token_usage_includes_context_pack_and_bucket_records(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1"
    )
    store.append_event(
        session_id=session_id,
        event_type="context.manifest.created",
        payload={
            "llm_call_id": "call-context",
            "prompt_context_id": "prompt-context-1",
            "total_used_tokens": 100,
            "total_cap_tokens": 200,
            "pack_policy_used": "position_aware_v1",
            "token_budget_buckets": {
                "recent_window": {"used_tokens": 40, "cap_tokens": 60},
                "retrieval": {"used_tokens": 25, "cap_tokens": 80},
            },
        },
    )
    store.append_event(
        session_id=session_id,
        event_type="context.manifest.created",
        payload={"total_used_tokens": 10},
    )

    summary = StatsService(store).get_session_token_usage(session_id)

    assert summary.total_estimated_tokens == 175
    assert summary.totals_by_surface == {
        "context_pack": 110,
        "context_bucket": 65,
    }
    assert summary.totals_by_context_bucket == {
        "recent_window": 40,
        "retrieval": 25,
    }
    bucket_records = [
        record for record in summary.records if record.surface == "context_bucket"
    ]
    assert all(record.estimated for record in bucket_records)
    assert all(record.policy == "position_aware_v1" for record in bucket_records)


def test_token_usage_export_has_stable_prompt_free_shape(
    store: SQLiteSessionStore,
) -> None:
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1"
    )
    store.append_event(
        session_id=session_id,
        event_type="llm.call.completed",
        payload={
            "run_id": "run-export",
            "prompt": "do not export this raw prompt",
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        },
    )

    payload = summary_to_json_payload(
        StatsService(store).get_session_token_usage(session_id)
    )

    assert payload["session_id"] == session_id
    assert payload["totals"]["input_tokens"] == 3
    assert payload["totals"]["output_tokens"] == 2
    assert payload["records"][0]["source_event_type"] == "llm.call.completed"
    assert "prompt" not in payload["records"][0]
    assert "do not export" not in str(payload)


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
