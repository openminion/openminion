"""Bridge typed self-improvement decisions into memory candidate staging."""

from typing import TYPE_CHECKING, Any, Mapping

from pydantic import BaseModel, ConfigDict

from openminion.base.constants import STATE_KEY_SOURCE_OUTCOME
from openminion.modules.brain.runtime.memory import (
    stage_self_improvement_candidate,
)

from .contracts import ImprovementDecision, OnlineImprovementEval

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openminion.modules.brain.schemas import WorkingState
    from openminion.modules.brain.runner import BrainRunner


class SelfImprovementStageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str | None = None
    skipped_reason: str | None = None
    action: str
    memory_kind: str


def stage_improvement_decision(
    runner: "BrainRunner",
    *,
    state: "WorkingState",
    decision: ImprovementDecision | Mapping[str, Any],
    evaluation: OnlineImprovementEval | Mapping[str, Any],
) -> SelfImprovementStageResult:
    decision_obj = (
        decision
        if isinstance(decision, ImprovementDecision)
        else ImprovementDecision.model_validate(decision)
    )
    evaluation_obj = (
        evaluation
        if isinstance(evaluation, OnlineImprovementEval)
        else OnlineImprovementEval.model_validate(evaluation)
    )
    if decision_obj.action not in {"stage_lesson", "stage_candidate"}:
        return _skipped(decision_obj, "action_not_stageable")
    if decision_obj.memory_kind == "none":
        return _skipped(decision_obj, "missing_memory_kind")
    if getattr(runner, "memory_api", None) is None:
        return _skipped(decision_obj, "memory_api_unavailable")

    candidate_id = stage_self_improvement_candidate(
        runner,
        state=state,
        record_type=_record_type_for_memory_kind(decision_obj.memory_kind),
        title=_candidate_title(decision_obj, evaluation_obj),
        content={
            "action": decision_obj.action,
            "rationale_code": decision_obj.rationale_code,
            "memory_kind": decision_obj.memory_kind,
            "outcome_status": evaluation_obj.outcome_status,
            "failure_reason_code": evaluation_obj.failure_reason_code,
            "trace_id": evaluation_obj.trace_id,
            "attempt_id": evaluation_obj.attempt_id,
        },
        tags=_candidate_tags(decision_obj, evaluation_obj),
        evidence_refs=list(evaluation_obj.evidence_refs),
        confidence=float(decision_obj.confidence),
        meta={
            "source_self_improvement": True,
            "source_improvement_action": decision_obj.action,
            "source_memory_kind": decision_obj.memory_kind,
            STATE_KEY_SOURCE_OUTCOME: evaluation_obj.outcome_status,
            "source_trace_id": evaluation_obj.trace_id,
            "source_attempt_id": evaluation_obj.attempt_id,
        },
    )
    return SelfImprovementStageResult(
        candidate_id=candidate_id,
        skipped_reason=None,
        action=decision_obj.action,
        memory_kind=decision_obj.memory_kind,
    )


def _record_type_for_memory_kind(memory_kind: str) -> str:
    if memory_kind == "lesson":
        return "fact"
    return memory_kind


def _candidate_title(
    decision: ImprovementDecision,
    evaluation: OnlineImprovementEval,
) -> str:
    reason = str(decision.rationale_code or evaluation.failure_reason_code or "unknown")
    return f"self_improvement:{decision.memory_kind}:{reason}"


def _candidate_tags(
    decision: ImprovementDecision,
    evaluation: OnlineImprovementEval,
) -> list[str]:
    tags = [
        "self_improvement",
        f"action:{decision.action}",
        f"memory_kind:{decision.memory_kind}",
        f"outcome:{evaluation.outcome_status}",
    ]
    for tag in decision.tags:
        normalized = str(tag or "").strip()
        if normalized and normalized not in tags:
            tags.append(normalized)
    return tags


def _skipped(
    decision: ImprovementDecision,
    reason: str,
) -> SelfImprovementStageResult:
    return SelfImprovementStageResult(
        candidate_id=None,
        skipped_reason=reason,
        action=decision.action,
        memory_kind=decision.memory_kind,
    )


__all__ = [
    "SelfImprovementStageResult",
    "stage_improvement_decision",
]
