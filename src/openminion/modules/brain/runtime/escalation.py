from dataclasses import dataclass
from typing import Literal

ActionRiskTier = Literal["silent", "notify", "approve", "halt"]


@dataclass(frozen=True)
class EscalationDecision:
    risk_tier: ActionRiskTier
    reason: str
    requires_user_confirm: bool = False


@dataclass(frozen=True)
class ApprovalResponse:
    status: Literal["pending", "approved", "denied"]
    reason: str


@dataclass(frozen=True)
class PendingApprovalDecision:
    risk_tier: ActionRiskTier
    response: ApprovalResponse


def goal_policy_risk_tier(
    *, allowed: bool, requires_user_confirm: bool
) -> ActionRiskTier:
    if allowed and not requires_user_confirm:
        return "silent"
    if requires_user_confirm:
        return "approve"
    return "halt"


def pending_approval_decision(
    *, declared_risk_tier: ActionRiskTier, reason: str
) -> PendingApprovalDecision:
    return PendingApprovalDecision(
        risk_tier=declared_risk_tier,
        response=ApprovalResponse(status="pending", reason=reason),
    )


__all__ = [
    "ActionRiskTier",
    "ApprovalResponse",
    "EscalationDecision",
    "PendingApprovalDecision",
    "goal_policy_risk_tier",
    "pending_approval_decision",
]
