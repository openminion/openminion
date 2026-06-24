"""Typed goal lifecycle verification helpers for LGMH Tier B."""

from dataclasses import dataclass
from typing import Literal, Mapping

from ..verification.policy import VerifierInvocation, VerifierResult, run_verifier
from ...schemas import (
    ActionResult,
    Command,
    FailureCondition,
    Goal,
    VerificationMode,
    WorkingState,
    evaluate_goal_wall_clock_budget,
)
from ...storage.goals import GoalStore


GoalVerificationStatus = Literal["passed", "failed", "incomplete"]


@dataclass(frozen=True)
class GoalVerificationInput:
    """Bound verifier input for one criterion or deliverable."""

    command: Command
    action_result: ActionResult


@dataclass(frozen=True)
class GoalVerificationResult:
    """Typed result returned by `verify_goal_completion`."""

    status: GoalVerificationStatus
    unmet_criteria: tuple[str, ...]
    missing_deliverables: tuple[str, ...]
    triggered_failures: tuple[FailureCondition, ...]
    verifier_results: tuple[VerifierResult, ...]


def verify_goal_completion(
    goal_id: str,
    *,
    goals: GoalStore,
    run_id: str,
    state: WorkingState,
    logger,
    criterion_inputs: Mapping[str, GoalVerificationInput] | None = None,
    deliverable_inputs: Mapping[str, GoalVerificationInput] | None = None,
    elapsed_wall_clock_seconds: int | float | None = None,
    mode: VerificationMode = VerificationMode.rule_based,
) -> GoalVerificationResult:
    """Run goal verification for a persisted goal."""

    goal = goals.get(goal_id)
    if goal is None:
        raise KeyError(f"Unknown goal_id: {goal_id!r}")
    return verify_goal(
        goal,
        run_id=run_id,
        state=state,
        logger=logger,
        criterion_inputs=criterion_inputs,
        deliverable_inputs=deliverable_inputs,
        elapsed_wall_clock_seconds=elapsed_wall_clock_seconds,
        mode=mode,
    )


def verify_goal(
    goal: Goal,
    *,
    run_id: str,
    state: WorkingState,
    logger,
    criterion_inputs: Mapping[str, GoalVerificationInput] | None = None,
    deliverable_inputs: Mapping[str, GoalVerificationInput] | None = None,
    elapsed_wall_clock_seconds: int | float | None = None,
    mode: VerificationMode = VerificationMode.rule_based,
) -> GoalVerificationResult:
    """Run structural verification for a typed goal value."""

    criterion_inputs = dict(criterion_inputs or {})
    deliverable_inputs = dict(deliverable_inputs or {})
    unmet_criteria: list[str] = []
    missing_deliverables: list[str] = []
    triggered_failures: list[FailureCondition] = []
    verifier_results: list[VerifierResult] = []

    if elapsed_wall_clock_seconds is not None:
        exhausted = evaluate_goal_wall_clock_budget(
            goal,
            elapsed_seconds=elapsed_wall_clock_seconds,
        )
        if exhausted is not None:
            triggered_failures.append(exhausted)

    for condition in goal.failure_conditions:
        if condition.kind == "operator_cancelled" and goal.status == "cancelled":
            triggered_failures.append(condition)

    for criterion in goal.success_criteria:
        payload = criterion_inputs.get(criterion.criterion_id)
        if payload is None:
            unmet_criteria.append(criterion.criterion_id)
            continue
        result = run_verifier(
            VerifierInvocation(
                family="structural",
                goal_id=goal.goal_id,
                run_id=run_id,
                command=payload.command,
                action_result=payload.action_result,
                criterion=criterion,
                mode=mode,
            ),
            state=state,
            logger=logger,
        )
        verifier_results.append(result)
        if not result.passed:
            unmet_criteria.append(criterion.criterion_id)

    for deliverable in goal.deliverables:
        payload = deliverable_inputs.get(deliverable.deliverable_id)
        if payload is None:
            missing_deliverables.append(deliverable.deliverable_id)
            continue
        result = run_verifier(
            VerifierInvocation(
                family=deliverable.verification_hint,
                goal_id=goal.goal_id,
                run_id=run_id,
                command=payload.command,
                action_result=payload.action_result,
                deliverable=deliverable,
                mode=mode,
            ),
            state=state,
            logger=logger,
        )
        verifier_results.append(result)
        if not result.passed:
            missing_deliverables.append(deliverable.deliverable_id)

    if triggered_failures:
        status: GoalVerificationStatus = "failed"
    elif unmet_criteria or missing_deliverables:
        status = "failed" if verifier_results else "incomplete"
    else:
        status = "passed"

    return GoalVerificationResult(
        status=status,
        unmet_criteria=tuple(unmet_criteria),
        missing_deliverables=tuple(missing_deliverables),
        triggered_failures=tuple(triggered_failures),
        verifier_results=tuple(verifier_results),
    )


__all__ = [
    "GoalVerificationInput",
    "GoalVerificationResult",
    "GoalVerificationStatus",
    "verify_goal",
    "verify_goal_completion",
]
