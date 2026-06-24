"""Policy verification helpers for brain runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ...constants import BRAIN_ACTION_STATUS_SUCCESS, BRAIN_COMMAND_KIND_TOOL
from ...diagnostics.events import CanonicalEventLogger
from ...schemas import (
    ActionResult,
    Command,
    Deliverable,
    FreshnessContract,
    FreshnessObligations,
    Goal,
    SuccessCriterion,
    VerificationMode,
    VerifierFamily,
    WorkingState,
)

VerifierVerdict = Literal["pass", "fail"]
_VERIFICATION_MODE_RANK = {
    VerificationMode.none: 0,
    VerificationMode.rule_based: 1,
    VerificationMode.second_opinion: 2,
    VerificationMode.panel_judge: 3,
}


@dataclass(frozen=True)
class VerifierInvocation:
    """Typed verifier invocation."""

    family: VerifierFamily
    goal_id: str
    run_id: str
    command: Command
    action_result: ActionResult
    criterion: SuccessCriterion | None = None
    deliverable: Deliverable | None = None
    mode: VerificationMode = VerificationMode.rule_based

    def __post_init__(self) -> None:
        if not self.goal_id:
            raise ValueError("VerifierInvocation.goal_id is required")
        if not self.run_id:
            raise ValueError("VerifierInvocation.run_id is required")
        provided = sum(
            1 for item in (self.criterion, self.deliverable) if item is not None
        )
        if provided != 1:
            raise ValueError(
                "VerifierInvocation requires exactly one of "
                "`criterion` or `deliverable`."
            )


@dataclass(frozen=True)
class VerifierResult:
    """Typed verifier result."""

    family: VerifierFamily
    goal_id: str
    run_id: str
    target_id: str  # criterion_id or deliverable_id
    passed: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> VerifierVerdict:
        return "pass" if self.passed else "fail"

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "goal_id": self.goal_id,
            "run_id": self.run_id,
            "target_id": self.target_id,
            "passed": self.passed,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
        }


def run_verifier(
    invocation: VerifierInvocation,
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
) -> VerifierResult:
    """Run the runtime-owned verifier dispatch."""

    family = invocation.family
    target_id = (
        invocation.criterion.criterion_id
        if invocation.criterion is not None
        else (invocation.deliverable.deliverable_id if invocation.deliverable else "")
    )

    reasons: list[str] = []

    if family == "artifact_presence":
        if not invocation.action_result.artifact_refs:
            reasons.append("Missing artifact_refs for artifact_presence verifier.")
    elif family == "freshness":
        contract = getattr(invocation.command, "freshness_contract", None)
        obligations = getattr(invocation.command, "freshness_obligations", None)
        answer = getattr(invocation.command, "answer", "") or ""
        freshness_reasons = verify_freshness_answer(
            contract=contract,
            obligations=obligations,
            answer=str(answer),
            action_result=invocation.action_result,
        )
        if contract is None or obligations is None:
            reasons.append("Missing freshness contract for freshness verifier.")
        else:
            reasons.extend(freshness_reasons)
    else:
        passed = verify(
            state=state,
            command=invocation.command,
            action_result=invocation.action_result,
            mode=invocation.mode,
            logger=logger,
        )
        if not passed:
            reasons.append("Structural verify(...) reported failure.")

    result = VerifierResult(
        family=family,
        goal_id=invocation.goal_id,
        run_id=invocation.run_id,
        target_id=target_id,
        passed=not reasons,
        reasons=reasons,
    )
    logger.emit(
        "verifier.completed",
        {
            "family": family,
            "goal_id": invocation.goal_id,
            "run_id": invocation.run_id,
            "target_id": target_id,
            "verdict": result.verdict,
            "reasons": list(reasons),
        },
        trace_id=state.trace_id,
        status=("ok" if result.passed else "error"),
    )
    return result


def is_run_completion_confirmed(
    *,
    goal: Goal,
    results: list[VerifierResult],
) -> bool:
    """Return whether every success criterion and deliverable passed."""

    if not goal.success_criteria or not goal.deliverables:
        return False

    passed_targets = {result.target_id for result in results if result.passed}
    for criterion in goal.success_criteria:
        if criterion.criterion_id not in passed_targets:
            return False
    for deliverable in goal.deliverables:
        if deliverable.deliverable_id not in passed_targets:
            return False
    return True


def _has_structured_evidence(action_result: ActionResult | None) -> bool:
    if action_result is None:
        return False
    return bool(
        action_result.outputs
        or action_result.artifact_refs
        or action_result.memory_refs
    )


_DATED_EVIDENCE_KEYS = frozenset(
    {
        "current_datetime",
        "date",
        "evidence_date",
        "observed_at",
        "published_at",
        "query_time",
        "retrieved_at",
    }
)


def _contains_dated_evidence(raw: Any) -> bool:
    if isinstance(raw, dict):
        for key, value in raw.items():
            if (
                str(key or "").strip() in _DATED_EVIDENCE_KEYS
                and str(value or "").strip()
            ):
                return True
            if _contains_dated_evidence(value):
                return True
        return False
    if isinstance(raw, list | tuple):
        return any(_contains_dated_evidence(item) for item in raw)
    return False


def _has_dated_evidence(action_result: ActionResult | None) -> bool:
    if action_result is None:
        return False
    if _contains_dated_evidence(action_result.outputs):
        return True
    if _contains_dated_evidence(action_result.artifact_refs):
        return True
    if _contains_dated_evidence(action_result.memory_refs):
        return True
    return False


def verify_freshness_answer(
    *,
    contract: FreshnessContract | None,
    obligations: FreshnessObligations | None,
    answer: str,
    action_result: ActionResult | None,
) -> list[str]:
    """Return freshness-verification failures for a candidate answer."""
    if contract is None or obligations is None or not contract.time_sensitive:
        return []
    del answer
    reasons: list[str] = []
    if obligations.require_live_data and not _has_structured_evidence(action_result):
        reasons.append("Missing live-data evidence for a freshness-sensitive answer.")
    if obligations.require_exact_date and not _has_dated_evidence(action_result):
        reasons.append(
            "Missing exact-date evidence for an exact-date freshness answer."
        )
    return reasons


def build_freshness_failure_message(
    *,
    contract: FreshnessContract | None,
    reasons: list[str],
) -> str:
    """Render the user-facing freshness failure message."""
    if contract is None or not contract.time_sensitive:
        return (
            "I could not produce a grounded current answer from the available evidence."
        )
    domain = contract.domain.value
    reason_text = (
        "; ".join(reasons) if reasons else "required live-data obligations were not met"
    )
    return (
        f"I couldn't provide a grounded current {domain} answer because {reason_text}. "
        "Please retry with live data available so I can answer with sources and an exact date."
    )


def resolve_verification_mode(
    *, current: VerificationMode, candidate: VerificationMode | None
) -> VerificationMode:
    if candidate is None or candidate == VerificationMode.none:
        return current
    if current == VerificationMode.none:
        return candidate

    if _VERIFICATION_MODE_RANK[candidate] > _VERIFICATION_MODE_RANK[current]:
        return candidate
    return current


def verify(
    *,
    state: WorkingState,
    command: Command,
    action_result: ActionResult,
    mode: VerificationMode,
    logger: CanonicalEventLogger,
) -> bool:
    reasons: list[str] = []
    if action_result.status != BRAIN_ACTION_STATUS_SUCCESS:
        reasons.append(f"Action status is {action_result.status}.")

    if mode in {
        VerificationMode.rule_based,
        VerificationMode.second_opinion,
        VerificationMode.panel_judge,
    }:
        if not _has_structured_evidence(action_result):
            reasons.append("Missing structured evidence in action result.")
        elif command.success_criteria:
            for k, v in command.success_criteria.items():
                actual = action_result.outputs.get(k) if action_result.outputs else None
                if actual != v:
                    reasons.append(
                        f"Success criteria '{k}' not met. Expected {v}, got {actual}."
                    )

    if (
        mode == VerificationMode.panel_judge
        and command.kind == BRAIN_COMMAND_KIND_TOOL
        and command.risk_level == "high"
        and not action_result.artifact_refs
    ):
        reasons.append(
            "High-risk tool step requires artifact evidence for panel_judge mode."
        )

    passed = len(reasons) == 0
    logger.emit(
        "verify.completed",
        {
            "command_id": command.command_id,
            "mode": mode.value,
            "outcome": ("pass" if passed else "fail"),
            "reasons": reasons,
        },
        trace_id=state.trace_id,
        status=("ok" if passed else "error"),
    )
    return passed


__all__ = [
    "VerifierInvocation",
    "VerifierResult",
    "VerifierVerdict",
    "build_freshness_failure_message",
    "is_run_completion_confirmed",
    "resolve_verification_mode",
    "run_verifier",
    "verify",
    "verify_freshness_answer",
]
