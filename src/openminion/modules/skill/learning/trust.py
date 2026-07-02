"""Execution-trust ladder for learned skills."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .shapes import WorkflowTrustState

SkillRunOutcome = Literal["success", "fail", "partial"]

_PROMOTION_ORDER: dict[WorkflowTrustState, int] = {
    "candidate": 0,
    "pending_review": 1,
    "catalog_applied": 2,
    "suggest_only": 3,
    "trusted_for_manual": 4,
    "trusted_for_low_risk": 5,
    "execution_downgraded": -1,
    "catalog_review_required": -2,
}


class SkillExecutionTrustRecord(BaseModel):
    """Execution trust for one learned skill, separate from catalog status."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    shape_id: str
    trust_state: WorkflowTrustState = "candidate"
    risk_level: Literal["low", "medium", "high"] = "low"
    success_count_after_apply: int = Field(default=0, ge=0)
    failure_count_after_apply: int = Field(default=0, ge=0)
    partial_count_after_apply: int = Field(default=0, ge=0)
    active_replay_regression: bool = False
    evidence_refs: list[str] = Field(default_factory=list)


class TrustTransitionError(ValueError):
    """Raised when trust promotion lacks enough evidence."""


def promote_execution_trust(
    record: SkillExecutionTrustRecord,
    target_state: WorkflowTrustState,
) -> SkillExecutionTrustRecord:
    """Promote execution trust only when evidence gates are satisfied."""

    if target_state == "suggest_only":
        if _PROMOTION_ORDER[record.trust_state] < _PROMOTION_ORDER["catalog_applied"]:
            raise TrustTransitionError("suggest_only_requires_catalog_applied")
        return record.model_copy(update={"trust_state": target_state})
    if target_state == "trusted_for_manual":
        if record.success_count_after_apply < 1:
            raise TrustTransitionError("manual_trust_requires_successful_use")
        if record.active_replay_regression:
            raise TrustTransitionError("manual_trust_blocked_by_replay_regression")
        return record.model_copy(update={"trust_state": target_state})
    if target_state == "trusted_for_low_risk":
        if record.risk_level != "low":
            raise TrustTransitionError("low_risk_trust_requires_low_risk_skill")
        if record.success_count_after_apply < 2:
            raise TrustTransitionError("low_risk_trust_requires_two_successes")
        if record.failure_count_after_apply or record.active_replay_regression:
            raise TrustTransitionError("low_risk_trust_blocked_by_regression")
        return record.model_copy(update={"trust_state": target_state})
    current_rank = _PROMOTION_ORDER.get(record.trust_state, -99)
    target_rank = _PROMOTION_ORDER.get(target_state, -99)
    if target_rank < current_rank:
        raise TrustTransitionError("use_downgrade_for_lower_trust_state")
    return record.model_copy(update={"trust_state": target_state})


def downgrade_execution_trust(
    record: SkillExecutionTrustRecord,
    *,
    reason_ref: str,
    review_required: bool = False,
) -> SkillExecutionTrustRecord:
    """Disable automatic execution without deleting the catalog skill."""

    evidence_refs = [*record.evidence_refs]
    if reason_ref and reason_ref not in evidence_refs:
        evidence_refs.append(reason_ref)
    return record.model_copy(
        update={
            "trust_state": (
                "catalog_review_required" if review_required else "execution_downgraded"
            ),
            "evidence_refs": evidence_refs,
        }
    )


def record_skill_run_outcome(
    record: SkillExecutionTrustRecord,
    *,
    outcome: SkillRunOutcome,
    evidence_ref: str,
    replay_regression: bool = False,
) -> SkillExecutionTrustRecord:
    """Update trust counters from ``skill.log_run`` outcome evidence."""

    refs = [*record.evidence_refs]
    if evidence_ref and evidence_ref not in refs:
        refs.append(evidence_ref)
    updates = {"evidence_refs": refs}
    if outcome == "success":
        updates["success_count_after_apply"] = record.success_count_after_apply + 1
    elif outcome == "partial":
        updates["partial_count_after_apply"] = record.partial_count_after_apply + 1
    else:
        updates["failure_count_after_apply"] = record.failure_count_after_apply + 1
    if replay_regression:
        updates["active_replay_regression"] = True
    updated = record.model_copy(update=updates)
    if updated.failure_count_after_apply >= 2 or updated.active_replay_regression:
        return downgrade_execution_trust(updated, reason_ref=evidence_ref)
    return updated


__all__ = (
    "SkillExecutionTrustRecord",
    "SkillRunOutcome",
    "TrustTransitionError",
    "downgrade_execution_trust",
    "promote_execution_trust",
    "record_skill_run_outcome",
)
