from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProjectEffectStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    NON_REVERSIBLE = "non_reversible"


class ProjectEffectReplayDecision(StrEnum):
    ALLOW = "allow"
    REUSE_EXISTING = "reuse_existing"
    BLOCK_DUPLICATE = "block_duplicate"
    BLOCK_STALE_PRECONDITION = "block_stale_precondition"


class ProjectEffectRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effect_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    actor_ref: str = Field(min_length=1)
    capability_ref: str = Field(min_length=1)
    precondition_refs: tuple[str, ...] = Field(min_length=1)
    approval_ref: str | None = None
    result_ref: str | None = None
    verification_refs: tuple[str, ...] = ()
    rollback_ref: str | None = None
    non_reversible_reason: str | None = None
    status: ProjectEffectStatus = ProjectEffectStatus.STARTED

    @model_validator(mode="after")
    def _terminal_effects_need_result_and_reversal_posture(
        self,
    ) -> "ProjectEffectRecord":
        if (
            self.status
            in {
                ProjectEffectStatus.SUCCEEDED,
                ProjectEffectStatus.NON_REVERSIBLE,
            }
            and not self.result_ref
        ):
            raise ValueError("successful effects require result_ref")
        if self.status == ProjectEffectStatus.SUCCEEDED and not (
            self.rollback_ref or self.non_reversible_reason
        ):
            raise ValueError(
                "successful effects require rollback_ref or non_reversible_reason"
            )
        if (
            self.status == ProjectEffectStatus.NON_REVERSIBLE
            and not self.non_reversible_reason
        ):
            raise ValueError("non-reversible effects require non_reversible_reason")
        if self.status == ProjectEffectStatus.ROLLED_BACK and not self.rollback_ref:
            raise ValueError("rolled-back effects require rollback_ref")
        return self


class ProjectEffectReplayResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ProjectEffectReplayDecision
    reason: str
    existing_effect_ref: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == ProjectEffectReplayDecision.ALLOW


def evaluate_project_effect_replay(
    existing: ProjectEffectRecord | None,
    *,
    idempotency_key: str,
    precondition_refs: tuple[str, ...],
) -> ProjectEffectReplayResult:
    if existing is None:
        return ProjectEffectReplayResult(
            decision=ProjectEffectReplayDecision.ALLOW,
            reason="no_existing_effect",
        )
    if existing.idempotency_key != idempotency_key:
        return ProjectEffectReplayResult(
            decision=ProjectEffectReplayDecision.ALLOW,
            reason="different_idempotency_key",
        )
    if existing.precondition_refs != precondition_refs:
        return ProjectEffectReplayResult(
            decision=ProjectEffectReplayDecision.BLOCK_STALE_PRECONDITION,
            reason="precondition_refs_changed",
            existing_effect_ref=existing.effect_id,
        )
    if existing.status in {
        ProjectEffectStatus.SUCCEEDED,
        ProjectEffectStatus.NON_REVERSIBLE,
        ProjectEffectStatus.ROLLED_BACK,
    }:
        return ProjectEffectReplayResult(
            decision=ProjectEffectReplayDecision.REUSE_EXISTING,
            reason="effect_already_completed",
            existing_effect_ref=existing.effect_id,
        )
    if existing.status == ProjectEffectStatus.STARTED:
        return ProjectEffectReplayResult(
            decision=ProjectEffectReplayDecision.BLOCK_DUPLICATE,
            reason="effect_in_progress",
            existing_effect_ref=existing.effect_id,
        )
    return ProjectEffectReplayResult(
        decision=ProjectEffectReplayDecision.ALLOW,
        reason="previous_effect_failed",
        existing_effect_ref=existing.effect_id,
    )


__all__ = [
    "ProjectEffectRecord",
    "ProjectEffectReplayDecision",
    "ProjectEffectReplayResult",
    "ProjectEffectStatus",
    "evaluate_project_effect_replay",
]
