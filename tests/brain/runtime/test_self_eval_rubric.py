from __future__ import annotations

import pytest

from openminion.modules.brain.runtime.improvement.rubric import (
    SelfEvalReconciliationFact,
    SelfEvalResult,
    SelfEvalRubric,
    SelfEvalSubmission,
    build_self_eval_submission,
    compare_self_eval_vs_external,
    score_self_eval,
)


def _rubric() -> SelfEvalRubric:
    return SelfEvalRubric(
        rubric_id="research_quality_v1",
        criterion_ids=[
            "goal_satisfied",
            "evidence_collected",
            "risk_disclosed",
        ],
        threshold=2 / 3,
    )


def test_build_self_eval_submission_emits_structured_only_payload() -> None:
    submission = build_self_eval_submission(
        _rubric(),
        criterion_results={
            "goal_satisfied": True,
            "evidence_collected": True,
            "risk_disclosed": False,
        },
        evidence_refs=["memory:1", "artifact:2"],
    )
    assert isinstance(submission, SelfEvalSubmission)
    assert submission.rubric_id == "research_quality_v1"
    assert submission.per_criterion_passed == {
        "goal_satisfied": True,
        "evidence_collected": True,
        "risk_disclosed": False,
    }
    assert submission.evidence_refs == ["memory:1", "artifact:2"]


def test_build_self_eval_submission_requires_full_criterion_set() -> None:
    with pytest.raises(ValueError):
        build_self_eval_submission(
            _rubric(),
            criterion_results={
                "goal_satisfied": True,
                "evidence_collected": True,
            },
            evidence_refs=[],
        )


def test_score_self_eval_is_structural_and_deterministic() -> None:
    submission = build_self_eval_submission(
        _rubric(),
        criterion_results={
            "goal_satisfied": True,
            "evidence_collected": True,
            "risk_disclosed": False,
        },
        evidence_refs=["memory:1"],
    )
    a = score_self_eval(submission, rubric=_rubric(), policy_id="majority_threshold_v1")
    b = score_self_eval(submission, rubric=_rubric(), policy_id="majority_threshold_v1")
    assert a.model_dump(mode="json") == b.model_dump(mode="json")
    assert a.computed_score == 2 / 3
    assert a.passed is True
    assert a.policy_id == "majority_threshold_v1"


def test_compare_self_eval_vs_external_surfaces_disagreement_without_override() -> None:
    self_result = SelfEvalResult(
        submission_ref="self_eval::research_quality_v1::evidence_collected=1|goal_satisfied=1|risk_disclosed=0",
        computed_score=2 / 3,
        passed=True,
        policy_id="majority_threshold_v1",
    )
    external_report = {"passed": False, "transcript_name": "inference_validation_smoke"}
    fact = compare_self_eval_vs_external(self_result, external_report)
    assert isinstance(fact, SelfEvalReconciliationFact)
    assert fact.self_passed is True
    assert fact.external_passed is False
    assert fact.disagreement is True
    assert fact.external_ref == "inference_validation_smoke"


def test_self_eval_schema_has_no_freeform_reason_field() -> None:
    schema_fields = (
        set(SelfEvalRubric.model_fields.keys())
        | set(SelfEvalSubmission.model_fields.keys())
        | set(SelfEvalResult.model_fields.keys())
        | set(SelfEvalReconciliationFact.model_fields.keys())
    )
    forbidden = ("reason", "narrative", "summary", "flywheel", "parallel")
    for field_name in schema_fields:
        for fragment in forbidden:
            assert fragment not in field_name
