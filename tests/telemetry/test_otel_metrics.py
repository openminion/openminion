from __future__ import annotations

from openminion.base.config import OTELExporterConfig
from openminion.modules.telemetry.export.otel import (
    OpenTelemetryTraceExporter,
    RecordingOTELTraceSink,
)
from openminion.modules.telemetry.schemas import TelemetryEvent


def _event(event_type: str, **data: object) -> TelemetryEvent:
    return TelemetryEvent(
        session_id="session-secret",
        turn_id="turn-secret",
        invocation_id="invocation-secret",
        execution_id="execution-secret",
        event_type=event_type,
        data=dict(data),
    )


def _metrics(event: TelemetryEvent):
    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(enabled=True, endpoint="http://collector:4318"),
        sink=sink,
    )
    exporter.export(event)
    return [record for record in sink.records if record.kind == "metric"]


def test_model_uses_standard_token_and_duration_instruments_once() -> None:
    metrics = _metrics(
        _event(
            "llm.call.completed",
            usage={"input_tokens": 10, "output_tokens": 4},
            provider_round_trip_ms=250,
        )
    )
    tokens = [item for item in metrics if item.name == "gen_ai.client.token.usage"]
    assert [(item.metric_value, item.metric_unit) for item in tokens] == [
        (10.0, "{token}"),
        (4.0, "{token}"),
    ]
    duration = next(
        item for item in metrics if item.name == "gen_ai.client.operation.duration"
    )
    assert duration.metric_value == 0.25
    assert duration.metric_unit == "s"
    assert not any(item.name == "openminion_model_input_tokens" for item in metrics)


def test_model_cost_requires_one_explicit_source() -> None:
    without_source = _metrics(_event("llm.call.completed", cost_usd=0.02, usage={}))
    assert not any(item.name == "openminion_model_cost" for item in without_source)

    with_source = _metrics(
        _event(
            "llm.call.completed",
            cost_usd=0.02,
            cost_source="provider",
            usage={},
        )
    )
    cost = next(item for item in with_source if item.name == "openminion_model_cost")
    assert cost.metric_value == 0.02
    assert cost.metric_unit == "USD"
    assert cost.attributes == {"cost_source": "provider"}


def test_policy_safety_business_and_lifecycle_metrics_are_bounded() -> None:
    events = [
        _event("policy.decision", decision="deny", action="execute"),
        _event(
            "safety.preempted",
            action="panic",
            violation_category="credential_boundary",
        ),
        _event(
            "business.outcome.recorded",
            domain="support",
            status="completed",
            value=1,
            unit="ticket",
            ticket_id="ticket-secret",
        ),
        _event("agent.execution.completed", status="completed", duration_ms=12),
    ]
    metrics = [metric for event in events for metric in _metrics(event)]
    assert {
        "openminion_policy_decisions_total",
        "openminion_safety_preemptions_total",
        "openminion_business_outcomes_total",
        "openminion_execution_operations_total",
        "openminion_execution_duration_ms",
    }.issubset({metric.name for metric in metrics})
    forbidden = {
        "session_id",
        "turn_id",
        "invocation_id",
        "execution_id",
        "ticket_id",
    }
    assert not any(forbidden & set(metric.attributes) for metric in metrics)
