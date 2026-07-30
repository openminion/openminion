from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openminion.modules.task.autonomy import AutonomyRunStatus


class AutonomyLoopConditionKind(StrEnum):
    PRODUCTIVE = "productive"
    WAITING = "waiting"
    RETRYABLE_FAILURE = "retryable_failure"
    MISSING_CAPABILITY = "missing_capability"
    DENIED = "denied"
    DUPLICATE_ACTION = "duplicate_action"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    STRATEGY_FAILURE = "strategy_failure"
    TERMINAL_INABILITY = "terminal_inability"


class AutonomyLoopJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: AutonomyLoopConditionKind
    run_status: AutonomyRunStatus
    requires_model_replan: bool = False
    requires_operator: bool = False
    bounded_retry_allowed: bool = False
    terminal: bool = False
    reason_code: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    next_resume_action: str | None = None

    @model_validator(mode="after")
    def _operator_cases_need_resume_action(self) -> "AutonomyLoopJudgment":
        if self.requires_operator and not self.next_resume_action:
            raise ValueError("operator-required judgment needs next_resume_action")
        return self


def classify_autonomy_loop_condition(
    *,
    condition: AutonomyLoopConditionKind,
    evidence_refs: tuple[str, ...] = (),
) -> AutonomyLoopJudgment:
    if condition == AutonomyLoopConditionKind.PRODUCTIVE:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.RUNNING,
            reason_code="progress_observed",
            evidence_refs=evidence_refs,
        )
    if condition == AutonomyLoopConditionKind.WAITING:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.WAITING_FOR_INPUT,
            requires_operator=True,
            reason_code="waiting_on_external_condition",
            evidence_refs=evidence_refs,
            next_resume_action="answer-input-request",
        )
    if condition == AutonomyLoopConditionKind.RETRYABLE_FAILURE:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.RUNNING,
            bounded_retry_allowed=True,
            reason_code="retryable_failure",
            evidence_refs=evidence_refs,
        )
    if condition == AutonomyLoopConditionKind.MISSING_CAPABILITY:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.BLOCKED,
            requires_operator=True,
            reason_code="missing_capability",
            evidence_refs=evidence_refs,
            next_resume_action="approve-or-install-capability",
        )
    if condition == AutonomyLoopConditionKind.DENIED:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.BLOCKED,
            requires_operator=True,
            reason_code="permission_denied",
            evidence_refs=evidence_refs,
            next_resume_action="revise-scope-or-approve",
        )
    if condition == AutonomyLoopConditionKind.DUPLICATE_ACTION:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.BLOCKED,
            requires_model_replan=True,
            reason_code="duplicate_action_bounded",
            evidence_refs=evidence_refs,
        )
    if condition == AutonomyLoopConditionKind.BUDGET_EXHAUSTED:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.BLOCKED,
            requires_operator=True,
            reason_code="budget_exhausted",
            evidence_refs=evidence_refs,
            next_resume_action="extend-budget-or-stop",
        )
    if condition == AutonomyLoopConditionKind.DEADLINE_EXHAUSTED:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.BLOCKED,
            requires_operator=True,
            reason_code="deadline_exhausted",
            evidence_refs=evidence_refs,
            next_resume_action="extend-deadline-or-stop",
        )
    if condition == AutonomyLoopConditionKind.STRATEGY_FAILURE:
        return AutonomyLoopJudgment(
            condition=condition,
            run_status=AutonomyRunStatus.RUNNING,
            requires_model_replan=True,
            reason_code="strategy_failure_replan_required",
            evidence_refs=evidence_refs,
        )
    return AutonomyLoopJudgment(
        condition=condition,
        run_status=AutonomyRunStatus.FAILED,
        terminal=True,
        reason_code="terminal_inability",
        evidence_refs=evidence_refs,
    )


__all__ = [
    "AutonomyLoopConditionKind",
    "AutonomyLoopJudgment",
    "classify_autonomy_loop_condition",
]
