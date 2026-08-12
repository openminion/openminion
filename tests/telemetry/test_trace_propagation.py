from __future__ import annotations

from openminion.base.config import OTELExporterConfig
from openminion.modules.telemetry.export.otel import (
    OpenTelemetryTraceExporter,
    RecordingOTELTraceSink,
)
from openminion.modules.telemetry.export.sdk import OpenTelemetrySDKSink
from openminion.modules.telemetry.schemas import TelemetryEvent


TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def _event(event_type: str, **data: object) -> TelemetryEvent:
    return TelemetryEvent(
        session_id="session-1",
        turn_id="turn-1",
        event_type=event_type,
        trace_key="trace-1",
        invocation_id="invocation-1",
        execution_id=str(data.get("execution_id") or "execution-1"),
        agent_id="worker",
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


def test_receiver_execution_is_finite_internal_span_under_remote_parent() -> None:
    exporter, sink = _exporter()
    exporter.export(
        _event(
            "agent.execution.started",
            execution_id="execution-1",
            agent_name="worker",
            traceparent=TRACEPARENT,
            tracestate="vendor=value",
        )
    )
    exporter.export(
        _event(
            "agent.execution.completed",
            execution_id="execution-1",
            status="ok",
        )
    )

    span = sink.records[0]
    assert span.name == "invoke_agent worker"
    assert span.span_kind == "INTERNAL"
    assert span.parent_traceparent == TRACEPARENT
    assert span.tracestate == "vendor=value"
    assert span.end_timestamp_ns is not None


def test_v2_envelope_identity_pairs_execution_and_selects_trace_key() -> None:
    exporter, sink = _exporter()
    exporter.export(_event("agent.execution.started", agent_name="worker"))
    exporter.export(_event("agent.execution.completed", status="ok"))

    span = sink.records[0]
    assert span.trace_key == "trace-1"
    assert span.name == "invoke_agent worker"
    assert span.end_timestamp_ns is not None


def test_remote_caller_handoff_is_client_span_and_local_turn_is_sibling() -> None:
    exporter, sink = _exporter()
    exporter.export(
        _event(
            "agent.handoff.started",
            handoff_id="handoff-1",
            handoff_role="caller",
            target_agent="travel-worker",
        )
    )
    exporter.export(
        _event(
            "agent.turn.started",
            turn_operation_id="turn-op-1",
        )
    )
    exporter.export(
        _event("agent.handoff.completed", handoff_id="handoff-1", status="ok")
    )
    exporter.export(
        _event("agent.turn.completed", turn_operation_id="turn-op-1", status="ok")
    )

    handoff, turn = [record for record in sink.records if record.kind == "span"]
    assert handoff.name == "invoke_agent travel-worker"
    assert handoff.span_kind == "CLIENT"
    assert turn.name == "openminion.turn"
    assert turn.span_kind == "INTERNAL"


def test_malformed_parent_is_preserved_as_diagnostic_fact_not_fabricated() -> None:
    exporter, sink = _exporter()
    exporter.export(
        _event(
            "agent.execution.started",
            execution_id="execution-2",
            traceparent="malformed",
        )
    )
    exporter.export(
        _event("agent.execution.failed", execution_id="execution-2", status="error")
    )

    assert sink.records[0].parent_traceparent == "malformed"


def test_sdk_exports_one_parented_trace_with_correlated_log() -> None:
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import (
        InMemoryLogRecordExporter,
        SimpleLogRecordProcessor,
    )
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    span_exporter = InMemorySpanExporter()
    trace_provider = TracerProvider()
    trace_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    log_exporter = InMemoryLogRecordExporter()
    logger_provider = LoggerProvider()
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(log_exporter))
    sink = OpenTelemetrySDKSink(
        tracer=trace_provider.get_tracer("test.agent-observability"),
        trace_provider=trace_provider,
        logger=logger_provider.get_logger("test.agent-observability"),
        logger_provider=logger_provider,
    )
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(enabled=True, endpoint="http://collector:4318"),
        sink=sink,
    )

    def emit(event_type: str, timestamp: float, **data: object) -> None:
        exporter.export(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                event_type=event_type,
                timestamp=timestamp,
                trace_key="invocation-1",
                invocation_id="invocation-1",
                execution_id="execution-1",
                agent_id="worker",
                data=dict(data),
            )
        )

    emit(
        "agent.execution.started", 1.0, execution_id="execution-1", agent_name="worker"
    )
    emit("agent.turn.started", 2.0, turn_operation_id="turn-1")
    emit("llm.call.started", 3.0, llm_call_id="model-1", model="model-v1")
    emit("llm.call.completed", 4.0, llm_call_id="model-1", status="completed")
    emit("tool.execution.started", 5.0, tool_call_id="tool-1", tool_name="lookup")
    emit(
        "tool.execution.failed",
        6.0,
        tool_call_id="tool-1",
        tool_name="lookup",
        status="failed",
        error={"type": "tool_failed"},
    )
    emit("agent.turn.completed", 7.0, turn_operation_id="turn-1", status="completed")
    emit(
        "agent.execution.completed", 8.0, execution_id="execution-1", status="completed"
    )
    exporter.close()

    spans = {span.name: span for span in span_exporter.get_finished_spans()}
    root = spans["invoke_agent worker"]
    turn = spans["openminion.turn"]
    model = spans["chat model-v1"]
    tool = spans["execute_tool lookup"]
    assert {span.context.trace_id for span in spans.values()} == {root.context.trace_id}
    assert turn.parent.span_id == root.context.span_id
    assert model.parent.span_id == turn.context.span_id
    assert tool.parent.span_id == turn.context.span_id

    tool_log = next(
        record.log_record
        for record in log_exporter.get_finished_logs()
        if record.log_record.event_name == "tool.execution.failed"
    )
    assert tool_log.trace_id == root.context.trace_id
    assert tool_log.span_id == tool.context.span_id
