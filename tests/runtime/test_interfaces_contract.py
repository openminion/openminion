from __future__ import annotations

import pytest

from openminion.services.runtime.engine import (
    PolicyDecision,
    RuntimeContext,
    RuntimeEngine,
    ToolCall,
)
from openminion.base.runtime.interfaces import (
    RUNTIME_INTERFACE_VERSION,
    ensure_runtime_component_compatibility,
)
from openminion.services.runtime.manager import AgentRuntimeManager
from openminion.base.runtime.runners import LocalRunner
from openminion.base.runtime.sandbox import ExecResult, FsResult


class _AllowPolicy:
    contract_version = RUNTIME_INTERFACE_VERSION

    def evaluate(self, tool_call: ToolCall, ctx: RuntimeContext) -> PolicyDecision:
        del tool_call, ctx
        return PolicyDecision(outcome="allow", policy_request_id="policy-1")


def test_runtime_components_satisfy_contracts() -> None:
    runner = LocalRunner()
    policy = _AllowPolicy()
    engine = RuntimeEngine(runner=runner, policy=policy)

    def _turn_executor(req, emit, cancel):
        del req, emit, cancel
        raise RuntimeError("not used in this test")

    manager = AgentRuntimeManager(turn_executor=_turn_executor)

    ensure_runtime_component_compatibility(runner, component_type="runner")
    ensure_runtime_component_compatibility(policy, component_type="policy")
    ensure_runtime_component_compatibility(engine, component_type="engine")
    ensure_runtime_component_compatibility(manager, component_type="manager")


def test_engine_rejects_policy_without_contract_version() -> None:
    class _PolicyNoContract:
        def evaluate(self, tool_call: ToolCall, ctx: RuntimeContext) -> PolicyDecision:
            del tool_call, ctx
            return PolicyDecision(outcome="allow", policy_request_id="policy-x")

    with pytest.raises(TypeError):
        RuntimeEngine(runner=LocalRunner(), policy=_PolicyNoContract())


def test_interface_validator_rejects_broken_runner() -> None:
    class _BrokenRunner:
        contract_version = "v1"
        name = "broken"

        def run_exec(self, spec, sandbox):
            del spec, sandbox
            return ExecResult(returncode=0, stdout="", stderr="")

        def fs_write(self, spec, sandbox):
            del spec, sandbox
            return FsResult(success=True, path="/tmp")

    with pytest.raises(TypeError):
        ensure_runtime_component_compatibility(_BrokenRunner(), component_type="runner")
