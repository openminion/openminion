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


class _MCPResultTool(Tool):
    name = "mcp.fixture.delete_item"

    def __init__(self, *, ok: bool) -> None:
        self._ok = ok

    def execute(self, arguments, context) -> ToolExecutionResult:
        del arguments, context
        if self._ok:
            return ToolExecutionResult(
                tool_name=self.name,
                ok=True,
                content="secret remote content",
                verified=True,
                source="mcp",
                data={
                    "mcp_server": "fixture",
                    "mcp_remote_tool_name": "delete-item",
                    "content_items": [{"text": "secret remote content"}],
                },
            )
        return ToolExecutionResult(
            tool_name=self.name,
            ok=False,
            content="",
            verified=False,
            error="secret approval prose",
            source="mcp",
            data={
                "error_code": "CONFIRM_REQUIRED",
                "details": {
                    "mcp_server": "fixture",
                    "mcp_remote_tool_name": "delete-item",
                    "runtime_tool_name": self.name,
                    "approval_mode": "always",
                    "approval_required": True,
                    "reason_code": "POLICY_MCP_APPROVAL_REQUIRED",
                    "secret": "must-not-export",
                },
            },
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


def test_mcp_success_emits_only_allowlisted_terminal_provenance() -> None:
    telemetry = _Telemetry()
    tool = _MCPResultTool(ok=True)
    result = (
        ToolRegistry([tool])
        .execute_calls(
            [ProviderToolCall(id="call-mcp-ok", name=tool.name, arguments={})],
            context=_context(telemetry),
        )
        .results[0]
    )

    assert result.ok is True
    terminal = telemetry.events[-1].data
    assert terminal["mcp_server"] == "fixture"
    assert terminal["mcp_remote_tool_name"] == "delete-item"
    assert terminal["runtime_tool_name"] == tool.name
    assert terminal["mcp_primitive"] == "tools"
    assert "content_items" not in terminal
    assert "secret remote content" not in str(terminal)


def test_mcp_approval_failure_emits_only_allowlisted_terminal_provenance() -> None:
    telemetry = _Telemetry()
    tool = _MCPResultTool(ok=False)
    result = (
        ToolRegistry([tool])
        .execute_calls(
            [ProviderToolCall(id="call-mcp-denied", name=tool.name, arguments={})],
            context=_context(telemetry),
        )
        .results[0]
    )

    assert result.state == "denied"
    terminal = telemetry.events[-1].data
    assert terminal["mcp_server"] == "fixture"
    assert terminal["mcp_remote_tool_name"] == "delete-item"
    assert terminal["runtime_tool_name"] == tool.name
    assert terminal["approval_mode"] == "always"
    assert terminal["approval_required"] is True
    assert terminal["reason_code"] == "POLICY_MCP_APPROVAL_REQUIRED"
    assert terminal["mcp_primitive"] == "tools"
    assert "secret" not in str(terminal)
