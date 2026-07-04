"""Live goal-run evaluator contracts for goal-centered completion."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openminion.modules.brain.constants import MissionStatus

from .loop import GoalRunEvaluation, GoalRunOutcome
from .verification import GoalVerificationResult


class _StrictGoalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GoalTurnResult(_StrictGoalModel):
    """Structured result from one real turn or test runner turn."""

    proposed_outcome: GoalRunOutcome
    reason: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    next_instruction: str = ""
    error_refs: tuple[str, ...] = ()

    @field_validator("reason", "next_instruction", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("evidence_refs", "error_refs", mode="before")
    @classmethod
    def _normalize_refs(cls, value: object) -> tuple[str, ...]:
        if value is None:
            values: tuple[object, ...] = ()
        elif isinstance(value, str):
            values = (value,)
        elif isinstance(value, list | tuple | set):
            values = tuple(value)
        else:
            values = (value,)
        return tuple(str(item).strip() for item in values if str(item).strip())


class GoalLiveEvaluationInput(_StrictGoalModel):
    """Inputs needed to convert a turn result into a goal-run evaluation."""

    goal_id: str = Field(min_length=1)
    turn_result: GoalTurnResult
    verification: GoalVerificationResult | None = None


class GoalLiveEvaluator:
    """Validate structured turn facts without inferring semantics from prose."""

    def evaluate(self, payload: GoalLiveEvaluationInput) -> GoalRunEvaluation:
        result = payload.turn_result
        outcome = _verified_outcome(result.proposed_outcome, payload.verification)
        return GoalRunEvaluation(
            goal_id=payload.goal_id,
            outcome=outcome,
            mission_status=_mission_status_for_outcome(outcome),
            reason=_evaluation_reason(result, payload.verification, outcome),
            evidence_refs=result.evidence_refs,
            next_instruction=result.next_instruction if outcome == "continue" else "",
        )


def _verified_outcome(
    proposed: GoalRunOutcome,
    verification: GoalVerificationResult | None,
) -> GoalRunOutcome:
    if proposed != "satisfied":
        return proposed
    if verification is not None and verification.status == "passed":
        return "satisfied"
    return "continue"


def _evaluation_reason(
    result: GoalTurnResult,
    verification: GoalVerificationResult | None,
    outcome: GoalRunOutcome,
) -> str:
    if result.proposed_outcome == "satisfied" and outcome != "satisfied":
        status = verification.status if verification is not None else "not_checked"
        return f"verification_required:{status}"
    return result.reason


def _mission_status_for_outcome(outcome: GoalRunOutcome) -> MissionStatus:
    return {
        "satisfied": MissionStatus.COMPLETED,
        "continue": MissionStatus.ACTIVE,
        "blocked": MissionStatus.PAUSED,
        "needs_user": MissionStatus.PAUSED,
        "awaiting_async": MissionStatus.AWAITING_ASYNC,
        "halted": MissionStatus.HALTED,
    }[outcome]


__all__ = [
    "GoalLiveEvaluationInput",
    "GoalLiveEvaluator",
    "GoalTurnResult",
]
