"""Typed verifier helpers for coding-mode completion."""

from dataclasses import dataclass
from typing import Any, Literal

from openminion.modules.brain.runtime.verification.policy import (
    VerifierInvocation,
    VerifierResult,
    is_run_completion_confirmed,
    run_verifier,
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
    command: ToolCommand,
    action_result: ActionResult,
    state: WorkingState,
    logger: Any,
    mode: VerificationMode = VerificationMode.rule_based,
    budget_exhausted: bool = False,
    blocked: bool = False,
) -> CodingVerifierEvaluation:
    results: list[VerifierResult] = []
    run_id = str(command.command_id or "").strip()
    for criterion in goal.success_criteria:
        results.append(
            run_verifier(
                VerifierInvocation(
                    family="structural",
                    goal_id=goal.goal_id,
                    run_id=run_id,
                    command=command,
                    action_result=action_result,
                    criterion=criterion,
                    mode=mode,
                ),
                state=state,
                logger=logger,
            )
        )
    for deliverable in goal.deliverables:
        results.append(
            run_verifier(
                VerifierInvocation(
                    family=deliverable.verification_hint,
                    goal_id=goal.goal_id,
                    run_id=run_id,
                    command=command,
                    action_result=action_result,
                    deliverable=deliverable,
                    mode=mode,
                ),
                state=state,
                logger=logger,
            )
        )
    if budget_exhausted:
        verdict = CODING_VERIFIER_VERDICT_BUDGET_EXHAUSTED
    elif blocked:
        verdict = CODING_VERIFIER_VERDICT_BLOCKED
    elif is_run_completion_confirmed(goal=goal, results=list(results)):
        verdict = CODING_VERIFIER_VERDICT_COMPLETE
    else:
        verdict = CODING_VERIFIER_VERDICT_INCOMPLETE
    return CodingVerifierEvaluation(verdict=verdict, results=tuple(results))
