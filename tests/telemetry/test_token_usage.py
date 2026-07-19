from __future__ import annotations

import json
from pathlib import Path

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.storage.runtime.session_store.models import EventRecord
from openminion.modules.telemetry.usage import (
    StatsService,
    TokenUsageCoverage,
    TokenUsageDimensionCoverage,
    TokenUsageEventRef,
    TokenUsageRecord,
    TokenUsageSummary,
    summary_to_json_payload,
)
from openminion.modules.telemetry.usage.token_usage import (
    coverage_from_session_events,
    records_from_session_event,
)

_FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "token_usage"
    / "openminion_token_usage_v1.json"
)


def test_provider_total_is_emitted_once_and_cache_diagnostic_is_non_additive() -> None:
    completed = {
        "event_id": "event-1",
        "seq": 2,
        "timestamp": "2026-07-17T10:00:00+00:00",
        "event_type": "llm.call.completed",
        "payload": {
            "llm_call_id": "call-1",
            "provider": "openai",
            "model": "gpt-test",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 21,
                "cached_tokens": 3,
            },
        },
    }
    records = records_from_session_event(completed, session_id="session-1")

    total_records = [record for record in records if record.surface == "llm_total"]
    assert len(total_records) == 1
    assert total_records[0].total_tokens == 21
    assert total_records[0].total_source == "provider"
    assert records[0].surface == "llm_total"

    diagnostic = records_from_session_event(
        {
            "event_type": "llm.cache.metrics",
            "payload": {"cached_tokens": 3, "prompt_cache_key": "cache-1"},
        },
        session_id="session-1",
    )
    summary = TokenUsageSummary(
        session_id="session-1",
        records=records + diagnostic,
    )
    assert summary.total_provider_tokens == 21
    assert summary.total_cache_read_tokens == 3
    assert diagnostic[0].surface == "llm_cache_diagnostic"


def test_missing_or_invalid_provider_total_is_marked_derived() -> None:
    records = records_from_session_event(
        {
            "event_type": "llm.call.completed",
            "payload": {
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 2,
                    "total_tokens": "invalid",
                }
            },
        },
        session_id="session-1",
    )

    total = next(record for record in records if record.surface == "llm_total")
    assert total.total_tokens == 10
    assert total.total_source == "derived"


def test_usage_coverage_distinguishes_reported_missing_and_invalid() -> None:
    coverage = coverage_from_session_events(
        [
            {
                "event_type": "llm.call.completed",
                "trace_id": "trace-1",
                "payload": {
                    "run_id": "run-1",
                    "llm_call_id": "call-1",
                    "provider": "openai",
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": None,
                        "total_tokens": "invalid",
                        "cache_read_tokens": "invalid",
                        "cached_tokens": 0,
                        "cache_creation_tokens": -1,
                    },
                },
            },
            {
                "event_type": "context.manifest.created",
                "payload": {"llm_call_id": "call-1", "total_used_tokens": 10},
            },
            {"event_type": "llm.cache.metrics", "payload": {}},
        ]
    )

    assert coverage.llm_call_events == 1
    assert coverage.context_manifest_events == 1
    assert coverage.cache_metric_events == 1
    assert coverage.provider_identified_llm_call_events == 1
    assert coverage.model_identified_llm_call_events == 0
    assert coverage.run_id_present_events == 1
    assert coverage.trace_id_present_events == 1
    assert coverage.llm_call_id_present_events == 2
    assert coverage.input_tokens == TokenUsageDimensionCoverage(reported=1)
    assert coverage.output_tokens == TokenUsageDimensionCoverage(missing=1)
    assert coverage.total_tokens == TokenUsageDimensionCoverage(invalid=1)
    assert coverage.cache_read_tokens == TokenUsageDimensionCoverage(reported=1)
    assert coverage.cache_write_tokens == TokenUsageDimensionCoverage(invalid=1)


def test_usage_aliases_skip_present_but_invalid_values() -> None:
    records = records_from_session_event(
        {
            "event_type": "llm.call.completed",
            "payload": {
                "usage": {
                    "input_tokens": None,
                    "prompt_tokens": 8,
                    "output_tokens": "invalid",
                    "completion_tokens": 2,
                    "cache_read_tokens": None,
                    "cached_tokens": 3,
                }
            },
        },
        session_id="session-1",
    )

    summary = TokenUsageSummary(session_id="session-1", records=records)
    assert summary.total_input_tokens == 8
    assert summary.total_output_tokens == 2
    assert summary.total_cache_read_tokens == 3
    assert summary.total_provider_tokens == 10


def test_context_manifest_preserves_opaque_cache_correlation() -> None:
    records = records_from_session_event(
        {
            "event_type": "context.manifest.created",
            "payload": {
                "llm_call_id": "call-1",
                "prompt_context_id": "context-1",
                "prompt_cache_key": "openai:opaque",
                "static_prefix_hash": "prefix-opaque",
                "cache_hit": True,
                "total_used_tokens": 30,
            },
        },
        session_id="session-1",
    )

    assert len(records) == 1
    assert records[0].prompt_cache_key == "openai:opaque"
    assert records[0].static_prefix_hash == "prefix-opaque"
    assert records[0].cache_hit is True


def test_bounded_read_reports_incomplete_without_projecting_sentinel(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionStore(tmp_path / "bounded.db")
    try:
        session_id = store.create_session(
            initial_agent_id="agent.main", profile_version="v1"
        )
        for index in range(3):
            store.append_event(
                session_id,
                event_type="llm.call.completed",
                payload={"usage": {"input_tokens": index + 1}},
            )

        summary = StatsService(store).get_session_token_usage(
            session_id,
            event_limit=3,
        )

        assert summary.complete is False
        assert summary.event_limit == 3
        assert summary.events_scanned == 4
        assert summary.source_event_count == 2
        assert summary.first_source_event is not None
        assert summary.last_source_event is not None
        assert summary.last_source_event.sequence == 3
    finally:
        store.close()


def test_runtime_event_record_normalizes_id_and_created_at() -> None:
    event = EventRecord(
        id=7,
        session_id="runtime-session",
        event_type="llm.call.completed",
        payload={"usage": {"input_tokens": 4}},
        created_at="2026-07-17T11:00:00+00:00",
    )

    class RuntimeStore:
        def list_events(self, **_kwargs):
            return [event]

    summary = StatsService(RuntimeStore()).get_session_token_usage("runtime-session")

    assert summary.records[0].source_event_id == "7"
    assert summary.records[0].observed_at == "2026-07-17T11:00:00+00:00"
    assert summary.complete is True
    assert summary.event_limit == 10_000


def test_run_context_correlation_uses_call_id_not_cache_key(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "correlation.db")
    try:
        session_id = store.create_session(
            initial_agent_id="agent.main", profile_version="v1"
        )
        run_id = store.create_run_record(
            session_id,
            run_type="llm",
            run_id="run-1",
            meta={"request_id": "trace-1"},
        )
        store.finish_run_record(run_id, status="completed")
        store.append_event(
            session_id,
            event_type="context.manifest.created",
            payload={
                "llm_call_id": "call-1",
                "prompt_cache_key": "shared-key",
                "total_used_tokens": 20,
            },
        )
        store.append_event(
            session_id,
            event_type="context.manifest.created",
            payload={
                "llm_call_id": "call-other",
                "prompt_cache_key": "shared-key",
                "total_used_tokens": 99,
            },
        )
        store.append_event(
            session_id,
            event_type="llm.cache.metrics",
            payload={"prompt_cache_key": "shared-key", "cached_tokens": 50},
        )
        store.append_event(
            session_id,
            event_type="llm.call.completed",
            payload={"llm_call_id": "call-1", "usage": {"input_tokens": 5}},
            trace_id="trace-1",
        )

        summary = StatsService(store).get_run_token_usage(run_id)

        assert summary is not None
        assert summary.source_event_count == 2
        assert summary.total_estimated_tokens == 20
        assert all(
            record.surface != "llm_cache_diagnostic" for record in summary.records
        )
        assert {record.llm_call_id for record in summary.records} == {"call-1"}
    finally:
        store.close()


def test_json_export_matches_shared_v1_fixture() -> None:
    source = TokenUsageEventRef(
        sequence=1,
        observed_at="2026-07-17T12:00:00+00:00",
        event_type="llm.call.completed",
        event_id="event-fixture",
    )
    base = {
        "session_id": "session-fixture",
        "run_id": "run-fixture",
        "llm_call_id": "call-fixture",
        "provider": "openai",
        "model": "gpt-fixture",
        "source_event_type": source.event_type,
        "source_event_id": source.event_id,
        "source_event_sequence": source.sequence,
        "observed_at": source.observed_at,
    }
    summary = TokenUsageSummary(
        session_id="session-fixture",
        run_id="run-fixture",
        records=(
            TokenUsageRecord(
                **base,
                surface="llm_total",
                total_tokens=15,
                total_source="provider",
            ),
            TokenUsageRecord(**base, surface="llm_prompt", input_tokens=10),
            TokenUsageRecord(**base, surface="llm_output", output_tokens=5),
        ),
        source_event_count=1,
        events_scanned=1,
        first_source_event=source,
        last_source_event=source,
        coverage=TokenUsageCoverage(
            llm_call_events=1,
            provider_identified_llm_call_events=1,
            model_identified_llm_call_events=1,
            run_id_present_events=1,
            llm_call_id_present_events=1,
            input_tokens=TokenUsageDimensionCoverage(reported=1),
            output_tokens=TokenUsageDimensionCoverage(reported=1),
            total_tokens=TokenUsageDimensionCoverage(reported=1),
            cache_read_tokens=TokenUsageDimensionCoverage(missing=1),
            cache_write_tokens=TokenUsageDimensionCoverage(missing=1),
        ),
    )

    expected = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert summary_to_json_payload(summary) == expected


def test_export_models_normalize_json_boundary_values() -> None:
    source = TokenUsageEventRef(
        sequence="4",
        observed_at=" 2026-07-17T12:00:00+00:00 ",
        event_type=" llm.call.completed ",
    )
    record = TokenUsageRecord(
        session_id=" session-1 ",
        provider=7,
        surface=" llm_prompt ",
        input_tokens="5",
        total_source="unknown",
        cache_hit=1,
    )
    payload = summary_to_json_payload(
        TokenUsageSummary(
            session_id=" session-1 ",
            records=[record],
            source_event_count="1",
            events_scanned="2",
            first_source_event=source,
            last_source_event=source,
        )
    )

    assert payload["session_id"] == "session-1"
    assert payload["source_event_count"] == 1
    assert payload["source_event_range"]["first"] == {
        "sequence": 4,
        "observed_at": "2026-07-17T12:00:00+00:00",
        "event_type": "llm.call.completed",
    }
    assert payload["records"][0]["provider"] == "7"
    assert payload["records"][0]["surface"] == "llm_prompt"
    assert payload["records"][0]["total_source"] == ""
    assert payload["records"][0]["cache_hit"] is None
    assert payload["coverage"]["input_tokens"] == {
        "reported": 0,
        "missing": 0,
        "invalid": 0,
    }
    assert json.loads(json.dumps(payload)) == payload
