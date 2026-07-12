from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from openminion.modules.tool.base import (
    ToolExecutionContext,
    ToolExecutionResult,
)
from openminion.modules.tool.executor import execute_single_call
from openminion.modules.tool.registry import ToolSpec
from openminion.services.security.blast_radius.wiring import (
    SEAM_AGENT_EXECUTOR_RUNTIME,
    SEAM_AGENT_REQUIRED_LANE_RETRY,
    SEAM_AGENT_SERVICE,
    SEAM_AGENT_TOOL_FALLBACKS,
    SEAM_API_TOOLS,
    SEAM_BRAIN_RUNTIME_TOOL_API,
    SEAM_CLI_CRON,
    SEAM_CLI_TOOLS,
    SEAM_RUNTIME_ENGINE,
    SEAM_TOOL_EXECUTOR,
    build_default_composition_boundary_adapter,
)


@dataclass
class _FakeRegistry:
    _tools: dict


def _build_minimal_tool_spec(
    *,
    name: str = "file.read",
    min_scope: str = "READ_ONLY",
    dangerous: bool = False,
) -> ToolSpec:

    def _handler(args, ctx):
        return {"ok": True, "content": "fake"}

    class _Model:
        @classmethod
        def model_validate(cls, payload):
            return cls()

        def model_dump(self):
            return {}

    return ToolSpec(
        name=name,
        args_model=_Model,  # type: ignore[arg-type]
        min_scope=min_scope,  # type: ignore[arg-type]
        handler=_handler,
        dangerous=dangerous,
    )


def _build_spy_registry(tool_spec: ToolSpec) -> _FakeRegistry:
    return _FakeRegistry(_tools={tool_spec.name: tool_spec})


def _assert_module_source_contains(module_path: str, *snippets: str) -> None:
    source = importlib.import_module(module_path).__file__
    assert source is not None
    with open(source, "r", encoding="utf-8") as fh:
        text = fh.read()
    for snippet in snippets:
        assert snippet in text


def test_path1_brain_runtime_tool_api_wires_adapter_into_per_call_gate() -> None:
    _assert_module_source_contains(
        "openminion.services.brain.post_execution.mixin",
        "SEAM_BRAIN_RUNTIME_TOOL_API",
        "build_default_composition_boundary_adapter",
        "blast_radius_adapter=",
    )


def test_path2_executor_runtime_shares_turn_adapter_between_gate_and_executor() -> None:
    _assert_module_source_contains(
        "openminion.services.agent.execution.runtime",
        "SEAM_AGENT_EXECUTOR_RUNTIME",
        "turn_boundary_adapter",
        "blast_radius_adapter=turn_boundary_adapter",
        "ctx.blast_radius_adapter = turn_boundary_adapter",
    )


def test_path3_registry_executor_invokes_adapter_step_per_call(monkeypatch) -> None:
    spec = _build_minimal_tool_spec(name="file.read", min_scope="READ_ONLY")
    registry = _build_spy_registry(spec)
    adapter = build_default_composition_boundary_adapter(
        seam_id=SEAM_TOOL_EXECUTOR,
    )
    assert adapter.composed_radius() == "read_only"
    assert len(adapter.prior_profiles) == 0

    def _fake_execute_tool_spec_call(*, tool, arguments, context):
        return ToolExecutionResult(
            tool_name=tool.name,
            ok=True,
            content="ok",
            verified=True,
        )

    monkeypatch.setattr(
        "openminion.modules.tool.executor.execute_tool_spec_call_registry_toolspec_runtime",
        _fake_execute_tool_spec_call,
    )

    ctx = ToolExecutionContext(
        channel="test",
        target="test",
        session_id="s",
        blast_radius_adapter=adapter,
    )
    from openminion.modules.llm.providers.base import ProviderToolCall

    result = execute_single_call(
        registry,  # type: ignore[arg-type]
        call=ProviderToolCall(name="file.read", arguments={}, id="c1"),
        context=ctx,
        available_tool_names=("file.read",),
        runtime_binding_policies=None,
    )
    assert result.ok is True
    assert len(adapter.prior_profiles) == 1
    assert adapter.prior_profiles[0].tool_name == "file.read"


def test_path3_registry_executor_skips_step_when_adapter_unwired(
    monkeypatch,
) -> None:
    spec = _build_minimal_tool_spec(name="file.read")
    registry = _build_spy_registry(spec)

    def _fake_execute_tool_spec_call(*, tool, arguments, context):
        return ToolExecutionResult(
            tool_name=tool.name, ok=True, content="ok", verified=True
        )

    monkeypatch.setattr(
        "openminion.modules.tool.executor.execute_tool_spec_call_registry_toolspec_runtime",
        _fake_execute_tool_spec_call,
    )

    ctx = ToolExecutionContext(channel="t", target="t")
    from openminion.modules.llm.providers.base import ProviderToolCall

    result = execute_single_call(
        registry,  # type: ignore[arg-type]
        call=ProviderToolCall(name="file.read", arguments={}, id="c1"),
        context=ctx,
        available_tool_names=("file.read",),
        runtime_binding_policies=None,
    )
    assert result.ok is True


def test_path4_agent_service_attaches_adapter_to_caller_context() -> None:
    _assert_module_source_contains(
        "openminion.services.agent.service",
        "SEAM_AGENT_SERVICE",
        "build_default_composition_boundary_adapter",
        "blast_radius_adapter",
    )


def test_path4_agent_service_runtime_attaches_adapter_on_demand(monkeypatch) -> None:
    from openminion.services.agent.service import AgentService

    captured: dict[str, Any] = {}

    class _FakeTools:
        def execute_calls(self, calls, *, context):
            captured["adapter"] = getattr(context, "blast_radius_adapter", None)
            return SimpleNamespace(results=[])

    service = AgentService.__new__(AgentService)
    service._tools = _FakeTools()  # type: ignore[attr-defined]
    ctx = ToolExecutionContext(channel="t", target="t")
    service._execute_single_tool_call(  # type: ignore[attr-defined]
        tool_name="file.read",
        arguments={},
        context=ctx,
        source="test",
    )
    adapter = captured["adapter"]
    assert adapter is not None
    assert adapter.seam_id == SEAM_AGENT_SERVICE


def test_path5_tool_fallbacks_attaches_adapter_to_context() -> None:
    _assert_module_source_contains(
        "openminion.services.agent.execution.fallbacks",
        "SEAM_AGENT_TOOL_FALLBACKS",
        "build_default_composition_boundary_adapter",
        "blast_radius_adapter=",
    )


def test_path6_required_lane_arg_retry_attaches_adapter_when_missing() -> None:
    _assert_module_source_contains(
        "openminion.services.agent.execution.required.completion",
        "SEAM_AGENT_REQUIRED_LANE_RETRY",
        "build_default_composition_boundary_adapter",
        "state.ctx.blast_radius_adapter",
    )


def test_path7_api_operations_tools_wires_adapter_on_context() -> None:
    _assert_module_source_contains(
        "openminion.api.operations.tools",
        "SEAM_API_TOOLS",
        "build_default_composition_boundary_adapter",
        "blast_radius_adapter=",
    )


def test_path7b_cli_tools_wires_adapter_on_context() -> None:
    _assert_module_source_contains(
        "openminion.cli.commands.tools",
        "SEAM_CLI_TOOLS",
        "build_default_composition_boundary_adapter",
        "blast_radius_adapter=",
    )


def test_path7c_cli_cron_wires_adapter_on_context() -> None:
    _assert_module_source_contains(
        "openminion.cli.commands.cron",
        "SEAM_CLI_CRON",
        "build_default_composition_boundary_adapter",
        "blast_radius_adapter=",
    )


def test_path8_runtime_engine_invokes_adapter_step_on_execute() -> None:
    from openminion.base.runtime.constants import (
        RUNTIME_POLICY_OUTCOME_ALLOW_WITH_CONSTRAINTS,
    )
    from openminion.base.runtime.interfaces import RUNTIME_INTERFACE_VERSION
    from openminion.base.runtime.sandbox import ExecResult, ExecSpec
    from openminion.services.runtime.engine import (
        PolicyDecision,
        RuntimeContext,
        RuntimeEngine,
        ToolCall,
    )

    class _AllowPolicy:
        contract_version = RUNTIME_INTERFACE_VERSION

        def evaluate(self, tool_call, ctx):  # noqa: ARG002
            return PolicyDecision(
                outcome=RUNTIME_POLICY_OUTCOME_ALLOW_WITH_CONSTRAINTS,
                policy_request_id="r-1",
            )

    class _NoopRunner:
        name = "noop"
        contract_version = RUNTIME_INTERFACE_VERSION

        def run_exec(self, spec, sandbox):  # noqa: ARG002
            return ExecResult(returncode=0, stdout="", stderr="")

        def fs_write(self, spec, sandbox):  # noqa: ARG002
            return None

        def fs_delete(self, spec, sandbox):  # noqa: ARG002
            return None

        def net_fetch(self, spec, sandbox):  # noqa: ARG002
            return None

    adapter = build_default_composition_boundary_adapter(
        seam_id=SEAM_RUNTIME_ENGINE,
    )
    engine = RuntimeEngine(
        runner=_NoopRunner(),
        policy=_AllowPolicy(),
        blast_radius_adapter=adapter,
    )
    tool_call = ToolCall(
        tool_call_id="t1",
        name="exec.run",
        kind="exec",
        spec=ExecSpec(cmd=["/bin/true"]),
    )
    ctx = RuntimeContext(
        trace_id="tr",
        agent_id="a",
        session_id="s",
        run_id="r",
        workspace_root="/tmp",
    )
    engine.execute_tool_call(tool_call, ctx)
    assert len(adapter.prior_profiles) == 1
    assert adapter.prior_profiles[0].tool_name == "exec.run"
    assert adapter.prior_profiles[0].blast_radius == "code_execution"


def test_prior_profiles_accumulate_across_multi_call_turn(monkeypatch) -> None:
    spec_read = _build_minimal_tool_spec(name="file.read", min_scope="READ_ONLY")
    spec_write = _build_minimal_tool_spec(
        name="file.write", min_scope="WRITE_SAFE", dangerous=True
    )
    registry = _FakeRegistry(
        _tools={spec_read.name: spec_read, spec_write.name: spec_write}
    )

    def _fake_execute_tool_spec_call(*, tool, arguments, context):
        return ToolExecutionResult(
            tool_name=tool.name, ok=True, content="ok", verified=True
        )

    monkeypatch.setattr(
        "openminion.modules.tool.executor.execute_tool_spec_call_registry_toolspec_runtime",
        _fake_execute_tool_spec_call,
    )

    adapter = build_default_composition_boundary_adapter(
        seam_id=SEAM_TOOL_EXECUTOR,
    )
    from openminion.modules.llm.providers.base import ProviderToolCall

    ctx = ToolExecutionContext(channel="t", target="t", blast_radius_adapter=adapter)
    execute_single_call(
        registry,  # type: ignore[arg-type]
        call=ProviderToolCall(name="file.read", arguments={}, id="c1"),
        context=ctx,
        available_tool_names=("file.read", "file.write"),
        runtime_binding_policies=None,
    )
    execute_single_call(
        registry,  # type: ignore[arg-type]
        call=ProviderToolCall(name="file.write", arguments={}, id="c2"),
        context=ctx,
        available_tool_names=("file.read", "file.write"),
        runtime_binding_policies=None,
    )
    assert len(adapter.prior_profiles) == 2
    assert adapter.prior_profiles[0].blast_radius == "read_only"
    assert adapter.prior_profiles[1].blast_radius == "local_mutation"
    assert adapter.composed_radius() == "local_mutation"


def test_default_policy_is_structural_and_frozen() -> None:
    adapter = build_default_composition_boundary_adapter(seam_id="seam.x")
    assert adapter.policy.frozen is True
    assert adapter.policy.forbidden_transitions == frozenset()
    assert adapter.policy.escalation_required_transitions == frozenset()
    assert adapter.policy.max_radius_per_turn is None


def test_seam_ids_are_static_labels() -> None:
    expected = {
        SEAM_BRAIN_RUNTIME_TOOL_API,
        SEAM_AGENT_EXECUTOR_RUNTIME,
        SEAM_TOOL_EXECUTOR,
        SEAM_AGENT_SERVICE,
        SEAM_AGENT_TOOL_FALLBACKS,
        SEAM_AGENT_REQUIRED_LANE_RETRY,
        SEAM_API_TOOLS,
        SEAM_CLI_TOOLS,
        SEAM_CLI_CRON,
        SEAM_RUNTIME_ENGINE,
    }
    assert all(isinstance(seam, str) and seam for seam in expected)
    assert len(expected) == 10
