from __future__ import annotations

from dataclasses import dataclass, field

from openminion.base.config import OTELExporterConfig
from openminion.modules.telemetry.export.otel import (
    OpenTelemetryTraceExporter,
    RecordingOTELTraceSink,
)
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.tool.base import Tool, ToolExecutionContext, ToolExecutionResult
from openminion.modules.tool.contracts import ProviderToolCall
from openminion.modules.tool.registry import ToolRegistry


@dataclass
class _Telemetry:
    events: list[TelemetryEvent] = field(default_factory=list)

    async def emit_canonical_event(
        self,
        session_id: str,
        turn_id: str,
        event_type: str,
        payload: dict,
        *,
        trace_id: str = "",
        status: str | None = None,
        error: dict | None = None,
    ) -> None:
        data = dict(payload)
        data.update({"trace_id": trace_id, "status": status})
        if error:
            data["error"] = dict(error)
        self.events.append(
            TelemetryEvent(
                session_id=session_id,
                turn_id=turn_id,
                event_type=event_type,
                data=data,
            )
        )

    async def emit_module_counter(self, *args, **kwargs) -> None:
        return None


class _SuccessTool(Tool):
    name = "lookup"

    def execute(self, arguments, context) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            content="result",
            verified=True,
            data={"exit_code": 0},
        )


def _context(telemetry: _Telemetry) -> ToolExecutionContext:
    return ToolExecutionContext(
        channel="test",
        target="local",
        session_id="session-1",
        metadata={
            "turn_id": "turn-1",
            "trace_id": "trace-1",
            "invocation_id": "invocation-1",
            "execution_id": "execution-1",
        },
        telemetryctl=telemetry,
    )


def test_shared_executor_emits_one_normalized_success_lifecycle() -> None:
    telemetry = _Telemetry()
    registry = ToolRegistry([_SuccessTool()])
    batch = registry.execute_calls(
        [
            ProviderToolCall(
                id="call-1",
                name="lookup",
                arguments={"ticket": "secret-ticket"},
            )
        ],
        context=_context(telemetry),
    )

    assert batch.results[0].ok is True
    assert [event.event_type for event in telemetry.events] == [
        "tool.execution.started",
        "tool.execution.completed",
    ]
    started, completed = telemetry.events
    assert started.data["argument_count"] == 1
    assert "secret-ticket" not in str(started.data)
    assert completed.data["verified"] is True
    assert completed.data["exit_code"] == 0
    assert completed.data["invocation_id"] == "invocation-1"


def test_unknown_tool_emits_one_failed_lifecycle_and_execute_tool_span() -> None:
    telemetry = _Telemetry()
    registry = ToolRegistry()
    registry.execute_calls(
        [ProviderToolCall(id="call-2", name="missing", arguments={})],
        context=_context(telemetry),
    )

    assert [event.event_type for event in telemetry.events] == [
        "tool.execution.started",
        "tool.execution.failed",
    ]
    assert telemetry.events[-1].data["error"]["type"] == "unknown_tool_name"

    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(
        OTELExporterConfig(enabled=True, endpoint="http://collector:4318"),
        sink=sink,
    )
    for event in telemetry.events:
        exporter.export(event)
    span = sink.records[0]
    assert span.name == "execute_tool missing"
    assert span.span_kind == "INTERNAL"
    assert span.attributes["gen_ai.operation.name"] == "execute_tool"
    assert span.attributes["gen_ai.tool.name"] == "missing"
    assert span.attributes["error.type"] == "unknown_tool_name"
