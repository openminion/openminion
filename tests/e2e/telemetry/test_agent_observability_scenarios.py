from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time
from typing import Any

import pytest

from openminion.base.config import OTELExporterConfig
from openminion.modules.telemetry.schemas import (
    TELEMETRY_EVENT_SCHEMA_V2,
    TelemetryEvent,
)
from openminion.modules.telemetry.service import TelemetryService


TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
SECRET = "aobs-secret-must-not-appear"


def _event(
    event_type: str,
    *,
    invocation_id: str,
    execution_id: str,
    turn_id: str,
    agent_id: str,
    **data: object,
) -> TelemetryEvent:
    return TelemetryEvent(
        session_id=f"session-{invocation_id}",
        turn_id=turn_id,
        event_type=event_type,
        trace_key=invocation_id,
        invocation_id=invocation_id,
        execution_id=execution_id,
        agent_id=agent_id,
        data=dict(data),
    )


def _record_scenario(
    tmp_path: Path, events: list[TelemetryEvent]
) -> list[TelemetryEvent]:
    service = TelemetryService(
        home_root=tmp_path,
        otel_exporter_config=OTELExporterConfig(
            enabled=True,
            endpoint="http://127.0.0.1:14317",
            protocol="grpc",
            service_name="openminion-agent-observability-e2e",
        ),
    )
    for event in events:
        service.record_event_sync(event)
    invocation_id = str(events[0].invocation_id)
    stored = asyncio.run(service.get_invocation_events(invocation_id))
    service.close_sync()
    return stored


def _read_artifact(path: Path, expected: str) -> str:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if expected in text:
                return text
        time.sleep(0.25)
    raise AssertionError(f"{expected!r} was not exported to {path.name}")


def _documents(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _dicts(child)


def _spans(text: str) -> list[dict[str, Any]]:
    return [
        item
        for document in _documents(text)
        for item in _dicts(document)
        if "name" in item and "spanId" in item
    ]


def _assert_local(stored: list[TelemetryEvent], expected: set[str]) -> None:
    assert {event.event_type for event in stored} == expected
    assert all(event.schema_version == TELEMETRY_EVENT_SCHEMA_V2 for event in stored)
    assert all(event.event_id for event in stored)
    assert SECRET not in json.dumps([event.to_dict() for event in stored])


@pytest.mark.e2e
def test_customer_support_desk_agent(collector_artifacts: Path, tmp_path: Path) -> None:
    invocation = "11111111-1111-4111-8111-111111111111"
    execution = "21111111-1111-4111-8111-111111111111"
    common = {
        "invocation_id": invocation,
        "execution_id": execution,
        "turn_id": "support-turn",
        "agent_id": "support-desk",
    }
    events = [
        _event("agent.invocation.started", **common, status="started"),
        _event("agent.execution.started", **common, agent_name="support-desk"),
        _event("agent.turn.started", **common, turn_operation_id="support-turn-op"),
        _event(
            "llm.call.started",
            **common,
            llm_call_id="support-model",
            operation="chat",
            model="support-model-v1",
            provider_name="test-provider",
        ),
        _event(
            "llm.call.completed",
            **common,
            llm_call_id="support-model",
            status="completed",
            response_model="support-model-v1",
            usage={"input_tokens": 18, "output_tokens": 7},
            provider_round_trip_ms=12,
            cost_usd=0.002,
            cost_source="provider",
            content=SECRET,
        ),
        _event(
            "tool.execution.started",
            **common,
            tool_call_id="kb-query",
            tool_name="knowledge_base.query",
            argument_count=1,
        ),
        _event(
            "tool.execution.completed",
            **common,
            tool_call_id="kb-query",
            tool_name="knowledge_base.query",
            status="completed",
            duration_ms=4,
            verification_status="passed",
            result=SECRET,
            audit_enabled=True,
        ),
        _event(
            "policy.decision",
            **common,
            owner="support-policy",
            action="ticket.update",
            decision="allow",
            reason_code="scope_allowed",
        ),
        _event(
            "business.outcome.recorded",
            **common,
            domain="support",
            outcome="support.ticket.updated",
            status="completed",
            value=1,
            unit="ticket",
            outcome_reference="ticket-ref-01",
            escalation_path="human_specialist",
        ),
        _event(
            "agent.turn.completed",
            **common,
            turn_operation_id="support-turn-op",
            status="completed",
            duration_ms=25,
            terminal_reason="escalated",
        ),
        _event(
            "agent.execution.completed", **common, status="completed", duration_ms=28
        ),
        _event(
            "agent.invocation.completed", **common, status="completed", duration_ms=30
        ),
    ]

    stored = _record_scenario(tmp_path, events)
    _assert_local(stored, {event.event_type for event in events})
    traces = _read_artifact(collector_artifacts / "traces.json", invocation)
    metrics = _read_artifact(
        collector_artifacts / "metrics.json", "openminion_business_outcomes_total"
    )
    logs = _read_artifact(collector_artifacts / "logs.json", "EventRecord")
    assert {
        "invoke_agent support-desk",
        "openminion.turn",
        "chat support-model-v1",
        "execute_tool knowledge_base.query",
    }.issubset({span["name"] for span in _spans(traces)})
    assert all(
        span.get("startTimeUnixNano") and span.get("endTimeUnixNano")
        for span in _spans(traces)
        if invocation in json.dumps(span)
    )
    assert "support.ticket.updated" in logs
    assert "ticket_id" not in metrics
    assert SECRET not in traces + metrics + logs


@pytest.mark.e2e
def test_ai_coding_assistant(collector_artifacts: Path, tmp_path: Path) -> None:
    invocation = "12222222-2222-4222-8222-222222222222"
    execution = "22222222-2222-4222-8222-222222222222"
    common = {
        "invocation_id": invocation,
        "execution_id": execution,
        "turn_id": "coding-turn",
        "agent_id": "coding-assistant",
    }
    events = [
        _event("agent.execution.started", **common, agent_name="coding-assistant"),
        _event("agent.phase.started", **common, phase_id="coding-plan", phase="plan"),
        _event(
            "agent.phase.completed",
            **common,
            phase_id="coding-plan",
            phase="plan",
            status="completed",
            duration_ms=3,
            proposed_diff_reference="diff-ref-01",
            diff_body=SECRET,
        ),
        _event(
            "llm.call.started",
            **common,
            llm_call_id="coding-model",
            operation="chat",
            model="coding-model-v1",
            provider_name="test-provider",
        ),
        _event(
            "llm.call.failed",
            **common,
            llm_call_id="coding-model",
            status="error",
            retry_count=1,
            usage={"input_tokens": 24, "output_tokens": 0},
            provider_round_trip_ms=9,
            cost_usd=0.001,
            cost_source="provider",
            error={"type": "provider_timeout"},
            system_prompt=SECRET,
        ),
        _event(
            "tool.execution.started",
            **common,
            tool_call_id="repo-read",
            tool_name="repository.read",
            argument_count=1,
        ),
        _event(
            "tool.execution.completed",
            **common,
            tool_call_id="repo-read",
            tool_name="repository.read",
            status="completed",
            duration_ms=2,
            verification_status="passed",
            path=SECRET,
        ),
        _event(
            "tool.execution.started",
            **common,
            tool_call_id="shell-check",
            tool_name="shell.execute",
            argument_count=1,
        ),
        _event(
            "tool.execution.failed",
            **common,
            tool_call_id="shell-check",
            tool_name="shell.execute",
            status="error",
            duration_ms=5,
            verification_status="failed",
            exit_code=1,
            error={"type": "command_failed"},
            arguments=SECRET,
        ),
        _event(
            "approval_required",
            **common,
            action="apply_diff",
            reason_code="write_scope",
            status="required",
        ),
        _event(
            "business.outcome.recorded",
            **common,
            domain="coding",
            outcome="verification.completed",
            status="failed",
            value=1,
            unit="check",
            outcome_reference="verification-ref-01",
        ),
        _event(
            "agent.execution.failed",
            **common,
            status="failed",
            duration_ms=20,
            terminal_reason="verification_failed",
        ),
    ]

    stored = _record_scenario(tmp_path, events)
    _assert_local(stored, {event.event_type for event in events})
    traces = _read_artifact(collector_artifacts / "traces.json", invocation)
    metrics = _read_artifact(
        collector_artifacts / "metrics.json", "openminion_tool_calls_total"
    )
    logs = _read_artifact(collector_artifacts / "logs.json", "LogRecord")
    names = {span["name"] for span in _spans(traces)}
    assert {
        "invoke_agent coding-assistant",
        "plan coding-assistant",
        "chat coding-model-v1",
        "execute_tool repository.read",
        "execute_tool shell.execute",
    }.issubset(names)
    assert "llm.call.failed" in logs
    assert "tool.execution.failed" in logs
    assert "verification.completed" in metrics + logs + traces
    assert SECRET not in traces + metrics + logs


@pytest.mark.e2e
def test_multi_agent_travel_planner(collector_artifacts: Path, tmp_path: Path) -> None:
    invocation = "13333333-3333-4333-8333-333333333333"
    coordinator = "23333333-3333-4333-8333-333333333333"
    worker = "33333333-3333-4333-8333-333333333333"
    coordinator_common = {
        "invocation_id": invocation,
        "execution_id": coordinator,
        "turn_id": "travel-turn",
        "agent_id": "travel-coordinator",
    }
    worker_common = {
        "invocation_id": invocation,
        "execution_id": worker,
        "turn_id": "travel-worker-turn",
        "agent_id": "travel-worker",
    }
    events = [
        _event(
            "agent.execution.started",
            **coordinator_common,
            agent_name="travel-coordinator",
        ),
        _event(
            "agent.handoff.started",
            **coordinator_common,
            handoff_id="travel-handoff",
            handoff_role="caller",
            target_agent="travel-worker",
            remote_endpoint="a2a://travel-worker",
        ),
        _event(
            "agent.handoff.completed",
            **coordinator_common,
            handoff_id="travel-handoff",
            handoff_role="caller",
            target_agent="travel-worker",
            status="completed",
            duration_ms=8,
        ),
        _event(
            "agent.execution.started",
            **worker_common,
            agent_name="travel-worker",
            traceparent=TRACEPARENT,
            tracestate="vendor=test",
        ),
        _event(
            "llm.call.started",
            **worker_common,
            llm_call_id="travel-model",
            operation="chat",
            model="travel-model-v1",
            provider_name="test-provider",
        ),
        _event(
            "llm.call.completed",
            **worker_common,
            llm_call_id="travel-model",
            status="completed",
            usage={"input_tokens": 20, "output_tokens": 8},
            provider_round_trip_ms=7,
            cost_usd=0.003,
            cost_source="provider",
        ),
        _event(
            "tool.execution.started",
            **worker_common,
            tool_call_id="weather",
            tool_name="weather.forecast",
        ),
        _event(
            "tool.execution.completed",
            **worker_common,
            tool_call_id="weather",
            tool_name="weather.forecast",
            status="completed",
            duration_ms=3,
            verification_status="passed",
        ),
        _event(
            "tool.execution.started",
            **worker_common,
            tool_call_id="currency",
            tool_name="currency.convert",
        ),
        _event(
            "tool.execution.completed",
            **worker_common,
            tool_call_id="currency",
            tool_name="currency.convert",
            status="completed",
            duration_ms=2,
            verification_status="passed",
        ),
        _event(
            "agent.execution.completed",
            **worker_common,
            status="completed",
            duration_ms=12,
        ),
        _event(
            "telemetry.propagation.invalid",
            **coordinator_common,
            reason_code="malformed_traceparent",
            status="warning",
        ),
        _event(
            "agent.handoff.started",
            **coordinator_common,
            handoff_id="booking-handoff",
            handoff_role="caller",
            target_agent="booking-worker",
        ),
        _event(
            "agent.handoff.failed",
            **coordinator_common,
            handoff_id="booking-handoff",
            handoff_role="caller",
            target_agent="booking-worker",
            status="failed",
            duration_ms=3,
            error={"type": "child_unavailable"},
        ),
        _event(
            "business.outcome.recorded",
            **coordinator_common,
            domain="travel",
            outcome="itinerary.created",
            status="completed",
            value=1,
            unit="itinerary",
            outcome_reference="itinerary-ref-01",
            traveler_id="traveler-ref-01",
            itinerary_details=SECRET,
        ),
        _event(
            "agent.execution.completed",
            **coordinator_common,
            status="completed",
            duration_ms=24,
        ),
    ]

    stored = _record_scenario(tmp_path, events)
    _assert_local(stored, {event.event_type for event in events})
    assert {event.execution_id for event in stored} == {coordinator, worker}
    traces = _read_artifact(collector_artifacts / "traces.json", invocation)
    metrics = _read_artifact(
        collector_artifacts / "metrics.json", "openminion_handoff_operations_total"
    )
    logs = _read_artifact(collector_artifacts / "logs.json", "agent.handoff.completed")
    spans = _spans(traces)
    caller = next(
        span
        for span in spans
        if span["name"] == "invoke_agent travel-worker"
        and span.get("kind") in (3, "SPAN_KIND_CLIENT")
    )
    receiver = next(
        span
        for span in spans
        if span["name"] == "invoke_agent travel-worker"
        and span.get("kind") in (1, "SPAN_KIND_INTERNAL")
    )
    assert caller["endTimeUnixNano"]
    assert receiver["parentSpanId"] == "00f067aa0ba902b7"
    assert {"execute_tool weather.forecast", "execute_tool currency.convert"}.issubset(
        {span["name"] for span in spans}
    )
    assert "itinerary.created" in metrics + logs + traces
    assert "telemetry.propagation.invalid" in logs
    assert "agent.handoff.failed" in logs
    assert "openminion_model_cost" in metrics
    assert "traveler_id" not in metrics
    assert SECRET not in traces + metrics + logs
