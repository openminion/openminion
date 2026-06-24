"""Threshold calibration helpers for brain runtime."""

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from openminion.base.time import utc_now_iso

ThresholdId = Literal[
    "high_risk_score_threshold",
    "medium_risk_score_threshold",
    "low_grounding_threshold",
    "low_intent_confidence_threshold",
    "high_ambiguity_threshold",
    "repeat_failure_threshold",
    "loop_count_threshold",
    "replan_count_threshold",
    "low_progress_ticks_threshold",
    "low_progress_no_new_facts_threshold",
    "low_progress_iterations_without_new_typed_record_threshold",
    "low_progress_repeated_arg_signature_threshold",
    "low_progress_unique_tool_call_count_delta_threshold",
    "budget_pressure_threshold",
    "tool_degraded_threshold",
    "ask_threshold",
]

DecisionStatus = Literal["accepted", "rejected", "deferred"]
Numeric = int | float

_VALID_THRESHOLD_IDS: tuple[ThresholdId, ...] = (
    "high_risk_score_threshold",
    "medium_risk_score_threshold",
    "low_grounding_threshold",
    "low_intent_confidence_threshold",
    "high_ambiguity_threshold",
    "repeat_failure_threshold",
    "loop_count_threshold",
    "replan_count_threshold",
    "low_progress_ticks_threshold",
    "low_progress_no_new_facts_threshold",
    "low_progress_iterations_without_new_typed_record_threshold",
    "low_progress_repeated_arg_signature_threshold",
    "low_progress_unique_tool_call_count_delta_threshold",
    "budget_pressure_threshold",
    "tool_degraded_threshold",
    "ask_threshold",
)

_INT_THRESHOLD_IDS = frozenset(
    {
        "high_risk_score_threshold",
        "medium_risk_score_threshold",
        "repeat_failure_threshold",
        "loop_count_threshold",
        "replan_count_threshold",
        "low_progress_ticks_threshold",
        "low_progress_no_new_facts_threshold",
        "low_progress_iterations_without_new_typed_record_threshold",
        "low_progress_repeated_arg_signature_threshold",
        "low_progress_unique_tool_call_count_delta_threshold",
    }
)

_THRESHOLD_ALIASES: dict[ThresholdId, tuple[str, ...]] = {
    "high_risk_score_threshold": ("high_risk_score_threshold",),
    "medium_risk_score_threshold": ("medium_risk_score_threshold",),
    "low_grounding_threshold": ("low_grounding_threshold",),
    "low_intent_confidence_threshold": ("low_intent_confidence_threshold",),
    "high_ambiguity_threshold": ("high_ambiguity_threshold",),
    "repeat_failure_threshold": (
        "repeat_failure_threshold",
        "repeat_error_threshold",
    ),
    "loop_count_threshold": ("loop_count_threshold", "stall_ticks_threshold"),
    "replan_count_threshold": ("replan_count_threshold", "no_new_facts_threshold"),
    "low_progress_ticks_threshold": ("low_progress_ticks_threshold",),
    "low_progress_no_new_facts_threshold": ("low_progress_no_new_facts_threshold",),
    "low_progress_iterations_without_new_typed_record_threshold": (
        "low_progress_iterations_without_new_typed_record_threshold",
    ),
    "low_progress_repeated_arg_signature_threshold": (
        "low_progress_repeated_arg_signature_threshold",
    ),
    "low_progress_unique_tool_call_count_delta_threshold": (
        "low_progress_unique_tool_call_count_delta_threshold",
    ),
    "budget_pressure_threshold": ("budget_pressure_threshold",),
    "tool_degraded_threshold": ("tool_degraded_threshold",),
    "ask_threshold": ("ask_threshold",),
}

_SENSITIVITY_DIRECTIONS: dict[ThresholdId, int] = {
    "high_risk_score_threshold": -1,
    "medium_risk_score_threshold": -1,
    "low_grounding_threshold": 1,
    "low_intent_confidence_threshold": 1,
    "high_ambiguity_threshold": -1,
    "repeat_failure_threshold": -1,
    "loop_count_threshold": -1,
    "replan_count_threshold": -1,
    "low_progress_ticks_threshold": -1,
    "low_progress_no_new_facts_threshold": -1,
    "low_progress_iterations_without_new_typed_record_threshold": -1,
    "low_progress_repeated_arg_signature_threshold": -1,
    "low_progress_unique_tool_call_count_delta_threshold": -1,
    "budget_pressure_threshold": -1,
    "tool_degraded_threshold": -1,
    "ask_threshold": 1,
}

_METRIC_TARGETS: dict[ThresholdId, tuple[str, float]] = {
    "high_risk_score_threshold": ("performance.failure_rate", 0.25),
    "medium_risk_score_threshold": ("performance.failure_rate", 0.25),
    "low_grounding_threshold": ("performance.other_rate", 0.10),
    "low_intent_confidence_threshold": ("performance.other_rate", 0.10),
    "high_ambiguity_threshold": ("performance.other_rate", 0.10),
    "repeat_failure_threshold": ("failure.recurrence", 1.0),
    "loop_count_threshold": ("failure.recurrence", 1.0),
    "replan_count_threshold": ("failure.recurrence", 1.0),
    "low_progress_ticks_threshold": ("failure.recurrence", 1.0),
    "low_progress_no_new_facts_threshold": ("failure.recurrence", 1.0),
    "low_progress_iterations_without_new_typed_record_threshold": (
        "failure.recurrence",
        1.0,
    ),
    "low_progress_repeated_arg_signature_threshold": ("failure.recurrence", 1.0),
    "low_progress_unique_tool_call_count_delta_threshold": (
        "failure.recurrence",
        1.0,
    ),
    "budget_pressure_threshold": ("failure.budget_pressure_rate", 0.15),
    "tool_degraded_threshold": ("failure.tool_degraded_rate", 0.10),
    "ask_threshold": ("performance.other_rate", 0.10),
}


class CalibrationInput(BaseModel):
    """Typed calibration input for one threshold."""

    model_config = ConfigDict(extra="forbid")

    threshold_id: ThresholdId
    evidence_kind: str
    evidence_refs: list[str] = Field(default_factory=list)
    observed_metric: float
    target_metric: float


class ThresholdChangeProposal(BaseModel):
    """Typed proposal for one threshold adjustment."""

    model_config = ConfigDict(extra="forbid")

    threshold_id: ThresholdId
    current_value: Numeric
    proposed_value: Numeric
    evidence_refs: list[str] = Field(default_factory=list)
    policy_id: str = ""
    rationale_facts: dict[str, Any] = Field(default_factory=dict)


class ThresholdChangeDecision(BaseModel):
    """Typed operator decision for one proposal."""

    model_config = ConfigDict(extra="forbid")

    proposal_ref: str
    status: DecisionStatus
    policy_id: str = ""
    decided_at: str


class ThresholdAdjustment(BaseModel):
    """Typed applied threshold adjustment."""

    model_config = ConfigDict(extra="forbid")

    threshold_id: ThresholdId
    prior_value: Numeric
    new_value: Numeric
    decision_ref: str
    applied_at: str
    reversible: bool = True


def _entry_list(readout: Any, field_name: str) -> list[Any]:
    if readout is None:
        return []
    if isinstance(readout, Mapping):
        value = readout.get(field_name)
        return list(value) if isinstance(value, list) else []
    value = getattr(readout, field_name, None)
    return list(value) if isinstance(value, list) else []


def _field(item: Any, field_name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(field_name)
    return getattr(item, field_name, None)


def _threshold_value(
    current_thresholds: Any, threshold_id: ThresholdId
) -> Numeric | None:
    for field_name in _THRESHOLD_ALIASES[threshold_id]:
        if isinstance(current_thresholds, Mapping) and field_name in current_thresholds:
            value = current_thresholds.get(field_name)
        else:
            value = getattr(current_thresholds, field_name, None)
        if isinstance(value, bool) or value is None:
            continue
        if threshold_id in _INT_THRESHOLD_IDS:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _numeric_string(value: Numeric, *, threshold_id: ThresholdId) -> str:
    if threshold_id in _INT_THRESHOLD_IDS:
        return str(int(value))
    rendered = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return rendered if rendered else "0"


def _proposal_ref(
    threshold_id: ThresholdId,
    current_value: Numeric,
    proposed_value: Numeric,
) -> str:
    return (
        "threshold_change::"
        f"{threshold_id}::"
        f"{_numeric_string(current_value, threshold_id=threshold_id)}::"
        f"{_numeric_string(proposed_value, threshold_id=threshold_id)}"
    )


def _parse_numeric(value: str, *, threshold_id: ThresholdId) -> Numeric:
    if threshold_id in _INT_THRESHOLD_IDS:
        return int(value)
    return float(value)


def _parse_proposal_ref(proposal_ref: str) -> tuple[ThresholdId, Numeric, Numeric]:
    parts = str(proposal_ref or "").split("::")
    if len(parts) != 4 or parts[0] != "threshold_change":
        raise ValueError("invalid proposal_ref")
    threshold_id = parts[1]
    if threshold_id not in _VALID_THRESHOLD_IDS:
        raise ValueError("unknown threshold_id in proposal_ref")
    current_value = _parse_numeric(parts[2], threshold_id=threshold_id)  # type: ignore[arg-type]
    proposed_value = _parse_numeric(parts[3], threshold_id=threshold_id)  # type: ignore[arg-type]
    return threshold_id, current_value, proposed_value  # type: ignore[return-value]


def _performance_metrics(readout: Any) -> tuple[dict[str, float], list[str]]:
    total_success = 0
    total_failure = 0
    total_other = 0
    refs: list[str] = []
    for entry in _entry_list(readout, "entries"):
        subject_id = str(_field(entry, "subject_id") or "").strip()
        success_count = int(_field(entry, "success_count") or 0)
        failure_count = int(_field(entry, "failure_count") or 0)
        other_count = int(_field(entry, "other_count") or 0)
        total_success += success_count
        total_failure += failure_count
        total_other += other_count
        if subject_id and (success_count or failure_count or other_count):
            refs.append(f"performance:{subject_id}")
    total = total_success + total_failure + total_other
    if total <= 0:
        return {}, []
    return (
        {
            "performance.failure_rate": total_failure / total,
            "performance.other_rate": total_other / total,
        },
        sorted(set(refs)),
    )


def _failure_metrics(readout: Any) -> tuple[dict[str, float], list[str]]:
    rows = _entry_list(readout, "rows")
    if not rows:
        return {}, []

    total_facts = 0
    max_recurrence = 0
    budget_recurrence = 0
    tool_recurrence = 0
    refs: list[str] = []
    for row in rows:
        seam_id = str(_field(row, "seam_id") or "").strip()
        reason_code = str(_field(row, "reason_code") or "").strip()
        recurrence_count = int(_field(row, "recurrence_count") or 0)
        if not seam_id or not reason_code or recurrence_count <= 0:
            continue
        refs.append(f"failure:{seam_id}|{reason_code}")
        total_facts += recurrence_count
        max_recurrence = max(max_recurrence, recurrence_count)
        if seam_id == "adaptive_termination":
            budget_recurrence += recurrence_count
        if seam_id in {"search_provider", "controlplane_route", "github_policy"}:
            tool_recurrence += recurrence_count
    if total_facts <= 0:
        return {}, []
    return (
        {
            "failure.recurrence": float(max_recurrence),
            "failure.budget_pressure_rate": budget_recurrence / total_facts,
            "failure.tool_degraded_rate": tool_recurrence / total_facts,
        },
        sorted(set(refs)),
    )


def _step_size(threshold_id: ThresholdId) -> Numeric:
    if threshold_id in _INT_THRESHOLD_IDS:
        return 1
    return 0.05


def _clamp_value(threshold_id: ThresholdId, value: Numeric) -> Numeric:
    if threshold_id in _INT_THRESHOLD_IDS:
        if threshold_id in {"high_risk_score_threshold", "medium_risk_score_threshold"}:
            return max(1, min(100, int(value)))
        return max(1, int(value))
    return max(0.0, min(1.0, round(float(value), 4)))


def _proposed_value(
    threshold_id: ThresholdId,
    current_value: Numeric,
    *,
    observed_metric: float,
    target_metric: float,
) -> Numeric | None:
    if observed_metric <= target_metric:
        return None
    direction = _SENSITIVITY_DIRECTIONS[threshold_id]
    step = _step_size(threshold_id)
    candidate = _clamp_value(threshold_id, current_value + (direction * step))
    if candidate == current_value:
        return None
    return candidate


def _emit_adjustment_event(audit_log: Any, adjustment: ThresholdAdjustment) -> None:
    if audit_log is None:
        return
    payload = adjustment.model_dump(mode="json")
    event_type = "brain.threshold_adjustment"
    if hasattr(audit_log, "emit") and callable(audit_log.emit):
        audit_log.emit(event_type, payload)
        return
    if hasattr(audit_log, "emit_canonical_event") and callable(
        audit_log.emit_canonical_event
    ):
        audit_log.emit_canonical_event(event_type, payload)
        return
    if hasattr(audit_log, "append") and callable(audit_log.append):
        audit_log.append({"event_type": event_type, "payload": payload})


def propose_calibration(
    performance_readout: Any,
    failure_readout: Any,
    *,
    current_thresholds: Any,
) -> list[ThresholdChangeProposal]:
    """Produce typed threshold-change proposals from SWPC/FPAC readouts."""

    performance_metrics, performance_refs = _performance_metrics(performance_readout)
    failure_metrics, failure_refs = _failure_metrics(failure_readout)
    all_metrics = {**performance_metrics, **failure_metrics}
    proposals: list[ThresholdChangeProposal] = []

    for threshold_id in _VALID_THRESHOLD_IDS:
        current_value = _threshold_value(current_thresholds, threshold_id)
        if current_value is None:
            continue
        evidence_kind, target_metric = _METRIC_TARGETS[threshold_id]
        observed_metric = all_metrics.get(evidence_kind)
        if observed_metric is None:
            continue
        evidence_refs = (
            performance_refs
            if evidence_kind.startswith("performance.")
            else failure_refs
        )
        calibration_input = CalibrationInput(
            threshold_id=threshold_id,
            evidence_kind=evidence_kind,
            evidence_refs=list(evidence_refs),
            observed_metric=observed_metric,
            target_metric=target_metric,
        )
        proposed_value = _proposed_value(
            threshold_id,
            current_value,
            observed_metric=calibration_input.observed_metric,
            target_metric=calibration_input.target_metric,
        )
        if proposed_value is None:
            continue
        proposals.append(
            ThresholdChangeProposal(
                threshold_id=threshold_id,
                current_value=current_value,
                proposed_value=proposed_value,
                evidence_refs=list(calibration_input.evidence_refs),
                policy_id="threshold_calibration_v1",
                rationale_facts=calibration_input.model_dump(mode="json"),
            )
        )

    proposals.sort(key=lambda item: item.threshold_id)
    return proposals


def decide_proposal(
    proposal: ThresholdChangeProposal | Mapping[str, Any],
    *,
    policy_id: str,
    status: DecisionStatus = "accepted",
) -> ThresholdChangeDecision:
    """Record a typed decision for one proposal without applying it."""

    proposal_obj = (
        proposal
        if isinstance(proposal, ThresholdChangeProposal)
        else ThresholdChangeProposal.model_validate(proposal)
    )
    normalized_policy_id = str(policy_id or "").strip()
    if not normalized_policy_id:
        raise ValueError("policy_id is required")
    return ThresholdChangeDecision(
        proposal_ref=_proposal_ref(
            proposal_obj.threshold_id,
            proposal_obj.current_value,
            proposal_obj.proposed_value,
        ),
        status=status,
        policy_id=normalized_policy_id,
        decided_at=utc_now_iso(),
    )


def apply_adjustment(
    decision: ThresholdChangeDecision | Mapping[str, Any],
    *,
    audit_log: Any,
) -> ThresholdAdjustment | None:
    """Apply one accepted decision as a typed adjustment record."""

    decision_obj = (
        decision
        if isinstance(decision, ThresholdChangeDecision)
        else ThresholdChangeDecision.model_validate(decision)
    )
    if decision_obj.status != "accepted":
        return None
    threshold_id, prior_value, new_value = _parse_proposal_ref(
        decision_obj.proposal_ref
    )
    adjustment = ThresholdAdjustment(
        threshold_id=threshold_id,
        prior_value=prior_value,
        new_value=new_value,
        decision_ref=decision_obj.proposal_ref,
        applied_at=utc_now_iso(),
        reversible=True,
    )
    _emit_adjustment_event(audit_log, adjustment)
    return adjustment


def rollback_adjustment(
    adjustment: ThresholdAdjustment | Mapping[str, Any],
    *,
    audit_log: Any,
) -> ThresholdAdjustment:
    """Create and emit the typed inverse adjustment for a prior change."""

    adjustment_obj = (
        adjustment
        if isinstance(adjustment, ThresholdAdjustment)
        else ThresholdAdjustment.model_validate(adjustment)
    )
    inverse = ThresholdAdjustment(
        threshold_id=adjustment_obj.threshold_id,
        prior_value=adjustment_obj.new_value,
        new_value=adjustment_obj.prior_value,
        decision_ref=f"rollback::{adjustment_obj.decision_ref}",
        applied_at=utc_now_iso(),
        reversible=True,
    )
    _emit_adjustment_event(audit_log, inverse)
    return inverse


__all__ = [
    "CalibrationInput",
    "DecisionStatus",
    "ThresholdAdjustment",
    "ThresholdChangeDecision",
    "ThresholdChangeProposal",
    "ThresholdId",
    "apply_adjustment",
    "decide_proposal",
    "propose_calibration",
    "rollback_adjustment",
]
