"""Strict initial and follow-up finding-review contracts."""

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from .schemas import (
    SecurityAuditScope,
    SecurityAuditFindingLocation,
    _CANONICAL_REF_PATTERN,
    _FINDING_ID_PATTERN,
)


class SecurityFindingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(pattern=_FINDING_ID_PATTERN)
    decision: Literal["accepted", "rejected", "needs_more_evidence"]
    rationale: str = Field(min_length=1, max_length=3000)
    remediation_objective: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
        description="Required for accepted decisions; omit for rejected or needs_more_evidence decisions.",
    )
    verification_criteria: (
        list[Annotated[str, Field(min_length=1, max_length=1000)]] | None
    ) = Field(
        default=None,
        min_length=1,
        max_length=10,
        description="Nonempty JSON array required for accepted decisions; omit for rejected or needs_more_evidence decisions.",
    )
    evidence_requests: (
        list[Annotated[str, Field(min_length=1, max_length=1000)]] | None
    ) = Field(
        default=None,
        min_length=1,
        max_length=10,
        description="Nonempty JSON array required for needs_more_evidence decisions; omit for accepted or rejected decisions.",
    )

    @model_validator(mode="after")
    def _decision_fields(self) -> "SecurityFindingDecision":
        if self.decision == "accepted":
            valid = (
                self.remediation_objective is not None
                and self.verification_criteria is not None
                and self.evidence_requests is None
            )
        elif self.decision == "needs_more_evidence":
            valid = (
                self.evidence_requests is not None
                and self.remediation_objective is None
                and self.verification_criteria is None
            )
        else:
            valid = (
                self.remediation_objective is None
                and self.verification_criteria is None
                and self.evidence_requests is None
            )
        if not valid:
            raise ValueError(
                "accepted requires remediation_objective and verification_criteria; omit evidence_requests. "
                "needs_more_evidence requires evidence_requests; omit remediation_objective and verification_criteria. "
                "rejected must omit all three fields."
            )
        return self


class SecurityClosureDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(pattern=_FINDING_ID_PATTERN)
    outcome: Literal["resolved", "residual", "not_reproduced"]
    current_finding_ids: list[Annotated[str, Field(pattern=_FINDING_ID_PATTERN)]] = (
        Field(
            default_factory=list,
            max_length=200,
            description="Nonempty JSON array required only for residual outcomes; omit for resolved or not_reproduced.",
        )
    )
    rationale: str = Field(min_length=1, max_length=3000)
    next_step_guidance: str | None = Field(
        default=None,
        min_length=1,
        max_length=3000,
        description="Required for residual or not_reproduced outcomes; omit for resolved.",
    )

    @model_validator(mode="after")
    def _closure_fields(self) -> "SecurityClosureDecision":
        if len(self.current_finding_ids) != len(set(self.current_finding_ids)):
            raise ValueError("current finding IDs must be unique")
        if bool(self.current_finding_ids) != (self.outcome == "residual"):
            raise ValueError("only residual outcomes require current finding IDs")
        if (self.next_step_guidance is not None) != (self.outcome != "resolved"):
            raise ValueError("only nonresolved outcomes require next-step guidance")
        return self


class SecurityInitialReviewArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_kind: Literal["initial"]
    report_ref: str = Field(pattern=_CANONICAL_REF_PATTERN)
    finding_reviews: list[SecurityFindingDecision] = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    limitations: str = Field(default="", max_length=4000)


class SecurityFollowUpReviewArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_kind: Literal["follow_up"]
    prior_review_ref: str = Field(pattern=_CANONICAL_REF_PATTERN)
    rescan_report_ref: str = Field(pattern=_CANONICAL_REF_PATTERN)
    source_task_id: str | None = Field(
        default=None,
        min_length=1,
        description="Optional scheduled source task whose approved repair and latest rescan this follow-up links. Omit for independent report review.",
    )
    closure_reviews: list[SecurityClosureDecision] = Field(
        min_length=1,
        max_length=200,
        description="JSON array with exactly one object per finding marked accepted in prior_review_ref. Copy each prior accepted finding_id exactly once; no duplicate or extra IDs. These are prior finding IDs, not current rescan candidate IDs.",
    )
    new_finding_reviews: list[SecurityFindingDecision] = Field(
        default_factory=list,
        max_length=200,
        description="JSON array of decisions for unmatched current candidates; omit when there are none.",
    )
    summary: str = Field(min_length=1, max_length=4000)
    limitations: str = Field(default="", max_length=4000)


class SecurityReviewArgs(
    RootModel[
        Annotated[
            SecurityInitialReviewArgs | SecurityFollowUpReviewArgs,
            Field(discriminator="review_kind"),
        ]
    ]
):
    pass


class SecurityRemediationFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    title: str
    category: str
    severity: str
    confidence: str
    locations: list[SecurityAuditFindingLocation]
    evidence_ref: str | None = None
    scanner_finding_ids: list[str]
    evidence_summary: str
    remediation_objective: str
    verification_criteria: list[str]


class SecurityRepairSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_task_id: str = Field(min_length=1)
    review_ref: str = Field(pattern=_CANONICAL_REF_PATTERN)
    finding_id: str = Field(pattern=_FINDING_ID_PATTERN)
    write_paths: tuple[str, ...] = Field(min_length=1)


class SecurityFindingReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["security-finding-review/v1"] = "security-finding-review/v1"
    review_id: str
    review_kind: Literal["initial", "follow_up"]
    created_at: str
    source_report_ref: str = Field(pattern=_CANONICAL_REF_PATTERN)
    assessment_id: str
    scope: SecurityAuditScope
    report_author_agent_id: str
    reviewer_agent_id: str
    reviewer_session_id: str
    reviewer_trace_id: str
    review_status: Literal[
        "accepted",
        "rejected",
        "needs_more_evidence",
        "resolved",
        "residual",
        "not_reproduced",
        "mixed",
    ]
    finding_reviews: list[SecurityFindingDecision] = Field(default_factory=list)
    closure_reviews: list[SecurityClosureDecision] = Field(default_factory=list)
    new_finding_reviews: list[SecurityFindingDecision] = Field(default_factory=list)
    prior_review_ref: str | None = Field(default=None, pattern=_CANONICAL_REF_PATTERN)
    rescan_report_ref: str | None = Field(default=None, pattern=_CANONICAL_REF_PATTERN)
    rescan_revision: str | None = None
    summary: str
    limitations: str
    redaction_count: int
    evidence_refs: list[str]
    remediation_brief: list[SecurityRemediationFinding] = Field(default_factory=list)
