from __future__ import annotations

from typing import Any

from openminion.modules.tool.contracts.model_ids import MODEL_EXEC_RUN
from openminion.modules.tool.registry import ToolSpec
from openminion.services.security.policy import (
    DECISION_REQUIRE_APPROVAL,
    SecurityPolicyContext,
    SecurityPolicyEngine,
    default_internal_actor,
)
from openminion.services.security.tool_execution import (
    build_execution_boundary_policy_adapter,
)
from openminion.tools.exec.schemas import ExecRunArgs


def test_service_tool_policy_surface_is_canonical_module_owner() -> None:
    from openminion.modules.policy.adapters.tool import (
        ExecutionBoundaryPolicyAdapter as canonical,
    )
    from openminion.services.security.tool_execution import (
        ExecutionBoundaryPolicyAdapter as compatibility,
    )

    assert compatibility is canonical


def _exec_spec() -> ToolSpec:
    def _handler(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        del args, ctx
        return {"ok": True}

    return ToolSpec(
        name=MODEL_EXEC_RUN,
        args_model=ExecRunArgs,
        min_scope="READ_ONLY",
        handler=_handler,
        dangerous=True,
    )


def _adapter():
    return build_execution_boundary_policy_adapter(
        policy=SecurityPolicyEngine(),
        actor=default_internal_actor("test-agent"),
        context=SecurityPolicyContext(channel="test"),
    )


def test_exec_discovery_command_denied_with_structured_tool_hint_before_confirmation() -> (
    None
):
    decision = _adapter().evaluate(
        tool_name=MODEL_EXEC_RUN,
        tool_spec=_exec_spec(),
        args={"command": "ls /tmp/project"},
    )

    assert decision.allowed is False
    assert decision.requires_confirm is False
    assert decision.code == "POLICY_DENIED"
    assert decision.details["suggested_tool"] == "file.list_dir"
    assert "file.list_dir" in decision.details["suggested_fix"]


def test_exec_toolchain_discovery_bypasses_high_risk_confirmation() -> None:
    decision = _adapter().evaluate(
        tool_name=MODEL_EXEC_RUN,
        tool_spec=_exec_spec(),
        args={"command": "command -v nasm"},
    )

    assert decision.allowed is True
    assert decision.requires_confirm is False
    assert decision.code == "OK"
    assert decision.reason == "read_only_exec_allowed"
    assert decision.details["action_class"] == "read_only_discovery"


def test_exec_platform_probe_bypasses_high_risk_confirmation() -> None:
    decision = _adapter().evaluate(
        tool_name=MODEL_EXEC_RUN,
        tool_spec=_exec_spec(),
        args={"command": "uname -m"},
    )

    assert decision.allowed is True
    assert decision.requires_confirm is False
    assert decision.code == "OK"


def test_non_discovery_exec_command_keeps_high_risk_confirmation_contract() -> None:
    decision = _adapter().evaluate(
        tool_name=MODEL_EXEC_RUN,
        tool_spec=_exec_spec(),
        args={"command": "python -m pytest -q tests"},
    )

    assert decision.allowed is False
    assert decision.requires_confirm is True
    assert decision.code == "require_approval"
    assert decision.reason == "approval_required_high_risk"
    assert decision.details["decision"] == DECISION_REQUIRE_APPROVAL
