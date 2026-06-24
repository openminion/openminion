"""Self-eval rubric helpers for brain runtime."""

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SelfEvalCriterionId = Literal[
    "goal_satisfied",
    "evidence_collected",
    "risk_disclosed",
]


class SelfEvalRubric(BaseModel):
    """Typed self-eval rubric declared on disk."""

    model_config = ConfigDict(extra="forbid")

    rubric_id: str
    criterion_ids: list[SelfEvalCriterionId] = Field(default_factory=list)
    threshold: float = Field(default=1.0, ge=0.0, le=1.0)


class SelfEvalSubmission(BaseModel):
    """Typed self-eval submission with structured evidence only."""

    model_config = ConfigDict(extra="forbid")

    rubric_id: str
    per_criterion_passed: dict[SelfEvalCriterionId, bool] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)


class SelfEvalResult(BaseModel):
    """Scored self-eval result."""

    model_config = ConfigDict(extra="forbid")

    submission_ref: str
    computed_score: float = Field(ge=0.0, le=1.0)
    passed: bool
    policy_id: str = ""


class SelfEvalReconciliationFact(BaseModel):
    """Typed comparison fact between self-eval and external grading."""

    model_config = ConfigDict(extra="forbid")

    submission_ref: str
    self_passed: bool
    external_passed: bool
    disagreement: bool
    external_ref: str = ""


def _submission_ref(rubric_id: str, criterion_results: Mapping[str, bool]) -> str:
    ordered_pairs = [
        f"{criterion_id}={1 if bool(criterion_results[criterion_id]) else 0}"
        for criterion_id in sorted(criterion_results)
    ]
    return f"self_eval::{rubric_id}::{'|'.join(ordered_pairs)}"


def build_self_eval_submission(
    rubric: SelfEvalRubric | Mapping[str, Any],
    *,
    criterion_results: Mapping[str, bool],
    evidence_refs: list[str],
) -> SelfEvalSubmission:
    """Construct one typed self-eval submission from structured inputs."""

    rubric_obj = (
        rubric
        if isinstance(rubric, SelfEvalRubric)
        else SelfEvalRubric.model_validate(rubric)
    )
    normalized_results: dict[SelfEvalCriterionId, bool] = {}
    for criterion_id in rubric_obj.criterion_ids:
        if criterion_id not in criterion_results:
            raise ValueError(f"missing criterion result for {criterion_id}")
        normalized_results[criterion_id] = bool(criterion_results[criterion_id])
    return SelfEvalSubmission(
        rubric_id=str(rubric_obj.rubric_id or "").strip(),
        per_criterion_passed=normalized_results,
        evidence_refs=[str(ref).strip() for ref in evidence_refs if str(ref).strip()],
    )


def score_self_eval(
    submission: SelfEvalSubmission | Mapping[str, Any],
    *,
    rubric: SelfEvalRubric | Mapping[str, Any],
    policy_id: str,
) -> SelfEvalResult:
    """Score a typed self-eval submission structurally."""

    rubric_obj = (
        rubric
        if isinstance(rubric, SelfEvalRubric)
        else SelfEvalRubric.model_validate(rubric)
    )
    submission_obj = (
        submission
        if isinstance(submission, SelfEvalSubmission)
        else SelfEvalSubmission.model_validate(submission)
    )
    if submission_obj.rubric_id != rubric_obj.rubric_id:
        raise ValueError("submission.rubric_id must match rubric.rubric_id")
    criterion_ids = list(rubric_obj.criterion_ids)
    if not criterion_ids:
        raise ValueError("rubric.criterion_ids must be non-empty")
    passed_count = sum(
        1
        for criterion_id in criterion_ids
        if submission_obj.per_criterion_passed[criterion_id]
    )
    score = passed_count / len(criterion_ids)
    return SelfEvalResult(
        submission_ref=_submission_ref(
            submission_obj.rubric_id,
            submission_obj.per_criterion_passed,
        ),
        computed_score=score,
        passed=score >= rubric_obj.threshold,
        policy_id=str(policy_id or "").strip(),
    )


def compare_self_eval_vs_external(
    self_result: SelfEvalResult | Mapping[str, Any],
    external_report: Any,
) -> SelfEvalReconciliationFact:
    """Produce a typed disagreement fact without overriding either side."""

    self_result_obj = (
        self_result
        if isinstance(self_result, SelfEvalResult)
        else SelfEvalResult.model_validate(self_result)
    )
    if isinstance(external_report, Mapping):
        external_passed = bool(external_report.get("passed"))
        external_ref = str(
            external_report.get("transcript_name")
            or external_report.get("workflow_id")
            or external_report.get("report_id")
            or ""
        ).strip()
    else:
        if hasattr(external_report, "summary"):
            summary = getattr(external_report, "summary")
            external_passed = bool(getattr(summary, "passed", False))
            external_ref = str(
                getattr(summary, "transcript_name", "")
                or getattr(external_report, "report_version", "")
                or ""
            ).strip()
        else:
            external_passed = bool(getattr(external_report, "passed", False))
            external_ref = str(
                getattr(external_report, "transcript_name", "")
                or getattr(external_report, "workflow_id", "")
                or ""
            ).strip()
    return SelfEvalReconciliationFact(
        submission_ref=self_result_obj.submission_ref,
        self_passed=self_result_obj.passed,
        external_passed=external_passed,
        disagreement=self_result_obj.passed != external_passed,
        external_ref=external_ref,
    )


__all__ = [
    "SelfEvalCriterionId",
    "SelfEvalReconciliationFact",
    "SelfEvalResult",
    "SelfEvalRubric",
    "SelfEvalSubmission",
    "build_self_eval_submission",
    "compare_self_eval_vs_external",
    "score_self_eval",
]
