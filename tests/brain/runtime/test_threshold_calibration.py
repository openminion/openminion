from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.modules.brain.runtime.verification.thresholds import (
    CalibrationInput,
    ThresholdAdjustment,
    ThresholdChangeDecision,
    ThresholdChangeProposal,
    apply_adjustment,
    decide_proposal,
    propose_calibration,
    rollback_adjustment,
)


def _performance_readout() -> dict[str, object]:
    return {
        "entries": [
            {
                "subject_kind": "strategy",
                "subject_id": "research",
                "success_count": 1,
                "failure_count": 3,
                "other_count": 2,
            }
        ]
    }


def _failure_readout() -> dict[str, object]:
    return {
        "rows": [
            {
                "seam_id": "low_progress",
                "reason_code": "low_progress_unknown",
                "recurrence_count": 4,
            },
            {
                "seam_id": "adaptive_termination",
                "reason_code": "ADAPTIVE_TERM_BUDGET",
                "recurrence_count": 3,
            },
            {
                "seam_id": "search_provider",
                "reason_code": "SEARCH_FAULT_NETWORK_TIMEOUT",
                "recurrence_count": 3,
            },
        ]
    }


def _current_thresholds() -> dict[str, object]:
    return {
        "high_risk_score_threshold": 70,
        "medium_risk_score_threshold": 40,
        "low_grounding_threshold": 0.5,
        "low_intent_confidence_threshold": 0.6,
        "high_ambiguity_threshold": 0.7,
        "repeat_failure_threshold": 2,
        "loop_count_threshold": 3,
        "replan_count_threshold": 3,
        "low_progress_ticks_threshold": 3,
        "low_progress_no_new_facts_threshold": 2,
        "low_progress_iterations_without_new_typed_record_threshold": 3,
        "low_progress_repeated_arg_signature_threshold": 2,
        "low_progress_unique_tool_call_count_delta_threshold": 2,
        "budget_pressure_threshold": 0.8,
        "tool_degraded_threshold": 0.8,
        "ask_threshold": 0.7,
    }


def test_propose_calibration_emits_typed_proposals_from_structural_metrics() -> None:
    proposals = propose_calibration(
        _performance_readout(),
        _failure_readout(),
        current_thresholds=_current_thresholds(),
    )
    by_id = {proposal.threshold_id: proposal for proposal in proposals}

    assert by_id["repeat_failure_threshold"].current_value == 2
    assert by_id["repeat_failure_threshold"].proposed_value == 1
    assert by_id["repeat_failure_threshold"].policy_id == "threshold_calibration_v1"
    assert by_id["repeat_failure_threshold"].rationale_facts["evidence_kind"] == (
        "failure.recurrence"
    )

    assert by_id["budget_pressure_threshold"].current_value == 0.8
    assert by_id["budget_pressure_threshold"].proposed_value == 0.75
    assert by_id["budget_pressure_threshold"].evidence_refs == [
        "failure:adaptive_termination|ADAPTIVE_TERM_BUDGET",
        "failure:low_progress|low_progress_unknown",
        "failure:search_provider|SEARCH_FAULT_NETWORK_TIMEOUT",
    ]

    assert by_id["ask_threshold"].current_value == 0.7
    assert by_id["ask_threshold"].proposed_value == 0.75
    assert by_id["ask_threshold"].evidence_refs == ["performance:research"]


def test_propose_calibration_is_honest_when_no_evidence_exists() -> None:
    assert (
        propose_calibration(
            {"entries": []},
            {"rows": []},
            current_thresholds=_current_thresholds(),
        )
        == []
    )


def test_propose_calibration_accepts_metactl_alias_fields() -> None:
    current_thresholds = SimpleNamespace(
        high_risk_score_threshold=70,
        low_grounding_threshold=0.5,
        repeat_error_threshold=2,
        stall_ticks_threshold=3,
        no_new_facts_threshold=3,
        low_progress_iterations_without_new_typed_record_threshold=3,
        low_progress_repeated_arg_signature_threshold=2,
        low_progress_unique_tool_call_count_delta_threshold=2,
        budget_pressure_threshold=0.8,
        ask_threshold=0.7,
    )
    proposals = propose_calibration(
        _performance_readout(),
        _failure_readout(),
        current_thresholds=current_thresholds,
    )
    proposal_ids = {proposal.threshold_id for proposal in proposals}
    assert "repeat_failure_threshold" in proposal_ids
    assert "loop_count_threshold" in proposal_ids
    assert "replan_count_threshold" in proposal_ids


def test_propose_calibration_is_deterministic() -> None:
    a = propose_calibration(
        _performance_readout(),
        _failure_readout(),
        current_thresholds=_current_thresholds(),
    )
    b = propose_calibration(
        _performance_readout(),
        _failure_readout(),
        current_thresholds=_current_thresholds(),
    )
    assert [item.model_dump(mode="json") for item in a] == [
        item.model_dump(mode="json") for item in b
    ]


@pytest.mark.parametrize("status", ["accepted", "rejected", "deferred"])
def test_decide_proposal_preserves_closed_set_status(status: str) -> None:
    proposal = ThresholdChangeProposal(
        threshold_id="repeat_failure_threshold",
        current_value=2,
        proposed_value=1,
        evidence_refs=["failure:low_progress|low_progress_unknown"],
        policy_id="threshold_calibration_v1",
        rationale_facts=CalibrationInput(
            threshold_id="repeat_failure_threshold",
            evidence_kind="failure.recurrence",
            evidence_refs=["failure:low_progress|low_progress_unknown"],
            observed_metric=4.0,
            target_metric=1.0,
        ).model_dump(mode="json"),
    )
    decision = decide_proposal(
        proposal,
        policy_id="operator_policy_v1",
        status=status,  # type: ignore[arg-type]
    )
    assert isinstance(decision, ThresholdChangeDecision)
    assert decision.status == status
    assert decision.policy_id == "operator_policy_v1"
    assert decision.proposal_ref.startswith(
        "threshold_change::repeat_failure_threshold::2::1"
    )


def test_apply_adjustment_emits_typed_event_to_audit_log() -> None:
    decision = decide_proposal(
        ThresholdChangeProposal(
            threshold_id="budget_pressure_threshold",
            current_value=0.8,
            proposed_value=0.75,
            evidence_refs=["failure:adaptive_termination|ADAPTIVE_TERM_BUDGET"],
            policy_id="threshold_calibration_v1",
            rationale_facts={},
        ),
        policy_id="operator_policy_v1",
    )
    audit_log: list[dict[str, object]] = []
    adjustment = apply_adjustment(decision, audit_log=audit_log)

    assert isinstance(adjustment, ThresholdAdjustment)
    assert adjustment.threshold_id == "budget_pressure_threshold"
    assert adjustment.prior_value == 0.8
    assert adjustment.new_value == 0.75
    assert adjustment.reversible is True
    assert audit_log == [
        {
            "event_type": "brain.threshold_adjustment",
            "payload": adjustment.model_dump(mode="json"),
        }
    ]


def test_apply_adjustment_is_inert_for_non_accepted_status() -> None:
    decision = ThresholdChangeDecision(
        proposal_ref="threshold_change::repeat_failure_threshold::2::1",
        status="deferred",
        policy_id="operator_policy_v1",
        decided_at="2026-05-13T00:00:00Z",
    )
    assert apply_adjustment(decision, audit_log=[]) is None


def test_rollback_adjustment_creates_typed_inverse_round_trip() -> None:
    decision = ThresholdChangeDecision(
        proposal_ref="threshold_change::repeat_failure_threshold::2::1",
        status="accepted",
        policy_id="operator_policy_v1",
        decided_at="2026-05-13T00:00:00Z",
    )
    audit_log: list[dict[str, object]] = []
    adjustment = apply_adjustment(decision, audit_log=audit_log)
    assert adjustment is not None

    inverse = rollback_adjustment(adjustment, audit_log=audit_log)
    assert inverse.threshold_id == "repeat_failure_threshold"
    assert inverse.prior_value == 1
    assert inverse.new_value == 2
    assert inverse.decision_ref.startswith("rollback::threshold_change::")
    assert len(audit_log) == 2


def test_tcal_schemas_avoid_anti_llm_field_drift() -> None:
    schema_fields = (
        set(CalibrationInput.model_fields.keys())
        | set(ThresholdChangeProposal.model_fields.keys())
        | set(ThresholdChangeDecision.model_fields.keys())
        | set(ThresholdAdjustment.model_fields.keys())
    )
    forbidden = (
        "seems",
        "appears",
        "smart",
        "recommend",
        "narrative",
        "summary",
        "heuristic",
        "confidence_text",
    )
    for field_name in schema_fields:
        for fragment in forbidden:
            assert fragment not in field_name
