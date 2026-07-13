from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from openminion.base.time import utc_now

from .schemas import OperationRisk, OperationTarget, StrictModel

PolicyOutcome = Literal["allow", "ask", "deny"]


class OperationPolicyDecision(StrictModel):
    outcome: PolicyOutcome
    reason: str = Field(min_length=1)


class BreakGlassGrant(StrictModel):
    target_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    expires_at: str = Field(min_length=1)

    def permits(self, target: OperationTarget) -> bool:
        try:
            expires_at = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return False
        return self.target_id == target.target_id and expires_at > utc_now()


def decide_operation_policy(
    target: OperationTarget,
    *,
    risk: OperationRisk,
    privileged: bool = False,
    headless: bool = False,
    profile_known: bool = True,
    breakglass: BreakGlassGrant | None = None,
) -> OperationPolicyDecision:
    if not target.enabled:
        return OperationPolicyDecision(
            outcome="deny",
            reason="operation target is disabled",
        )
    if not profile_known:
        return OperationPolicyDecision(
            outcome="deny",
            reason="unknown operation profile",
        )
    if privileged:
        return OperationPolicyDecision(
            outcome="deny",
            reason="privileged operations are outside the bounded pack",
        )
    if risk == "read":
        return OperationPolicyDecision(outcome="allow", reason="read-only observation")
    if headless:
        return OperationPolicyDecision(
            outcome="deny",
            reason="write-safe operations require an interactive approval surface",
        )
    if target.environment == "production":
        if breakglass is not None and breakglass.permits(target):
            return OperationPolicyDecision(
                outcome="ask",
                reason="breakglass action requires explicit approval",
            )
        return OperationPolicyDecision(
            outcome="deny",
            reason="production or privileged mutation requires breakglass",
        )
    return OperationPolicyDecision(
        outcome="ask",
        reason="write-safe action requires explicit approval",
    )
