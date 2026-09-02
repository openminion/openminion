from __future__ import annotations

from openminion.base.config import OTELExporterConfig
from openminion.modules.telemetry.export.otel import (
    OpenTelemetryTraceExporter,
    RecordingOTELTraceSink,
)
from openminion.modules.telemetry.schemas import TelemetryEvent


def _event(event_type: str, **data: object) -> TelemetryEvent:
    return TelemetryEvent(
        session_id="session-1",
        turn_id="turn-1",
        event_type=event_type,
        data=dict(data),
    )


def test_model_span_uses_current_name_kind_and_reported_facts() -> None:
    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(enabled=True, endpoint="http://collector:4318"),
        sink=sink,
    )
    exporter.export(
        _event(
            "llm.call.started",
            llm_call_id="call-1",
            model="claude-sonnet-4",
            purpose="entry",
        )
    )

    pending = next(iter(exporter._pending_paired_spans.values()))
    assert pending["span_name"] == "chat claude-sonnet-4"
    assert pending["attributes"]["gen_ai.operation.name"] == "chat"
    assert pending["attributes"]["gen_ai.request.model"] == "claude-sonnet-4"
    assert pending["attributes"]["openminion.model.purpose"] == "entry"
    assert "gen_ai.provider.name" not in pending["attributes"]

    exporter.export(
        _event(
            "llm.call.completed",
            llm_call_id="call-1",
            provider="anthropic",
            response_model="claude-sonnet-4-20250514",
            usage={
                "input_tokens": 120,
                "output_tokens": 30,
                "cached_tokens": 20,
                "cache_creation_tokens": 10,
                "reasoning_tokens": 5,
            },
            provider_round_trip_ms=250.0,
            time_to_first_token_ms=80.0,
            retry_count=1,
            finish_reason="end_turn",
            cost_usd=0.012,
            cost_source="provider",
            status="ok",
        )
    )

    span = sink.records[0]
    assert span.name == "chat claude-sonnet-4"
    assert span.span_kind == "CLIENT"
    assert span.attributes["gen_ai.provider.name"] == "anthropic"
    assert span.attributes["gen_ai.response.model"] == "claude-sonnet-4-20250514"
    assert span.attributes["gen_ai.usage.cache_read.input_tokens"] == 20
    assert span.attributes["gen_ai.usage.cache_write.input_tokens"] == 10
    assert span.attributes["gen_ai.usage.reasoning_tokens"] == 5
    assert span.attributes["gen_ai.usage.cost.usd"] == 0.012
    assert span.attributes["openminion.model.cost_source"] == "provider"


def test_failed_model_call_closes_span_with_normalized_error() -> None:
    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(enabled=True, endpoint="http://collector:4318"),
        sink=sink,
    )
    exporter.export(_event("llm.call.started", llm_call_id="call-2"))
    exporter.export(
        _event(
            "llm.call.failed",
            llm_call_id="call-2",
            status="error",
            error={
                "type": "provider_exhausted",
                "code": "LLM_PROVIDER_EXHAUSTED",
                "category": "availability",
                "message": "No provider completed the request",
            },
        )
    )

    span = sink.records[0]
    assert span.name == "chat"
    assert span.span_kind == "CLIENT"
    assert span.attributes["error.type"] == "provider_exhausted"
    assert span.attributes["openminion.error.code"] == "LLM_PROVIDER_EXHAUSTED"
    assert span.attributes["openminion.error.category"] == "availability"
    assert "openminion.error.message" not in span.attributes
    assert not any(
        key.endswith(".error.message") for key in span.attributes
    )


def test_error_prose_is_not_exported_when_assistant_body_is_enabled() -> None:
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
        _event("llm.call.started", llm_call_id="call-error-content")
    )
    exporter.export(
        _event(
            "llm.call.failed",
            llm_call_id="call-error-content",
            error={
                "code": "FAIL",
                "type": "provider_error",
                "category": "availability",
                "message": "private error prose",
                "details": "private details",
            },
            error_text="private scalar error",
        )
    )

    span = next(record for record in sink.records if record.kind == "span")
    assert span.attributes["openminion.error.code"] == "FAIL"
    assert span.attributes["openminion.error.category"] == "availability"
    assert "private error prose" not in str(span.attributes)
    assert "private details" not in str(span.attributes)
    assert "private scalar error" not in str(span.attributes)


def test_content_and_unreported_provider_facts_are_omitted() -> None:
    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(enabled=True, endpoint="http://collector:4318"),
        sink=sink,
    )
    exporter.export(
        _event(
            "llm.call.completed",
            llm_call_id="orphan",
            model="gpt-4o",
            content="private completion",
            system_prompt="private instructions",
        )
    )

    span = next(record for record in sink.records if record.kind == "span")
    assert "gen_ai.provider.name" not in span.attributes
    assert not any("content" in key for key in span.attributes)
    assert not any("system_prompt" in key for key in span.attributes)
