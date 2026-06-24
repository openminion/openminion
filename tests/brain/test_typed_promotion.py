from __future__ import annotations

import pytest
from pydantic import ValidationError

from openminion.modules.brain.runtime.verification.policy import VerifierResult
from openminion.modules.brain.runtime.recurrence import (
    RecurringTaskShape,
)
from openminion.modules.brain.schemas.promotion import (
    CatalogPerformanceRecord,
    CatalogRetirementRecord,
    ContradictoryFailureBlock,
    ContradictoryFailureBlockConfig,
    PromotionCandidate,
    PromotionCandidateOutcomeRecord,
    RepetitionShape,
    StaleRetirementThreshold,
    compose_catalog_performance_record,
    compose_promotion_candidate,
    repetition_shape_from_recurring_task_shape,
)


# Fixtures (pure, no clock, no LLM)


def _rtse_shape(
    *,
    failure_pattern_refs: list[str] | None = None,
) -> RecurringTaskShape:
    return RecurringTaskShape(
        task_shape_ref="task_shape:search|web|research",
        strategy_id="search",
        capability_category="web",
        intent_category="research",
        recurrence_count=4,
        performance_entry_refs=["performance:search|web|research"],
        failure_pattern_refs=list(failure_pattern_refs or []),
    )


# RepetitionShape (SPRR-01 / SPRR-Q2)


def test_repetition_shape_projects_rtse_triple_verbatim() -> None:

    rtse_shape = _rtse_shape()
    projected = repetition_shape_from_recurring_task_shape(rtse_shape)
    assert isinstance(projected, RepetitionShape)
    assert projected.strategy_id == rtse_shape.strategy_id
    assert projected.capability_category == rtse_shape.capability_category
    assert projected.intent_category == rtse_shape.intent_category
    assert projected.recurring_task_shape_ref == rtse_shape.task_shape_ref
    assert projected.recurrence_count == rtse_shape.recurrence_count
    assert projected.performance_entry_refs == rtse_shape.performance_entry_refs
    assert projected.failure_pattern_refs == rtse_shape.failure_pattern_refs


def test_repetition_shape_rejects_empty_required_fields() -> None:
    with pytest.raises(ValidationError):
        RepetitionShape(
            recurring_task_shape_ref="",
            strategy_id="strat",
            capability_category="cap",
            intent_category="intent",
            recurrence_count=1,
        )


# PromotionCandidate composition (SPRR-01)


def test_compose_promotion_candidate_emits_typed_candidate_when_no_block() -> None:

    shape = repetition_shape_from_recurring_task_shape(_rtse_shape())
    result = compose_promotion_candidate(
        repetition_shape=shape,
        positive_outcome_refs=["performance:search|web|research"],
        proposer_policy_id="policy-sprr-1",
        proposed_at="2026-05-14T00:00:00Z",
        block_config=ContradictoryFailureBlockConfig(),
    )
    assert isinstance(result, PromotionCandidate)
    assert result.candidate_id.startswith("promotion_candidate:")
    assert result.repetition_shape == shape
    assert result.positive_outcome_refs == ["performance:search|web|research"]
    assert result.mission_type is None
    assert result.proposal_ref is None


def test_compose_promotion_candidate_is_deterministic() -> None:

    shape = repetition_shape_from_recurring_task_shape(_rtse_shape())
    one = compose_promotion_candidate(
        repetition_shape=shape,
        positive_outcome_refs=["performance:search|web|research"],
        proposer_policy_id="policy-sprr-1",
        proposed_at="2026-05-14T00:00:00Z",
        block_config=ContradictoryFailureBlockConfig(),
    )
    two = compose_promotion_candidate(
        repetition_shape=shape,
        positive_outcome_refs=["performance:search|web|research"],
        proposer_policy_id="policy-sprr-1",
        proposed_at="2026-05-14T00:00:00Z",
        block_config=ContradictoryFailureBlockConfig(),
    )
    assert isinstance(one, PromotionCandidate)
    assert isinstance(two, PromotionCandidate)
    assert one.candidate_id == two.candidate_id


def test_promotion_candidate_requires_positive_outcome_refs() -> None:

    shape = repetition_shape_from_recurring_task_shape(_rtse_shape())
    with pytest.raises(ValidationError):
        PromotionCandidate(
            candidate_id="promotion_candidate:abc",
            repetition_shape=shape,
            positive_outcome_refs=[],
            proposer_policy_id="policy-1",
            proposed_at="2026-05-14T00:00:00Z",
        )


def test_promotion_candidate_accepts_mission_type_verbatim() -> None:

    shape = repetition_shape_from_recurring_task_shape(_rtse_shape())
    candidate = PromotionCandidate(
        candidate_id="promotion_candidate:abc",
        repetition_shape=shape,
        positive_outcome_refs=["performance:search|web|research"],
        proposer_policy_id="policy-1",
        proposed_at="2026-05-14T00:00:00Z",
        mission_type="research",
    )
    assert candidate.mission_type == "research"


def test_promotion_candidate_rejects_unknown_mission_type() -> None:

    shape = repetition_shape_from_recurring_task_shape(_rtse_shape())
    with pytest.raises(ValidationError):
        PromotionCandidate(
            candidate_id="promotion_candidate:abc",
            repetition_shape=shape,
            positive_outcome_refs=["performance:search|web|research"],
            proposer_policy_id="policy-1",
            proposed_at="2026-05-14T00:00:00Z",
            mission_type="freeform-prose-mission",
        )


# Contradictory-failure blocking (SPRR-02 / SPRR-Q4)


def test_compose_emits_block_when_contradictory_signature_intersects() -> None:

    shape = repetition_shape_from_recurring_task_shape(
        _rtse_shape(failure_pattern_refs=["failure:search_provider|TIMEOUT"])
    )
    block_config = ContradictoryFailureBlockConfig(
        blocking_signatures=["failure:search_provider|TIMEOUT"],
        min_contradicting_failures=1,
    )
    result = compose_promotion_candidate(
        repetition_shape=shape,
        positive_outcome_refs=["performance:search|web|research"],
        proposer_policy_id="policy-1",
        proposed_at="2026-05-14T00:00:00Z",
        block_config=block_config,
    )
    assert isinstance(result, ContradictoryFailureBlock)
    assert result.intersecting_signatures == ["failure:search_provider|TIMEOUT"]
    assert result.repetition_shape == shape


def test_compose_does_not_block_below_threshold() -> None:

    shape = repetition_shape_from_recurring_task_shape(
        _rtse_shape(failure_pattern_refs=["failure:search_provider|TIMEOUT"])
    )
    block_config = ContradictoryFailureBlockConfig(
        blocking_signatures=["failure:search_provider|TIMEOUT"],
        min_contradicting_failures=2,
    )
    result = compose_promotion_candidate(
        repetition_shape=shape,
        positive_outcome_refs=["performance:search|web|research"],
        proposer_policy_id="policy-1",
        proposed_at="2026-05-14T00:00:00Z",
        block_config=block_config,
    )
    assert isinstance(result, PromotionCandidate)


# PromotionCandidateOutcomeRecord (SPRR-02 / SPRR-Q3)


def test_outcome_record_accepts_first_class_advisory_pattern_only() -> None:

    record = PromotionCandidateOutcomeRecord(
        candidate_ref="promotion_candidate:abc",
        outcome="advisory_pattern_only",
        review_ref="review:1",
        decided_at="2026-05-14T00:00:00Z",
    )
    assert record.outcome == "advisory_pattern_only"
    assert record.catalog_entry_id is None


def test_outcome_record_promoted_requires_catalog_entry_id() -> None:

    with pytest.raises(ValidationError):
        PromotionCandidateOutcomeRecord(
            candidate_ref="promotion_candidate:abc",
            outcome="promoted_to_catalog",
            review_ref="review:1",
            decided_at="2026-05-14T00:00:00Z",
        )


def test_outcome_record_rejects_catalog_entry_id_for_non_promotion() -> None:

    with pytest.raises(ValidationError):
        PromotionCandidateOutcomeRecord(
            candidate_ref="promotion_candidate:abc",
            outcome="rejected",
            review_ref="review:1",
            decided_at="2026-05-14T00:00:00Z",
            catalog_entry_id="catalog:1",
        )


def test_outcome_record_rejects_prose_outcome() -> None:

    with pytest.raises(ValidationError):
        PromotionCandidateOutcomeRecord(
            candidate_ref="promotion_candidate:abc",
            outcome="needs more thought",
            review_ref="review:1",
            decided_at="2026-05-14T00:00:00Z",
        )


# CatalogPerformanceRecord (SPRR-03 / SPRR-Q5 / SPRR-Q7)


def _verifier_result(*, passed: bool, target_id: str) -> VerifierResult:
    return VerifierResult(
        family="structural",
        goal_id="goal-1",
        run_id="run-1",
        target_id=target_id,
        passed=passed,
        reasons=[],
    )


def test_catalog_performance_record_consumes_verifier_results_verbatim() -> None:

    results = [
        _verifier_result(passed=True, target_id="criterion-1"),
        _verifier_result(passed=True, target_id="criterion-2"),
        _verifier_result(passed=False, target_id="criterion-3"),
    ]
    record = compose_catalog_performance_record(
        catalog_entry_id="catalog:42",
        verifier_results=results,
        last_invoked_at="2026-05-14T00:00:00Z",
    )
    assert record.catalog_entry_id == "catalog:42"
    assert record.success_count == 2
    assert record.failure_count == 1
    assert len(record.verifier_results) == 3


def test_catalog_performance_record_rejects_mismatched_counts() -> None:

    results = [_verifier_result(passed=True, target_id="criterion-1")]
    with pytest.raises(ValidationError):
        CatalogPerformanceRecord(
            catalog_entry_id="catalog:42",
            verifier_results=results,
            success_count=5,
            failure_count=0,
            last_invoked_at="2026-05-14T00:00:00Z",
        )


# CatalogRetirementRecord (SPRR-03 / SPRR-Q6)


def test_catalog_retirement_record_stale_requires_threshold() -> None:

    with pytest.raises(ValidationError):
        CatalogRetirementRecord(
            catalog_entry_id="catalog:1",
            signal="stale",
            recorded_at="2026-05-14T00:00:00Z",
        )


def test_catalog_retirement_record_superseded_by_pairing() -> None:

    record = CatalogRetirementRecord(
        catalog_entry_id="catalog:1",
        signal="superseded_by",
        superseded_by="catalog:2",
        recorded_at="2026-05-14T00:00:00Z",
    )
    assert record.superseded_by == "catalog:2"

    with pytest.raises(ValidationError):
        CatalogRetirementRecord(
            catalog_entry_id="catalog:1",
            signal="superseded_by",
            recorded_at="2026-05-14T00:00:00Z",
        )


def test_catalog_retirement_record_failing_outcome_requires_ref() -> None:

    record = CatalogRetirementRecord(
        catalog_entry_id="catalog:1",
        signal="failing_outcome",
        failing_performance_ref="performance:catalog:1",
        recorded_at="2026-05-14T00:00:00Z",
    )
    assert record.failing_performance_ref == "performance:catalog:1"

    with pytest.raises(ValidationError):
        CatalogRetirementRecord(
            catalog_entry_id="catalog:1",
            signal="failing_outcome",
            recorded_at="2026-05-14T00:00:00Z",
        )


def test_catalog_retirement_record_operator_decommissioned_requires_ref() -> None:

    record = CatalogRetirementRecord(
        catalog_entry_id="catalog:1",
        signal="operator_decommissioned",
        operator_decision_ref="op-decision:1",
        recorded_at="2026-05-14T00:00:00Z",
    )
    assert record.operator_decision_ref == "op-decision:1"

    with pytest.raises(ValidationError):
        CatalogRetirementRecord(
            catalog_entry_id="catalog:1",
            signal="operator_decommissioned",
            recorded_at="2026-05-14T00:00:00Z",
        )


def test_catalog_retirement_taxonomy_is_closed_four_member_set() -> None:

    with pytest.raises(ValidationError):
        CatalogRetirementRecord(
            catalog_entry_id="catalog:1",
            signal="freeform-prose",
            recorded_at="2026-05-14T00:00:00Z",
        )


# Integration: four-path outcome coverage (SPRR-04)


def test_integration_promotion_candidate_four_outcome_paths() -> None:

    rtse_shape = _rtse_shape()
    shape = repetition_shape_from_recurring_task_shape(rtse_shape)
    result = compose_promotion_candidate(
        repetition_shape=shape,
        positive_outcome_refs=["performance:search|web|research"],
        proposer_policy_id="policy-sprr-1",
        proposed_at="2026-05-14T00:00:00Z",
        block_config=ContradictoryFailureBlockConfig(),
    )
    assert isinstance(result, PromotionCandidate)
    candidate = result

    rejected = PromotionCandidateOutcomeRecord(
        candidate_ref=candidate.candidate_id,
        outcome="rejected",
        review_ref="review:rejected",
        decided_at="2026-05-14T00:01:00Z",
    )
    advisory = PromotionCandidateOutcomeRecord(
        candidate_ref=candidate.candidate_id,
        outcome="advisory_pattern_only",
        review_ref="review:advisory",
        decided_at="2026-05-14T00:02:00Z",
    )
    promoted = PromotionCandidateOutcomeRecord(
        candidate_ref=candidate.candidate_id,
        outcome="promoted_to_catalog",
        review_ref="review:promoted",
        decided_at="2026-05-14T00:03:00Z",
        catalog_entry_id="catalog:promoted-1",
    )
    assert rejected.outcome == "rejected"
    assert advisory.outcome == "advisory_pattern_only"
    assert promoted.outcome == "promoted_to_catalog"
    assert promoted.catalog_entry_id == "catalog:promoted-1"


def test_integration_contradictory_failure_block_path() -> None:

    rtse_shape = _rtse_shape(
        failure_pattern_refs=[
            "failure:search_provider|TIMEOUT",
            "failure:gateway_memory|OOM",
        ]
    )
    shape = repetition_shape_from_recurring_task_shape(rtse_shape)
    block_config = ContradictoryFailureBlockConfig(
        blocking_signatures=[
            "failure:search_provider|TIMEOUT",
            "failure:gateway_memory|OOM",
        ],
        min_contradicting_failures=2,
    )
    result = compose_promotion_candidate(
        repetition_shape=shape,
        positive_outcome_refs=["performance:search|web|research"],
        proposer_policy_id="policy-1",
        proposed_at="2026-05-14T00:00:00Z",
        block_config=block_config,
    )
    assert isinstance(result, ContradictoryFailureBlock)
    assert not isinstance(result, PromotionCandidate)
    assert set(result.intersecting_signatures) == {
        "failure:search_provider|TIMEOUT",
        "failure:gateway_memory|OOM",
    }


def test_stale_retirement_threshold_accepts_zero_days() -> None:

    threshold = StaleRetirementThreshold(unused_for_days=0)
    assert threshold.unused_for_days == 0


def test_stale_retirement_threshold_rejects_negative_days() -> None:

    with pytest.raises(ValidationError):
        StaleRetirementThreshold(unused_for_days=-1)
