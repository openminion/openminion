"""Structural online self-improvement judgment helpers."""

from typing import Any, Mapping

from openminion.modules.brain.loop.tools.contracts import AdaptiveToolLoopOutcome

from .contracts import (
    ImprovementDecision,
    OnlineImprovementEval,
    SelfImprovementPolicy,
)
from .constants import RETRY_ANOMALY_THRESHOLD


def decide_online_improvement(
    evaluation: OnlineImprovementEval | Mapping[str, Any],
    *,
    policy: SelfImprovementPolicy | Mapping[str, Any] | None = None,
) -> ImprovementDecision:
    """Return a typed decision from structural attempt facts."""

    ev = (
        evaluation
        if isinstance(evaluation, OnlineImprovementEval)
        else OnlineImprovementEval.model_validate(evaluation)
    )
    policy_obj = (
        policy
        if isinstance(policy, SelfImprovementPolicy)
        else SelfImprovementPolicy.model_validate(policy or {})
    )
    if not policy_obj.is_enabled:
        return ImprovementDecision(
            action="ignore",
            rationale_code="policy_disabled",
            confidence=0.0,
        )

    evidence_count = len([ref for ref in ev.evidence_refs if str(ref).strip()])
    has_external_signal = evidence_count >= policy_obj.min_external_signal_count
    if (
        policy_obj.max_staged_items_per_run > 0
        and has_external_signal
        and ev.outcome_status in {"failure", "partial", "blocked"}
    ):
        return ImprovementDecision(
            action="stage_candidate",
            rationale_code=f"{ev.outcome_status}_with_external_evidence",
            confidence=min(1.0, max(0.5, ev.anomaly_score)),
            memory_kind="failure_pattern",
            tags=["self_improvement", f"outcome:{ev.outcome_status}"],
        )

    if (
        policy_obj.reserved_llm_calls > 0
        and ev.outcome_status in {"failure", "blocked"}
        and ev.anomaly_score >= RETRY_ANOMALY_THRESHOLD
    ):
        return ImprovementDecision(
            action="retry_now",
            rationale_code="high_anomaly_failure",
            confidence=min(1.0, ev.anomaly_score),
            memory_kind="none",
            tags=["self_improvement", "online_retry"],
        )

    if (
        policy_obj.reserved_llm_calls > 0
        and ev.outcome_status in {"failure", "partial"}
        and ev.progress_delta in {"flat", "negative"}
    ):
        return ImprovementDecision(
            action="replan_now",
            rationale_code="non_positive_progress",
            confidence=max(0.5, min(1.0, ev.anomaly_score)),
            memory_kind="none",
            tags=["self_improvement", "online_replan"],
        )

    return ImprovementDecision(
        action="ignore",
        rationale_code="insufficient_structural_signal",
        confidence=0.0,
    )


def evaluation_from_adaptive_outcome(
    outcome: AdaptiveToolLoopOutcome,
    *,
    attempt_id: str,
    trace_id: str,
    evidence_refs: list[str] | None = None,
) -> OnlineImprovementEval:
    """Project an adaptive-loop outcome into the BSIL online eval contract."""

    status = _outcome_status_from_termination(outcome.termination_reason)
    progress = "positive" if outcome.state.total_tool_calls > 0 else "unknown"
    return OnlineImprovementEval(
        attempt_id=attempt_id,
        trace_id=trace_id,
        mode_name=outcome.mode_name,
        tool_name=str(outcome.tool_name or ""),
        iteration=outcome.state.iteration,
        anomaly_score=0.0,
        outcome_status=status,
        failure_reason_code=str(outcome.termination_reason or "").strip(),
        progress_delta=progress,
        evidence_refs=list(evidence_refs or []),
    )


def decide_for_adaptive_outcome(
    outcome: AdaptiveToolLoopOutcome,
    *,
    policy: SelfImprovementPolicy | Mapping[str, Any] | None = None,
    attempt_id: str,
    trace_id: str,
    evidence_refs: list[str] | None = None,
) -> ImprovementDecision:
    """Convenience trigger surface for the first online evaluator path."""

    evaluation = evaluation_from_adaptive_outcome(
        outcome,
        attempt_id=attempt_id,
        trace_id=trace_id,
        evidence_refs=evidence_refs,
    )
    return decide_online_improvement(evaluation, policy=policy)


def attach_decision_to_adaptive_outcome(
    outcome: AdaptiveToolLoopOutcome,
    *,
    policy: SelfImprovementPolicy | Mapping[str, Any] | None = None,
    attempt_id: str,
    trace_id: str,
    evidence_refs: list[str] | None = None,
) -> ImprovementDecision:
    """Attach typed BSIL eval/decision telemetry to an adaptive outcome."""

    evaluation = evaluation_from_adaptive_outcome(
        outcome,
        attempt_id=attempt_id,
        trace_id=trace_id,
        evidence_refs=evidence_refs,
    )
    decision = decide_online_improvement(evaluation, policy=policy)
    outcome.self_improvement_evaluation = evaluation.model_dump(mode="json")
    outcome.self_improvement_decision = decision.model_dump(mode="json")
    return decision


def _outcome_status_from_termination(reason: str) -> str:
    normalized = str(reason or "").strip()
    if not normalized:
        return "other"
    if normalized in {"final_text", "confident_complete"}:
        return "success"
    if normalized in {"needs_user", "job_pending"}:
        return "blocked"
    if normalized in {"budget_exhausted", "iteration_cap"}:
        return "partial"
    return "failure"


__all__ = [
    "decide_for_adaptive_outcome",
    "decide_online_improvement",
    "attach_decision_to_adaptive_outcome",
    "evaluation_from_adaptive_outcome",
]
