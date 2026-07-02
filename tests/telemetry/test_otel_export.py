from __future__ import annotations

import json

from openminion.base.config import OTELExporterConfig
from openminion.modules.telemetry.export.otel import (
    OpenTelemetryTraceExporter,
    RecordingOTELTraceSink,
)
from openminion.modules.telemetry.schemas import TelemetryEvent


def test_tool_event_emits_span_record() -> None:
    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(enabled=True, endpoint="http://collector:4318"),
        sink=sink,
    )

    exported = exporter.export(
        TelemetryEvent(
            session_id="sess-1",
            turn_id="turn-1",
            event_type="tool.completed",
            data={
                "trace_id": "trace-1",
                "tool_name": "web.search",
                "status": "ok",
                "summary": "should stay local by default",
            },
        )
    )

    assert exported is True
    assert len(sink.records) == 1
    record = sink.records[0]
    assert record.kind == "span"
    assert record.trace_key == "trace-1"
    assert record.name == "tool.completed"
    assert record.attributes["openminion.payload.tool_name"] == "web.search"
    assert "openminion.payload.summary" not in record.attributes


def test_non_tool_event_emits_root_event_and_filters_prose_by_default() -> None:
    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(enabled=True, endpoint="http://collector:4318"),
        sink=sink,
    )

    exported = exporter.export(
        TelemetryEvent(
            session_id="sess-1",
            turn_id="turn-1",
            event_type="turn.assistant",
            data={
                "trace_id": "trace-1",
                "role": "assistant",
                "content": "draft answer that should not export by default",
                "status": "ok",
            },
        )
    )

    assert exported is True
    assert len(sink.records) == 1
    record = sink.records[0]
    assert record.kind == "event"
    assert record.terminal is True
    assert record.attributes["openminion.payload.role"] == "assistant"
    assert "openminion.payload.content" not in record.attributes


def test_include_assistant_body_is_opt_in() -> None:
    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(
            enabled=True,
            endpoint="http://collector:4318",
            include_assistant_body=True,
        ),
        sink=sink,
    )

    exporter.export(
        TelemetryEvent(
            session_id="sess-1",
            turn_id="turn-1",
            event_type="turn.assistant",
            data={
                "trace_id": "trace-1",
                "content": "allowed when operator opts in",
            },
        )
    )

    assert sink.records[0].attributes["openminion.payload.content"] == (
        "allowed when operator opts in"
    )


def test_list_payloads_export_as_deterministic_json_strings() -> None:
    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(enabled=True, endpoint="http://collector:4318"),
        sink=sink,
    )

    exporter.export(
        TelemetryEvent(
            session_id="sess-1",
            turn_id="turn-1",
            event_type="policy.applied",
            data={
                "trace_id": "trace-1",
                "steps": ["plan", 2, {"ok": True, "name": "tool"}],
            },
        )
    )

    record = sink.records[0]
    assert record.attributes["openminion.payload.steps"] == json.dumps(
        ["plan", 2, {"name": "tool", "ok": True}],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def test_list_payloads_keep_privacy_filtering_by_default() -> None:
    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(enabled=True, endpoint="http://collector:4318"),
        sink=sink,
    )

    exporter.export(
        TelemetryEvent(
            session_id="sess-1",
            turn_id="turn-1",
            event_type="turn.assistant",
            data={
                "trace_id": "trace-1",
                "summary": ["should", "not", "export"],
                "messages": [
                    {"role": "assistant", "content": "secret", "status": "ok"}
                ],
            },
        )
    )

    record = sink.records[0]
    assert "openminion.payload.summary" not in record.attributes
    assert record.attributes["openminion.payload.messages"] == json.dumps(
        [{"role": "assistant", "status": "ok"}],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def test_exporter_noops_when_endpoint_absent() -> None:
    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(enabled=True, endpoint=""),
        sink=sink,
    )

    exported = exporter.export(
        TelemetryEvent(
            session_id="sess-1",
            turn_id="turn-1",
            event_type="policy.applied",
            data={"trace_id": "trace-1"},
        )
    )

    assert exported is False
    assert sink.records == []


def test_sampling_is_deterministic_by_trace_key() -> None:
    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(
            enabled=True,
            endpoint="http://collector:4318",
            sample_rate=0.5,
        ),
        sink=sink,
    )
    event = TelemetryEvent(
        session_id="sess-1",
        turn_id="turn-1",
        event_type="policy.applied",
        data={"trace_id": "trace-deterministic"},
    )

    first = exporter.export(event)
    second = exporter.export(event)

    assert first == second
    if first:
        assert len(sink.records) == 2
    else:
        assert sink.records == []


def _make_exporter() -> tuple[OpenTelemetryTraceExporter, RecordingOTELTraceSink]:
    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(enabled=True, endpoint="http://collector:4318"),
        sink=sink,
    )
    return exporter, sink


def _event(
    event_type: str,
    *,
    trace_id: str = "trace-1",
    timestamp: float | None = None,
    **data,
) -> TelemetryEvent:
    return TelemetryEvent(
        session_id="sess-1",
        turn_id="turn-1",
        event_type=event_type,
        data={"trace_id": trace_id, **data},
        timestamp=timestamp,
    )


def test_storage_query_event_routes_to_span_classification() -> None:
    exporter, sink = _make_exporter()

    exported = exporter.export(_event("storage.query", duration_ms=12.5, rows=4))

    assert exported is True
    record = next(item for item in sink.records if item.kind == "span")
    assert record.kind == "span"
    assert record.name == "storage.query"
    assert record.attributes["openminion.payload.duration_ms"] == 12.5


def test_storage_pool_stats_event_routes_to_metric_gauge() -> None:
    exporter, sink = _make_exporter()

    exported = exporter.export(_event("storage.pool.stats", active=3, pool_size=8))

    assert exported is True
    assert len(sink.records) == 1
    record = sink.records[0]
    assert record.kind == "metric"
    assert record.metric_kind == "gauge"
    assert record.metric_value == 3.0


def test_memory_aggregate_counter_routes_to_metric_counter() -> None:
    exporter, sink = _make_exporter()

    exported = exporter.export(_event("memory.scope_capacity.evicted", count=7))

    assert exported is True
    assert len(sink.records) == 1
    record = sink.records[0]
    assert record.kind == "metric"
    assert record.metric_kind == "counter"
    assert record.metric_value == 7.0


def test_metric_and_message_catchalls_are_excluded_from_otel_emission() -> None:
    exporter, sink = _make_exporter()

    for excluded_type in ("metric", "message"):
        exported = exporter.export(
            _event(excluded_type, trace_id=f"trace-{excluded_type}", value=42)
        )
        assert exported is False, f"{excluded_type} should be excluded"

    assert sink.records == [], "excluded events must not reach the sink"


def test_paired_llm_call_events_collapse_to_single_span() -> None:
    exporter, sink = _make_exporter()

    exporter.export(
        _event(
            "llm.call.started",
            timestamp=1000.0,
            llm_call_id="call-abc",
            model="claude-opus-4-7",
            provider="anthropic",
        )
    )
    assert sink.records == []

    exporter.export(
        _event(
            "llm.call.completed",
            timestamp=1002.5,
            llm_call_id="call-abc",
            finish_reason="stop",
            usage={"input_tokens": 100, "output_tokens": 50},
        )
    )

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record.kind == "span"
    assert record.name == "llm.call"
    assert record.timestamp_ns == 1_000 * 1_000_000_000
    assert record.end_timestamp_ns == int(1002.5 * 1_000_000_000)
    assert record.attributes.get("gen_ai.system") == "anthropic"
    assert record.attributes.get("gen_ai.usage.input_tokens") == 100


def test_paired_rlm_tick_events_collapse_to_single_span() -> None:
    exporter, sink = _make_exporter()

    exporter.export(
        _event("rlm.tick.started", timestamp=2000.0, tick_index=3, purpose="retrieve")
    )
    exporter.export(
        _event("rlm.tick.completed", timestamp=2001.0, tick_index=3, status="ok")
    )

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record.kind == "span"
    assert record.name == "rlm.tick"
    assert record.end_timestamp_ns is not None
    assert record.end_timestamp_ns > record.timestamp_ns


def test_paired_completion_without_started_falls_through_classification() -> None:
    exporter, sink = _make_exporter()

    exporter.export(_event("llm.call.completed", llm_call_id="orphan-call"))

    assert len(sink.records) == 1
    assert sink.records[0].kind == "event"


def test_paired_started_without_pairing_id_falls_back_to_event() -> None:
    exporter, sink = _make_exporter()

    exporter.export(_event("llm.call.started"))

    assert len(sink.records) == 1
    assert sink.records[0].kind == "event"


def test_pending_paired_span_cap_evicts_oldest_to_bound_memory() -> None:
    exporter, sink = _make_exporter()
    cap = OpenTelemetryTraceExporter._MAX_PENDING_PAIRED_SPANS

    for i in range(cap + 50):
        exporter.export(_event("llm.call.started", llm_call_id=f"call-{i}"))

    assert len(exporter._pending_paired_spans) <= cap


def test_tool_prefix_still_routes_to_span_via_legacy_fast_path() -> None:
    exporter, sink = _make_exporter()

    exporter.export(_event("tool.run", tool_name="web.search"))

    assert len(sink.records) == 1
    assert sink.records[0].kind == "span"
    assert sink.records[0].name == "tool.run"


def test_unknown_event_type_falls_back_to_root_span_event() -> None:
    exporter, sink = _make_exporter()

    exporter.export(_event("policy.applied"))

    assert len(sink.records) == 1
    assert sink.records[0].kind == "event"


def test_otel_exporter_config_carries_backend_and_headers_fields() -> None:
    config = OTELExporterConfig(
        enabled=True,
        endpoint="https://collector.example.com/v1/traces",
        backend="tempo",
        headers={"Authorization": "Bearer redacted"},
    )

    assert config.backend == "tempo"
    assert config.headers == {"Authorization": "Bearer redacted"}


def test_otel_04_every_catalog_event_resolves_to_a_valid_class() -> None:
    from openminion.modules.telemetry.events.catalog import EVENT_TYPES
    from openminion.modules.telemetry.export.otel import (
        _CLASS_EXCLUDED,
        _CLASS_LOG,
        _CLASS_METRIC,
        _CLASS_SPAN,
        _EVENT_CLASSIFICATION,
        _PAIRED_COMPLETION_EVENTS,
        _PAIRED_SPAN_CLASSES,
    )

    valid_explicit_classes = {_CLASS_SPAN, _CLASS_METRIC, _CLASS_LOG, _CLASS_EXCLUDED}

    classified: dict[str, set[str]] = {
        "span": set(),
        "metric": set(),
        "log_record": set(),
        "excluded": set(),
        "paired_span": set(),
        "paired_completion": set(),
    }
    for event_type in EVENT_TYPES:
        if event_type in _PAIRED_SPAN_CLASSES:
            classified["paired_span"].add(event_type)
            continue
        if event_type in _PAIRED_COMPLETION_EVENTS:
            classified["paired_completion"].add(event_type)
            continue
        cls = _EVENT_CLASSIFICATION.get(event_type)
        if cls is None and event_type.startswith("tool."):
            classified["span"].add(event_type)
            continue
        if cls is None:
            classified["log_record"].add(event_type)
            continue
        assert cls in valid_explicit_classes, (
            f"{event_type!r} has invalid classification {cls!r}"
        )
        classified[cls].add(event_type)

    covered = (
        len(classified["span"])
        + len(classified["metric"])
        + len(classified["log_record"])
        + len(classified["excluded"])
        + len(classified["paired_span"])
        + len(classified["paired_completion"])
    )
    assert covered == len(EVENT_TYPES), (
        f"classification covered {covered} but catalog has {len(EVENT_TYPES)}"
    )

    assert classified["paired_span"] == {"llm.call.started", "rlm.tick.started"}
    assert classified["paired_completion"] == {
        "llm.call.completed",
        "rlm.tick.completed",
    }
    assert classified["excluded"] == {"metric", "message"}
    assert classified["span"].issuperset(
        {
            "tool.run",
            "storage.query",
            "storage.slow_query",
            "storage.migration",
            "chat.phase_timing",
        }
    )
    assert classified["metric"] == {
        "storage.pool.stats",
        "memory.scope_capacity.evicted",
        "memory.soft_deleted.purged",
        "llm.cache.metrics",
        "module.stats",
        "tui.render",
    }


def test_otel_04_chat_phase_timing_routes_to_span() -> None:
    exporter, sink = _make_exporter()

    exported = exporter.export(
        _event(
            "chat.phase_timing",
            total_turn_ms=120,
            time_to_first_text_ms=35,
            provider_round_trip_ms=70,
            context_pack_build_ms=12,
            provider_request_build_ms=3,
            transport="urllib",
            process_mode="single-process",
            session_id="must-not-be-metric-label",
            turn_id="must-not-be-metric-label",
        )
    )

    assert exported is True
    record = next(item for item in sink.records if item.kind == "span")
    assert record.kind == "span"
    assert record.name == "chat.phase_timing"
    metrics = [item for item in sink.records if item.kind == "metric"]
    metric_names = {item.name for item in metrics}
    assert {
        "openminion_turn_wall_ms",
        "openminion_turn_ttft_ms",
        "openminion_chat_phase_duration_ms",
        "openminion_provider_round_trip_ms",
        "openminion_context_assembly_ms",
    }.issubset(metric_names)
    turn_wall = next(item for item in metrics if item.name == "openminion_turn_wall_ms")
    assert turn_wall.metric_kind == "histogram"
    assert turn_wall.metric_value == 120.0
    assert turn_wall.attributes == {
        "route_class": "single-process",
        "outcome": "ok",
        "cold_start": "false",
    }
    for metric in metrics:
        assert "session_id" not in metric.attributes
        assert "turn_id" not in metric.attributes


def test_pomv2_storage_query_exports_low_cardinality_operation_metric() -> None:
    exporter, sink = _make_exporter()

    exported = exporter.export(
        _event(
            "storage.query",
            duration_ms=0,
            latency_ms=11,
            module_id="session-store",
            operation="append_turn",
            criticality="current_turn",
            session_id="must-not-be-metric-label",
        )
    )

    assert exported is True
    metric = next(
        item for item in sink.records if item.name == "openminion_storage_operation_ms"
    )
    assert metric.metric_kind == "histogram"
    assert metric.metric_value == 0.0
    assert metric.attributes == {
        "store_family": "session-store",
        "operation": "append_turn",
        "criticality": "current_turn",
        "outcome": "ok",
    }


def test_pomv2_tui_render_exports_low_cardinality_metrics() -> None:
    exporter, sink = _make_exporter()

    exported = exporter.export(
        _event(
            "tui.render",
            render_chunk_ms=7,
            queue_pressure=3,
            retained_messages=22,
            view_family="chat",
            session_id="must-not-be-metric-label",
            turn_id="must-not-be-metric-label",
            path="/tmp/not-a-label",
        )
    )

    assert exported is True
    metrics = [item for item in sink.records if item.kind == "metric"]
    names = {item.name for item in metrics}
    assert {
        "openminion_tui_render_chunk_ms",
        "openminion_tui_queue_pressure",
        "openminion_tui_retained_messages",
    }.issubset(names)
    chunk = next(
        item for item in metrics if item.name == "openminion_tui_render_chunk_ms"
    )
    assert chunk.metric_kind == "histogram"
    assert chunk.metric_value == 7.0
    assert chunk.attributes == {"view_family": "chat", "outcome": "ok"}
    for metric in metrics:
        assert "session_id" not in metric.attributes
        assert "turn_id" not in metric.attributes
        assert "path" not in metric.attributes


def test_otel_04_module_stats_routes_to_metric_gauge() -> None:
    exporter, sink = _make_exporter()

    exported = exporter.export(_event("module.stats", module="context", value=42.0))

    assert exported is True
    assert len(sink.records) == 1
    record = sink.records[0]
    assert record.kind == "metric"
    assert record.metric_kind == "gauge"


def test_otel_04_llm_cache_metrics_routes_to_metric_gauge() -> None:
    exporter, sink = _make_exporter()

    exported = exporter.export(_event("llm.cache.metrics", value=0.93))

    assert exported is True
    assert len(sink.records) == 1
    record = sink.records[0]
    assert record.kind == "metric"
    assert record.metric_kind == "gauge"


def test_disabled_exporter_noops_even_for_classified_events() -> None:
    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(enabled=False, endpoint="http://collector:4318"),
        sink=sink,
    )

    for event_type in (
        "storage.query",
        "storage.pool.stats",
        "memory.scope_capacity.evicted",
        "llm.call.started",
        "llm.call.completed",
        "tool.run",
        "policy.applied",
    ):
        exported = exporter.export(_event(event_type))
        assert exported is False, f"{event_type} should noop when disabled"

    assert sink.records == []
