from __future__ import annotations

from typing import Any, Callable, Dict, Sequence

import pytest
from pydantic import ValidationError

from openminion.modules.brain.schemas.promotion import (
    CatalogPerformanceRecord,
    CatalogRetirementRecord,
    ContradictoryFailureBlock,
    ContradictoryFailureBlockConfig,
    PromotionCandidate,
    PromotionCandidateOutcomeRecord,
    RepetitionShape,
    StaleRetirementThreshold,
    compose_promotion_candidate,
)


# Closed-set forbidden field names; mirrors the TGCR + APBR + MTRR +
# ASRR + AATR rosters verbatim. The six lanes' rosters MUST stay aligned.
FORBIDDEN_FIELDS: Sequence[str] = (
    "verdict",
    "reasoning",
    "narrative",
    "judgment",
    "description_text",
    "completion_summary",
    "summary_text",
    "notes",
)


# Builders for the minimal-valid form of each typed record.


def _repetition_shape() -> RepetitionShape:
    return RepetitionShape(
        recurring_task_shape_ref="task_shape:strat|cap|intent",
        strategy_id="strat",
        capability_category="cap",
        intent_category="intent",
        recurrence_count=3,
        performance_entry_refs=["performance:strat|cap|intent"],
        failure_pattern_refs=[],
    )


def _repetition_shape_kwargs() -> Dict[str, Any]:
    return {
        "recurring_task_shape_ref": "task_shape:strat|cap|intent",
        "strategy_id": "strat",
        "capability_category": "cap",
        "intent_category": "intent",
        "recurrence_count": 3,
    }


def _contradictory_failure_block_config_kwargs() -> Dict[str, Any]:
    return {}  # both fields default


def _contradictory_failure_block_kwargs() -> Dict[str, Any]:
    return {
        "repetition_shape": _repetition_shape(),
        "intersecting_signatures": ["failure:search_provider|TIMEOUT"],
        "block_policy_id": "policy-1",
    }


def _promotion_candidate_kwargs() -> Dict[str, Any]:
    return {
        "candidate_id": "promotion_candidate:abc",
        "repetition_shape": _repetition_shape(),
        "positive_outcome_refs": ["performance:strat|cap|intent"],
        "proposer_policy_id": "policy-1",
        "proposed_at": "2026-05-14T00:00:00Z",
    }


def _promotion_candidate_outcome_record_kwargs() -> Dict[str, Any]:
    return {
        "candidate_ref": "promotion_candidate:abc",
        "outcome": "rejected",
        "review_ref": "review:1",
        "decided_at": "2026-05-14T00:00:00Z",
    }


def _stale_retirement_threshold_kwargs() -> Dict[str, Any]:
    return {"unused_for_days": 30}


def _catalog_performance_record_kwargs() -> Dict[str, Any]:
    return {
        "catalog_entry_id": "catalog:1",
        "verifier_results": [],
        "success_count": 0,
        "failure_count": 0,
        "last_invoked_at": "2026-05-14T00:00:00Z",
    }


def _catalog_retirement_record_kwargs() -> Dict[str, Any]:
    return {
        "catalog_entry_id": "catalog:1",
        "signal": "stale",
        "stale_threshold": StaleRetirementThreshold(unused_for_days=30),
        "recorded_at": "2026-05-14T00:00:00Z",
    }


_RECORDS: Sequence[tuple[str, Callable[..., Any], Callable[[], Dict[str, Any]]]] = (
    ("RepetitionShape", RepetitionShape, _repetition_shape_kwargs),
    (
        "ContradictoryFailureBlockConfig",
        ContradictoryFailureBlockConfig,
        _contradictory_failure_block_config_kwargs,
    ),
    (
        "ContradictoryFailureBlock",
        ContradictoryFailureBlock,
        _contradictory_failure_block_kwargs,
    ),
    ("PromotionCandidate", PromotionCandidate, _promotion_candidate_kwargs),
    (
        "PromotionCandidateOutcomeRecord",
        PromotionCandidateOutcomeRecord,
        _promotion_candidate_outcome_record_kwargs,
    ),
    (
        "StaleRetirementThreshold",
        StaleRetirementThreshold,
        _stale_retirement_threshold_kwargs,
    ),
    (
        "CatalogPerformanceRecord",
        CatalogPerformanceRecord,
        _catalog_performance_record_kwargs,
    ),
    (
        "CatalogRetirementRecord",
        CatalogRetirementRecord,
        _catalog_retirement_record_kwargs,
    ),
)


@pytest.mark.parametrize("record_name,ctor,kwargs_fn", _RECORDS)
@pytest.mark.parametrize("forbidden_field", FORBIDDEN_FIELDS)
def test_typed_record_rejects_forbidden_prose_field(
    record_name: str,
    ctor: Callable[..., Any],
    kwargs_fn: Callable[[], Dict[str, Any]],
    forbidden_field: str,
) -> None:

    kwargs = kwargs_fn()
    kwargs[forbidden_field] = "any-prose-shaped-value"
    with pytest.raises(ValidationError):
        ctor(**kwargs)


def test_forbidden_field_list_is_non_empty_and_unique() -> None:

    assert len(FORBIDDEN_FIELDS) > 0
    assert len(set(FORBIDDEN_FIELDS)) == len(FORBIDDEN_FIELDS)


def test_forbidden_field_roster_matches_sibling_lane_precedent() -> None:

    from tests.brain.test_aatr_forbidden_fields import (
        FORBIDDEN_FIELDS as AATR_FORBIDDEN_FIELDS,
    )
    from tests.brain.test_apbr_forbidden_fields import (
        FORBIDDEN_FIELDS as APBR_FORBIDDEN_FIELDS,
    )
    from tests.brain.test_asrr_forbidden_fields import (
        FORBIDDEN_FIELDS as ASRR_FORBIDDEN_FIELDS,
    )
    from tests.brain.test_mtrr_forbidden_fields import (
        FORBIDDEN_FIELDS as MTRR_FORBIDDEN_FIELDS,
    )
    from tests.brain.test_tgcr_forbidden_fields import (
        FORBIDDEN_FIELDS as TGCR_FORBIDDEN_FIELDS,
    )

    assert tuple(FORBIDDEN_FIELDS) == tuple(TGCR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(APBR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(MTRR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(ASRR_FORBIDDEN_FIELDS)
    assert tuple(FORBIDDEN_FIELDS) == tuple(AATR_FORBIDDEN_FIELDS)


def test_composer_constructed_records_also_reject_forbidden_fields() -> None:

    candidate_or_block = compose_promotion_candidate(
        repetition_shape=_repetition_shape(),
        positive_outcome_refs=["performance:strat|cap|intent"],
        proposer_policy_id="policy-1",
        proposed_at="2026-05-14T00:00:00Z",
        block_config=ContradictoryFailureBlockConfig(),
    )
    assert isinstance(candidate_or_block, PromotionCandidate)
    dumped = candidate_or_block.model_dump()
    dumped["narrative"] = "free-form prose"
    with pytest.raises(ValidationError):
        PromotionCandidate(**dumped)
