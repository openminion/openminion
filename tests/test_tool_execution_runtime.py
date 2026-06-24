from __future__ import annotations

from typing import Any

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.artifact.models import sha_to_ref


class _ArtifactCtlRef:
    def __init__(self, sha256: str) -> None:
        self.sha256 = sha256
        self.ref = sha_to_ref(sha256)


class _RecordingArtifactCtl:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def ingest_bytes(self, **kwargs: Any) -> _ArtifactCtlRef:
        self.calls.append(dict(kwargs))
        return _ArtifactCtlRef("a" * 64)


class _FailingArtifactCtl:
    def ingest_bytes(self, **kwargs: Any) -> Any:
        raise RuntimeError("cas offline")


class _RecordingTelemetryCtl:
    def __init__(self) -> None:
        self.counters: list[dict[str, Any]] = []

    def emit_module_counter(self, *args: Any, **kwargs: Any) -> None:
        self.counters.append({"args": args, "kwargs": kwargs})


def test_tool_executor_emits_start_and_success_counters() -> None:
    telemetry = _RecordingTelemetryCtl()

    def handler(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        del args, ctx
        return {"ok": True, "content": "ok"}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="pphh.echo",
            args_model=dict,
            min_scope="READ_ONLY",
            handler=handler,
        )
    )

    result = registry.execute_calls(
        [ProviderToolCall(name="pphh.echo", arguments={}, id="1", source="test")],
        context=ToolExecutionContext(
            channel="console",
            target="cli-chat",
            session_id="session-1",
            metadata={"turn_id": "turn-1"},
            telemetryctl=telemetry,
        ),
    ).results[0]

    assert result.ok
    counter_names = [call["args"][3] for call in telemetry.counters]
    assert counter_names == ["tool_execution_started", "tool_execution_success"]


def test_tool_executor_emits_failure_counter_for_unknown_tool() -> None:
    telemetry = _RecordingTelemetryCtl()
    registry = ToolRegistry()

    result = registry.execute_calls(
        [ProviderToolCall(name="missing.tool", arguments={}, id="1", source="test")],
        context=ToolExecutionContext(
            channel="console",
            target="cli-chat",
            session_id="session-1",
            metadata={"turn_id": "turn-1"},
            telemetryctl=telemetry,
        ),
    ).results[0]

    assert not result.ok
    counter_names = [call["args"][3] for call in telemetry.counters]
    assert counter_names == ["tool_execution_started", "tool_execution_failure"]


def test_registry_wraps_legacy_ctx_args_toolspec_handler() -> None:
    calls: list[tuple[Any, Any]] = []

    def legacy_handler(ctx: Any, args: dict[str, Any]) -> dict[str, Any]:
        calls.append((ctx, args))
        return {"ok": True, "content": "legacy-ok", "data": {"order": "ctx_args"}}

    spec = ToolSpec(
        name="legacy.echo",
        args_model=dict,
        min_scope="READ_ONLY",
        handler=legacy_handler,
    )
    registry = ToolRegistry()
    registry.register(spec)

    assert bool(getattr(spec.handler, "__openminion_runtime_wrapped__", False))

    result = registry.execute_calls(
        [
            ProviderToolCall(
                name="legacy.echo", arguments={"x": 1}, id="1", source="test"
            )
        ],
        context=ToolExecutionContext(channel="console", target="cli-chat", metadata={}),
    ).results[0]

    assert result.ok
    assert result.content == "legacy-ok"
    assert result.data.get("order") == "ctx_args"
    assert len(calls) == 1
    assert isinstance(calls[0][1], dict)


def test_registry_wraps_canonical_args_ctx_toolspec_handler() -> None:
    calls: list[tuple[Any, Any]] = []

    def canonical_handler(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        calls.append((args, ctx))
        return {"ok": True, "content": "canonical-ok", "data": {"order": "args_ctx"}}

    spec = ToolSpec(
        name="canonical.echo",
        args_model=dict,
        min_scope="READ_ONLY",
        handler=canonical_handler,
    )
    registry = ToolRegistry()
    registry.register(spec)

    assert bool(getattr(spec.handler, "__openminion_runtime_wrapped__", False))

    result = registry.execute_calls(
        [
            ProviderToolCall(
                name="canonical.echo",
                arguments={"x": 1},
                id="1",
                source="test",
            )
        ],
        context=ToolExecutionContext(channel="console", target="cli-chat", metadata={}),
    ).results[0]

    assert result.ok
    assert result.content == "canonical-ok"
    assert result.data.get("order") == "args_ctx"
    assert len(calls) == 1
    assert isinstance(calls[0][0], dict)


def test_registry_toolspec_runtime_durable_artifact_emits_canonical_ref(
    monkeypatch,
    tmp_path,
) -> None:
    from openminion.modules.tool.runtime import registry_toolspec

    artifactctl = _RecordingArtifactCtl()
    monkeypatch.setattr(
        registry_toolspec,
        "create_default_artifactctl",
        lambda: artifactctl,
    )

    def handler(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        artifact = ctx.write_artifact(
            "artifacts/out.txt",
            str(args["payload"]).encode("utf-8"),
            "text/plain",
            durable=True,
        )
        return {
            "ok": True,
            "content": "artifact written",
            "data": {
                "artifact_path": artifact.path,
                "canonical_ref": artifact.canonical_ref,
            },
        }

    spec = ToolSpec(
        name="durable.echo",
        args_model=dict,
        min_scope="READ_ONLY",
        handler=handler,
    )
    registry = ToolRegistry()
    registry.register(spec)

    result = registry.execute_calls(
        [
            ProviderToolCall(
                name="durable.echo",
                arguments={"payload": "hello"},
                id="durable-1",
                source="test",
            )
        ],
        context=ToolExecutionContext(
            channel="console",
            target="cli-chat",
            session_id="sess-artifact",
            metadata={"workspace_root": str(tmp_path / "workspace")},
        ),
    ).results[0]

    assert result.ok
    assert result.data["canonical_ref"] == sha_to_ref("a" * 64)
    assert artifactctl.calls
    assert artifactctl.calls[0]["session_id"] == "sess-artifact"
    assert artifactctl.calls[0]["original_name"] == "out.txt"


def test_registry_toolspec_runtime_durable_artifact_cas_failure_keeps_local_only(
    monkeypatch,
    tmp_path,
) -> None:
    from openminion.modules.tool.runtime import registry_toolspec

    monkeypatch.setattr(
        registry_toolspec,
        "create_default_artifactctl",
        lambda: _FailingArtifactCtl(),
    )

    def handler(_args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        artifact = ctx.write_artifact(
            "artifacts/out.txt",
            b"hello",
            "text/plain",
            durable=True,
        )
        return {
            "ok": True,
            "content": "artifact written",
            "data": {
                "artifact_path": artifact.path,
                "canonical_ref": artifact.canonical_ref,
                "logs": [item.model_dump() for item in ctx.logs],
            },
        }

    spec = ToolSpec(
        name="durable.fail",
        args_model=dict,
        min_scope="READ_ONLY",
        handler=handler,
    )
    registry = ToolRegistry()
    registry.register(spec)

    result = registry.execute_calls(
        [
            ProviderToolCall(
                name="durable.fail",
                arguments={},
                id="durable-2",
                source="test",
            )
        ],
        context=ToolExecutionContext(
            channel="console",
            target="cli-chat",
            session_id="sess-artifact",
            metadata={"workspace_root": str(tmp_path / "workspace")},
        ),
    ).results[0]

    assert result.ok
    assert result.data["artifact_path"] == "artifacts/out.txt"
    assert result.data["canonical_ref"] is None
    assert not any(
        str(value or "").startswith("artifact://sha256/")
        for value in result.data.values()
        if not isinstance(value, list)
    )
    assert any(
        "CAS ingest failed" in str(item.get("msg", "")) for item in result.data["logs"]
    )


def test_registry_toolspec_runtime_carries_explicit_memory_service() -> None:
    sentinel = object()

    def handler(_args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "content": "memory-seam-ok",
            "data": {"same_service": ctx.memory_service is sentinel},
        }

    spec = ToolSpec(
        name="memory.runtime_probe",
        args_model=dict,
        min_scope="READ_ONLY",
        handler=handler,
    )
    registry = ToolRegistry()
    registry.register(spec)

    result = registry.execute_calls(
        [
            ProviderToolCall(
                name="memory.runtime_probe",
                arguments={},
                id="memory-probe-1",
                source="test",
            )
        ],
        context=ToolExecutionContext(
            channel="console",
            target="cli-chat",
            metadata={},
            memory_service=sentinel,
        ),
    ).results[0]

    assert result.ok
    assert result.data["same_service"] is True
