from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import pytest

from openminion.modules.tool.plugin_contract import (
    CASArtifactSink,
    HealthStatus,
    MemoryArtifactSink,
    MemoryEventSink,
    MethodSchema,
    PolicyDecision,
    ToolCapabilities,
    ToolContext,
    ToolError,
    ToolInvocation,
    ToolResult,
    ToolSchemaBundle,
)
from openminion.modules.tool.runtime.plugin import ToolRuntime


class _ArtifactCtlRef:
    def __init__(self, ref: str, sha256: str) -> None:
        self.ref = ref
        self.sha256 = sha256
        self.created_at = "2026-04-02T00:00:00Z"


class _RecordingArtifactCtl:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def ingest_bytes(self, **kwargs: Any) -> _ArtifactCtlRef:
        self.calls.append(dict(kwargs))
        return _ArtifactCtlRef("artifact://sha256/" + ("e" * 64), "e" * 64)


class _BrokenEventSink:
    def emit(self, *, event_name: str, payload: Dict[str, Any]) -> None:
        del event_name, payload
        raise RuntimeError("sink down")


@dataclass
class _Method:
    method_name: str
    args_schema: Dict[str, Any]
    return_schema: Dict[str, Any]
    handler: Any

    def run(self, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        return self.handler(args, ctx)


class _Tool:
    def __init__(self, name: str, method: _Method, risk_level: str = "low") -> None:
        self.name = name
        self.methods = {method.method_name: method}
        self.capabilities = ToolCapabilities(
            risk_level=risk_level,
            requires_network=False,
            requires_filesystem=False,
            supports_streaming=False,
            supports_idempotency=True,
            side_effects="none",
        )

    def schema(self) -> ToolSchemaBundle:
        method = next(iter(self.methods.values()))
        return ToolSchemaBundle(
            tool=self.name,
            description="test tool",
            methods=[
                MethodSchema(
                    method_name=method.method_name,
                    args_schema=method.args_schema,
                    return_schema=method.return_schema,
                    description="test method",
                )
            ],
            capabilities=self.capabilities,
        )


class _Plugin:
    def __init__(self, plugin_id: str, version: str, tools: List[_Tool]) -> None:
        self.plugin_id = plugin_id
        self.version = version
        self._tools = tools
        self.init_called = False
        self.shutdown_called = False

    def get_tools(self) -> List[_Tool]:
        return self._tools

    def get_config_schema(self) -> Dict[str, Any] | None:
        return {"type": "object", "additionalProperties": True}

    def validate_config(self, config: Dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise ValueError("config must be a dict")

    def init(self, runtime: ToolRuntime) -> None:
        del runtime
        self.init_called = True

    def shutdown(self) -> None:
        self.shutdown_called = True

    def healthcheck(self) -> HealthStatus:
        return HealthStatus(ok=True, details={})


def _echo_handler(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    del ctx
    return ToolResult(status="ok", data={"echo": args.get("value")})


def _stdout_handler(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
    del args, ctx
    return ToolResult(status="ok", stdout="0123456789ABC", data={})


def test_runtime_register_list_schema_and_invoke():
    event_sink = MemoryEventSink()
    artifact_sink = MemoryArtifactSink()
    runtime = ToolRuntime(event_sink=event_sink, artifact_sink=artifact_sink)
    method = _Method("echo", {"type": "object"}, {"type": "object"}, _echo_handler)
    plugin = _Plugin("plugin.echo", "1.0.0", [_Tool("example.echo", method)])

    runtime.register(plugin)

    tools = runtime.list_tools()
    assert len(tools) == 1
    assert tools[0].tool == "example.echo"
    assert tools[0].methods == ["echo"]

    schema = runtime.get_tool_schema("example.echo")
    assert schema["tool"] == "example.echo"
    assert schema["methods"][0]["method_name"] == "echo"

    ctx = ToolContext(
        trace_id="trace-1", event_sink=event_sink, artifact_sink=artifact_sink
    )
    result = runtime.invoke(
        ToolInvocation(tool="example.echo", method="echo", args={"value": "hi"}),
        ctx,
    )
    assert result.status == "ok"
    assert result.data["echo"] == "hi"

    names = [item["event_name"] for item in event_sink.events]
    assert names == [
        "tool.example.echo.echo.requested",
        "tool.example.echo.echo.completed",
    ]


def test_runtime_event_payload_includes_orchestration_extras():
    event_sink = MemoryEventSink()
    runtime = ToolRuntime(event_sink=event_sink, artifact_sink=MemoryArtifactSink())
    method = _Method("echo", {"type": "object"}, {"type": "object"}, _echo_handler)
    runtime.register(_Plugin("plugin.echo", "1.0.0", [_Tool("example.echo", method)]))

    result = runtime.invoke(
        ToolInvocation(tool="example.echo", method="echo", args={"value": "hi"}),
        ToolContext(
            trace_id="trace-with-orchestration",
            event_sink=event_sink,
            artifact_sink=MemoryArtifactSink(),
            extras={
                "orchestration": {
                    "mode_name": "act_multi",
                    "workflow_name": "time_lookup",
                    "workflow_kind": "compiled",
                    "command_id": "cmd-123",
                }
            },
        ),
    )

    assert result.status == "ok"
    payload = event_sink.events[0]["payload"]
    assert payload["mode_name"] == "act_multi"
    assert payload["workflow_name"] == "time_lookup"
    assert payload["workflow_kind"] == "compiled"
    assert payload["command_id"] == "cmd-123"


def test_runtime_policy_deny_and_require_confirm_map_to_errors():
    class _DenyHook:
        def check(self, *, invocation, ctx, capabilities):
            del invocation, ctx, capabilities
            return PolicyDecision(
                action="deny", reason="blocked", details={"rule": "x"}
            )

    class _ConfirmHook:
        def check(self, *, invocation, ctx, capabilities):
            del invocation, ctx, capabilities
            return PolicyDecision(
                action="require_confirm",
                reason="needs confirm",
                details={
                    "rule": "y",
                    "confirm_request": {
                        "summary": {"tool": "example.echo", "method": "echo"}
                    },
                },
            )

    method = _Method("echo", {"type": "object"}, {"type": "object"}, _echo_handler)
    plugin = _Plugin("plugin.echo", "1.0.0", [_Tool("example.echo", method)])
    ctx = ToolContext(
        trace_id="trace-2",
        event_sink=MemoryEventSink(),
        artifact_sink=MemoryArtifactSink(),
    )

    deny_runtime = ToolRuntime(policy_hook=_DenyHook())
    deny_runtime.register(plugin)
    deny_result = deny_runtime.invoke(
        ToolInvocation(tool="example.echo", method="echo", args={}), ctx
    )
    assert deny_result.status == "error"
    assert deny_result.error is not None
    assert deny_result.error.code == "POLICY_DENIED"

    confirm_runtime = ToolRuntime(policy_hook=_ConfirmHook())
    confirm_runtime.register(plugin)
    confirm_result = confirm_runtime.invoke(
        ToolInvocation(tool="example.echo", method="echo", args={}), ctx
    )
    assert confirm_result.status == "error"
    assert confirm_result.error is not None
    assert confirm_result.error.code == "CONFIRM_REQUIRED"
    assert confirm_result.data["confirm_request"]["summary"]["method"] == "echo"


def test_runtime_externalizes_large_stdout_to_artifact():
    event_sink = MemoryEventSink()
    artifact_sink = MemoryArtifactSink()
    runtime = ToolRuntime(
        event_sink=event_sink,
        artifact_sink=artifact_sink,
        artifact_inline_threshold_bytes=8,
    )
    method = _Method("dump", {"type": "object"}, {"type": "object"}, _stdout_handler)
    runtime.register(_Plugin("plugin.dump", "1.0.0", [_Tool("example.dump", method)]))

    result = runtime.invoke(
        ToolInvocation(tool="example.dump", method="dump", args={}),
        ToolContext(
            trace_id="trace-3", event_sink=event_sink, artifact_sink=artifact_sink
        ),
    )
    assert result.status == "ok"
    assert len(result.artifacts) == 1
    assert "artifact_ref=" in (result.stdout or "")
    assert result.artifacts[0].ref in artifact_sink.objects


def test_cas_artifact_sink_emits_canonical_refs() -> None:
    artifactctl = _RecordingArtifactCtl()
    sink = CASArtifactSink(
        artifactctl=artifactctl,
        session_id="sess-cas",
        trace_id="trace-cas",
        agent_id="agent-cas",
    )

    artifact = sink.put_bytes(
        name="stdout.txt",
        content=b"hello",
        kind="text",
        meta={"mime": "text/plain"},
    )

    assert artifact.ref == "artifact://sha256/" + ("e" * 64)
    assert artifact.meta["sha256"] == "e" * 64
    assert artifactctl.calls[0]["session_id"] == "sess-cas"


def test_runtime_uses_cas_sink_when_artifactctl_is_provided() -> None:
    event_sink = MemoryEventSink()
    artifactctl = _RecordingArtifactCtl()
    runtime = ToolRuntime(
        event_sink=event_sink,
        artifactctl=artifactctl,
        artifact_inline_threshold_bytes=8,
    )
    method = _Method("dump", {"type": "object"}, {"type": "object"}, _stdout_handler)
    runtime.register(_Plugin("plugin.dump", "1.0.0", [_Tool("example.dump", method)]))

    result = runtime.invoke(
        ToolInvocation(tool="example.dump", method="dump", args={}),
        ToolContext(trace_id="trace-7", event_sink=event_sink),
    )

    assert result.status == "ok"
    assert len(result.artifacts) == 1
    assert result.artifacts[0].ref == "artifact://sha256/" + ("e" * 64)
    assert artifactctl.calls


def test_runtime_falls_back_to_memory_sink_when_cas_wiring_is_absent() -> None:
    runtime = ToolRuntime(artifact_inline_threshold_bytes=8)
    method = _Method("dump", {"type": "object"}, {"type": "object"}, _stdout_handler)
    runtime.register(_Plugin("plugin.dump", "1.0.0", [_Tool("example.dump", method)]))

    result = runtime.invoke(
        ToolInvocation(tool="example.dump", method="dump", args={}),
        ToolContext(trace_id="trace-8"),
    )

    assert result.status == "ok"
    assert len(result.artifacts) == 1
    assert result.artifacts[0].ref.startswith("artifact:sha256:")


def test_runtime_uses_context_event_sink_override():
    runtime_sink = MemoryEventSink()
    context_sink = MemoryEventSink()
    runtime = ToolRuntime(event_sink=runtime_sink)
    method = _Method("echo", {"type": "object"}, {"type": "object"}, _echo_handler)
    runtime.register(_Plugin("plugin.echo", "1.0.0", [_Tool("example.echo", method)]))

    result = runtime.invoke(
        ToolInvocation(tool="example.echo", method="echo", args={"value": "x"}),
        ToolContext(trace_id="trace-4", event_sink=context_sink),
    )
    assert result.status == "ok"
    assert len(context_sink.events) == 2
    assert runtime_sink.events == []


def test_runtime_register_rolls_back_on_failure():
    runtime = ToolRuntime()
    method_a = _Method("a", {"type": "object"}, {"type": "object"}, _echo_handler)
    method_b = _Method("b", {"type": "object"}, {"type": "object"}, _echo_handler)

    plugin_one = _Plugin("plugin.one", "1.0.0", [_Tool("dup.tool", method_a)])
    plugin_two = _Plugin("plugin.two", "1.0.0", [_Tool("dup.tool", method_b)])
    runtime.register(plugin_one)

    from openminion.modules.tool.errors import ToolRuntimeError

    with pytest.raises((ValueError, ToolRuntimeError)):
        runtime.register(plugin_two)

    assert plugin_two.init_called is True
    assert plugin_two.shutdown_called is True
    assert [row.tool for row in runtime.list_tools()] == ["dup.tool"]
    assert all(row["plugin_id"] != "plugin.two" for row in runtime.plugin_health())


def test_runtime_event_sink_failures_do_not_fail_invocation():
    runtime = ToolRuntime(event_sink=_BrokenEventSink())
    method = _Method("echo", {"type": "object"}, {"type": "object"}, _echo_handler)
    runtime.register(_Plugin("plugin.echo", "1.0.0", [_Tool("example.echo", method)]))

    result = runtime.invoke(
        ToolInvocation(tool="example.echo", method="echo", args={"value": "hi"}),
        ToolContext(trace_id="trace-5"),
    )
    assert result.status == "ok"
    assert result.data["echo"] == "hi"


def test_runtime_unknown_tool_method_returns_not_found_error():
    sink = MemoryEventSink()
    runtime = ToolRuntime(event_sink=sink)
    result = runtime.invoke(
        ToolInvocation(tool="missing.tool", method="unknown", args={}),
        ToolContext(trace_id="trace-6", event_sink=sink),
    )
    assert result.status == "error"
    assert isinstance(result.error, ToolError)
    assert result.error.code == "NOT_FOUND"
    assert len(sink.events) == 1
    assert sink.events[0]["event_name"] == "tool.missing.tool.unknown.failed"


def test_runtime_sanitizes_non_serializable_args_without_crashing():
    class _Obj:
        pass

    sink = MemoryEventSink()
    runtime = ToolRuntime(event_sink=sink)
    method = _Method("echo", {"type": "object"}, {"type": "object"}, _echo_handler)
    runtime.register(_Plugin("plugin.echo", "1.0.0", [_Tool("example.echo", method)]))

    result = runtime.invoke(
        ToolInvocation(
            tool="example.echo", method="echo", args={"payload": {"obj": _Obj()}}
        ),
        ToolContext(trace_id="trace-7", event_sink=sink),
    )

    assert result.status == "ok"
    assert len(sink.events) == 2
