from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openminion.modules.task.autonomy import TestEvidence
from openminion.modules.task.runtime.lifecycle import TaskManager

from .checkpoints import (
    advance_repository_lifecycle_payload,
    load_latest_project_checkpoint,
    plan_checkpoint_payload,
)
from .models import ProjectCheckpoint, ProjectCycleDecision, ProjectRun
from .turn import ProjectTurnResult


_PROJECT_EFFECTS_PAYLOAD_KEY = "project_effects"
_PROJECT_EFFECT_RECEIPTS_PAYLOAD_KEY = "project_effect_receipts"


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


def load_project_effect_record(
    task_manager: TaskManager,
    *,
    task_id: str,
    effect_id: str,
) -> ProjectEffectRecord | None:
    checkpoint = load_latest_project_checkpoint(task_manager, task_id=task_id)
    if checkpoint is None:
        raise KeyError(f"project checkpoint not found: {task_id}")
    raw_effects = checkpoint.payload.get(_PROJECT_EFFECTS_PAYLOAD_KEY)
    if not isinstance(raw_effects, Mapping):
        return None
    raw_effect = raw_effects.get(effect_id)
    if not isinstance(raw_effect, Mapping):
        return None
    return ProjectEffectRecord.model_validate(raw_effect)


def load_project_effect_receipt(
    task_manager: TaskManager,
    *,
    task_id: str,
    effect_id: str,
) -> dict[str, Any] | None:
    checkpoint = load_latest_project_checkpoint(task_manager, task_id=task_id)
    if checkpoint is None:
        raise KeyError(f"project checkpoint not found: {task_id}")
    raw_receipts = checkpoint.payload.get(_PROJECT_EFFECT_RECEIPTS_PAYLOAD_KEY)
    if not isinstance(raw_receipts, Mapping):
        return None
    raw_receipt = raw_receipts.get(effect_id)
    return dict(raw_receipt) if isinstance(raw_receipt, Mapping) else None


def project_effect_checkpoint_payload(
    checkpoint: ProjectCheckpoint,
) -> dict[str, object]:
    return {
        key: checkpoint.payload[key]
        for key in (
            _PROJECT_EFFECTS_PAYLOAD_KEY,
            _PROJECT_EFFECT_RECEIPTS_PAYLOAD_KEY,
        )
        if key in checkpoint.payload
    }


def project_cycle_checkpoint_payload(
    checkpoint: ProjectCheckpoint,
    project_run: ProjectRun,
    *,
    turn: ProjectTurnResult,
    verification: tuple[TestEvidence, ...],
    verification_closure: dict[str, object],
    decision: ProjectCycleDecision,
    decision_reason: str,
    replan_count: int,
    waiting_for_checks: bool,
) -> dict[str, object]:
    turn_error = turn.error
    return {
        **project_effect_checkpoint_payload(checkpoint),
        "decision": decision.value,
        "summary": turn.summary,
        "gateway_run_id": turn.gateway_run_id,
        "verification": [item.model_dump(mode="json") for item in verification],
        "verification_closure": verification_closure,
        "condition": turn.condition.value,
        "decision_reason": decision_reason,
        **({"detail_code": "waiting_for_checks"} if waiting_for_checks else {}),
        "replan_count": replan_count,
        **plan_checkpoint_payload(checkpoint, turn),
        **advance_repository_lifecycle_payload(
            checkpoint,
            project_run,
            turn=turn,
            verification_count=len(verification),
            next_action=decision.value,
        ),
        **({"error": turn_error.to_dict()} if turn_error else {}),
    }


def save_project_effect_record(
    task_manager: TaskManager,
    effect: ProjectEffectRecord,
    *,
    receipt: Mapping[str, Any] | None = None,
) -> ProjectEffectRecord:
    checkpoint = load_latest_project_checkpoint(task_manager, task_id=effect.task_id)
    if checkpoint is None:
        raise KeyError(f"project checkpoint not found: {effect.task_id}")

    payload = dict(checkpoint.payload)
    raw_effects = payload.get(_PROJECT_EFFECTS_PAYLOAD_KEY)
    effects = dict(raw_effects) if isinstance(raw_effects, Mapping) else {}
    effects[effect.effect_id] = effect.model_dump(mode="json")
    payload[_PROJECT_EFFECTS_PAYLOAD_KEY] = effects
    if receipt is not None:
        raw_receipts = payload.get(_PROJECT_EFFECT_RECEIPTS_PAYLOAD_KEY)
        receipts = dict(raw_receipts) if isinstance(raw_receipts, Mapping) else {}
        receipts[effect.effect_id] = dict(receipt)
        payload[_PROJECT_EFFECT_RECEIPTS_PAYLOAD_KEY] = receipts

    effect_refs = tuple(
        dict.fromkeys((*checkpoint.project_run.effect_refs, effect.effect_id))
    )
    updated = checkpoint.model_copy(
        update={
            "project_run": checkpoint.project_run.model_copy(
                update={"effect_refs": effect_refs}
            ),
            "payload": payload,
        }
    )
    task_manager.save_checkpoint(
        effect.task_id,
        checkpoint.checkpoint_id,
        updated.model_dump(mode="json"),
    )
    return effect


__all__ = [
    "ProjectEffectRecord",
    "ProjectEffectReplayDecision",
    "ProjectEffectReplayResult",
    "ProjectEffectStatus",
    "evaluate_project_effect_replay",
    "load_project_effect_receipt",
    "load_project_effect_record",
    "project_effect_checkpoint_payload",
    "project_cycle_checkpoint_payload",
    "save_project_effect_record",
]
