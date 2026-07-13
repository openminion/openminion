from __future__ import annotations

from typing import Literal

from pydantic import Field

from .policy import resolve_policy
from .schemas import PackPolicyProfile, PolicyDecision, PolicyVerb, StrictModel

EvaluationStatus = Literal["pass", "fail"]


class CapabilityScenario(StrictModel):
    scenario_id: str = Field(min_length=1)
    verb: PolicyVerb
    capability_scope: str = Field(min_length=1)
    expected_decision: PolicyDecision
    evidence_required: bool = False
    evidence_present: bool = False


class CapabilityScenarioResult(StrictModel):
    scenario_id: str
    status: EvaluationStatus
    actual_decision: PolicyDecision
    reason: str = ""


def evaluate_scenario(
    profile: PackPolicyProfile,
    scenario: CapabilityScenario,
) -> CapabilityScenarioResult:
    decision = resolve_policy(
        profile,
        verb=scenario.verb,
        capability_scope=scenario.capability_scope,
    )
    if decision != scenario.expected_decision:
        return CapabilityScenarioResult(
            scenario_id=scenario.scenario_id,
            status="fail",
            actual_decision=decision,
            reason=f"expected {scenario.expected_decision}, got {decision}",
        )
    if scenario.evidence_required and not scenario.evidence_present:
        return CapabilityScenarioResult(
            scenario_id=scenario.scenario_id,
            status="fail",
            actual_decision=decision,
            reason="required evidence was not produced",
        )
    return CapabilityScenarioResult(
        scenario_id=scenario.scenario_id,
        status="pass",
        actual_decision=decision,
    )
