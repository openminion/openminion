"""Typed verifier helpers for coding-mode completion."""

from dataclasses import dataclass
from typing import Any, Literal

from openminion.modules.brain.runtime.verification.policy import (
    VerifierResult,
)
from openminion.modules.brain.runtime.goal.verification import (
    GoalVerificationInput,
    verify_goal,
)
from openminion.modules.brain.schemas import (
    ActionResult,
    Goal,
    ToolCommand,
    VerificationMode,
    WorkingState,
)

CODING_VERIFIER_VERDICT_COMPLETE = "verified_complete"
CODING_VERIFIER_VERDICT_INCOMPLETE = "verified_incomplete"
CODING_VERIFIER_VERDICT_BLOCKED = "verified_blocked"
CODING_VERIFIER_VERDICT_BUDGET_EXHAUSTED = "verified_budget_exhausted"

CodingVerifierVerdict = Literal[
    "verified_complete",
    "verified_incomplete",
    "verified_blocked",
    "verified_budget_exhausted",
]

CODING_VERIFIER_VERDICTS: frozenset[CodingVerifierVerdict] = frozenset(
    {
        CODING_VERIFIER_VERDICT_COMPLETE,
        CODING_VERIFIER_VERDICT_INCOMPLETE,
        CODING_VERIFIER_VERDICT_BLOCKED,
        CODING_VERIFIER_VERDICT_BUDGET_EXHAUSTED,
    }
)


@dataclass(frozen=True, slots=True)
class CodingVerifierEvaluation:
    verdict: CodingVerifierVerdict
    results: tuple[VerifierResult, ...]
    missing_targets: tuple[str, ...] = ()


def coerce_coding_verifier_verdict(value: str) -> CodingVerifierVerdict:
    normalized = str(value or "").strip()
    if normalized not in CODING_VERIFIER_VERDICTS:
        raise ValueError(
            "Coding verifier verdict must be one of "
            f"{sorted(CODING_VERIFIER_VERDICTS)}; got {normalized!r}."
        )
    return normalized  # type: ignore[return-value]


def serialize_verifier_candidate(
    *,
    command: ToolCommand,
    action_result: ActionResult,
) -> dict[str, Any]:
    return {
        "command": command.model_dump(mode="python"),
        "action_result": action_result.model_dump(mode="python"),
    }


def load_verifier_candidate(
    payload: Any,
) -> tuple[ToolCommand, ActionResult] | None:
    if not isinstance(payload, dict):
        return None
    raw_command = payload.get("command")
    raw_action_result = payload.get("action_result")
    if not isinstance(raw_command, dict) or not isinstance(raw_action_result, dict):
        return None
    try:
        return (
            ToolCommand.model_validate(raw_command),
            ActionResult.model_validate(raw_action_result),
        )
    except Exception:
        return None


def evaluate_coding_verifier(
    *,
    goal: Goal,
    candidates: tuple[tuple[ToolCommand, ActionResult], ...],
    state: WorkingState,
    logger: Any,
    mode: VerificationMode = VerificationMode.rule_based,
    budget_exhausted: bool = False,
    blocked: bool = False,
) -> CodingVerifierEvaluation:
    bound = {
        (
            str(command.verification_target_kind or "").strip(),
            str(command.verification_target_id or "").strip(),
        ): GoalVerificationInput(command=command, action_result=action_result)
        for command, action_result in candidates
        if command.verification_target_kind and command.verification_target_id
    }
    criterion_inputs = {
        criterion.criterion_id: bound[("criterion", criterion.criterion_id)]
        for criterion in goal.success_criteria
        if ("criterion", criterion.criterion_id) in bound
    }
    deliverable_inputs = {
        deliverable.deliverable_id: bound[("deliverable", deliverable.deliverable_id)]
        for deliverable in goal.deliverables
        if ("deliverable", deliverable.deliverable_id) in bound
    }
    verification = verify_goal(
        goal,
        run_id=str(state.trace_id or goal.goal_id),
        state=state,
        logger=logger,
        criterion_inputs=criterion_inputs,
        deliverable_inputs=deliverable_inputs,
        mode=mode,
    )
    missing_targets = tuple(
        [
            f"criterion:{criterion.criterion_id}"
            for criterion in goal.success_criteria
            if criterion.criterion_id not in criterion_inputs
        ]
        + [
            f"deliverable:{deliverable.deliverable_id}"
            for deliverable in goal.deliverables
            if deliverable.deliverable_id not in deliverable_inputs
        ]
    )
    if blocked:
        verdict = CODING_VERIFIER_VERDICT_BLOCKED
    elif verification.status == "passed":
        verdict = CODING_VERIFIER_VERDICT_COMPLETE
    elif budget_exhausted:
        verdict = CODING_VERIFIER_VERDICT_BUDGET_EXHAUSTED
    else:
        verdict = CODING_VERIFIER_VERDICT_INCOMPLETE
    return CodingVerifierEvaluation(
        verdict=verdict,
        results=verification.verifier_results,
        missing_targets=missing_targets,
    )
