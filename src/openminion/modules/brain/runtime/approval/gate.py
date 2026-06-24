"""Destructive-action approval gate helpers."""

from dataclasses import dataclass
from typing import Any

from .registry import ApprovalCriteriaRegistry, default_criteria_registry
from .protocol import ActionApprovalVerifier, ApprovalCriteria, ApprovalVerdict


@dataclass(frozen=True)
class ActionApprovalConfig:
    """Operator-tunable approval-gate config."""

    enabled: bool = False
    model_name: str = "claude-haiku-3.5"
    timeout_seconds: int = 3
    escalate_on_timeout: bool = True


def gate_destructive_action(
    *,
    tool_id: str,
    action: str,
    action_args: dict[str, Any],
    state: dict[str, Any],
    verifier: ActionApprovalVerifier,
    registry: ApprovalCriteriaRegistry | None = None,
    config: ActionApprovalConfig | None = None,
) -> ApprovalVerdict:
    """Run the verifier against the named `(tool_id, action)` pair."""

    cfg = config or ActionApprovalConfig()
    if not cfg.enabled:
        return ApprovalVerdict(
            decision="approve",
            rationale="action_approval_verifier_disabled",
            model="",
            latency_ms=0,
        )

    active_registry = registry or default_criteria_registry()
    criteria: ApprovalCriteria | None = active_registry.get(tool_id, action)
    if criteria is None:
        return ApprovalVerdict(
            decision="escalate",
            rationale="missing_approval_criteria",
            model="",
            latency_ms=0,
        )
    return verifier.verify(
        action={"tool_id": tool_id, "action": action, "args": dict(action_args)},
        state=dict(state),
        criteria=criteria,
    )


__all__ = ["ActionApprovalConfig", "gate_destructive_action"]
