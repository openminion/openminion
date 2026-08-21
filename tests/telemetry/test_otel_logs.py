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
        trace_key="trace-1",
        invocation_id="invocation-1",
        execution_id="execution-1",
        event_type=event_type,
        data=dict(data),
    )


def _exporter() -> tuple[OpenTelemetryTraceExporter, RecordingOTELTraceSink]:
    sink = RecordingOTELTraceSink()
    return (
        OpenTelemetryTraceExporter(
            OTELExporterConfig(enabled=True, endpoint="http://collector:4318"),
            sink=sink,
        ),
        sink,
    )


def test_tool_failure_emits_span_and_named_event_record() -> None:
    exporter, sink = _exporter()
    exporter.export(
        _event(
            "tool.execution.started",
            tool_call_id="call-1",
            tool_name="lookup",
        )
    )
    exporter.export(
        _event(
            "tool.execution.failed",
            tool_call_id="call-1",
            tool_name="lookup",
            status="denied",
            error={"type": "policy_denied"},
            _telemetry_policy={"redacted_fields": 1},
        )
    )

    assert [record.kind for record in sink.records if record.kind != "metric"] == [
        "span",
        "event_record",
    ]
    log = next(record for record in sink.records if record.kind == "event_record")
    assert log.name == "tool.execution.failed"
    assert log.attributes["openminion.log.severity"] == "ERROR"
    assert log.attributes["openminion.invocation_id"] == "invocation-1"
    assert log.attributes["openminion.payload._telemetry_policy.redacted_fields"] == 1


def test_provider_exhaustion_and_export_failure_are_diagnostic_logs() -> None:
    exporter, sink = _exporter()
    exporter.export(
        _event(
            "llm.call.failed",
            llm_call_id="call-2",
            error={"type": "provider_exhausted"},
        )
    )
    exporter.export(
        _event(
            "telemetry.export.failed",
            status="error",
            error={"type": "collector_unavailable"},
        )
    )

    logs = [record for record in sink.records if record.kind == "log_record"]
    assert [record.name for record in logs] == [
        "llm.call.failed",
        "telemetry.export.failed",
    ]


def test_invalid_propagation_is_warning_log_record() -> None:
    exporter, sink = _exporter()
    exporter.export(
        _event(
            "telemetry.propagation.invalid",
            reason_code="malformed_traceparent",
        )
    )

    log = sink.records[0]
    assert log.kind == "log_record"
    assert log.attributes["openminion.log.severity"] == "WARN"


def test_policy_and_handoff_are_named_event_records() -> None:
    exporter, sink = _exporter()
    exporter.export(_event("policy.denied", status="denied", reason_code="scope"))
    exporter.export(
        _event(
            "agent.handoff.failed",
            handoff_id="handoff-orphan",
            status="error",
        )
    )
    assert [record.kind for record in sink.records if record.kind != "metric"] == [
        "event_record",
        "event_record",
    ]


def test_routine_tool_success_has_no_duplicate_log_unless_audit_enabled() -> None:
    exporter, sink = _exporter()
    exporter.export(
        _event(
            "tool.execution.completed",
            tool_call_id="orphan-1",
            status="ok",
        )
    )
    assert all(
        record.kind not in {"event_record", "log_record"} for record in sink.records
    )

    exporter.export(
        _event(
            "tool.execution.completed",
            tool_call_id="orphan-2",
            status="ok",
            audit_enabled=True,
        )
    )
    assert any(record.kind == "event_record" for record in sink.records)
