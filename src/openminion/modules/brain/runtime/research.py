from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


VerificationStatus = Literal[
    "supported",
    "unsupported",
    "contradicted",
    "indeterminate",
]


class ResearchStep(BaseModel):
    """One typed step in a research plan."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    scope: str = ""
    rationale: str = ""


class ResearchPlan(BaseModel):
    """Typed multi-step research plan."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    steps: list[ResearchStep] = Field(default_factory=list)


class Citation(BaseModel):
    """Typed citation linking a finding to its supporting evidence refs."""

    model_config = ConfigDict(extra="forbid")

    citation_id: str = Field(min_length=1)
    finding_ref: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class Claim(BaseModel):
    """Typed verifiable claim — a specific assertion to be checked."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    text: str = ""
    required_evidence_kinds: list[str] = Field(default_factory=list)


class ClaimVerificationResult(BaseModel):
    """Typed verification result for one claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    status: VerificationStatus = "indeterminate"
    supporting_evidence_refs: list[str] = Field(default_factory=list)
    policy_id: str = ""


class ResearchComposition(BaseModel):
    """Operator-facing composition view of a research run."""

    model_config = ConfigDict(extra="forbid")

    plan: ResearchPlan
    citations: list[Citation] = Field(default_factory=list)
    verifications: list[ClaimVerificationResult] = Field(default_factory=list)
    findings_count: int = Field(default=0, ge=0)
    evidence_refs_count: int = Field(default=0, ge=0)


def _clean_refs(refs: Iterable[str] | None) -> list[str]:
    return [ref for ref in (str(item or "").strip() for item in (refs or [])) if ref]


def build_research_step(
    *,
    step_id: str,
    query: str,
    scope: str = "",
    rationale: str = "",
) -> ResearchStep:
    """Build a typed research step from structural inputs."""
    return ResearchStep(
        step_id=str(step_id).strip(),
        query=str(query).strip(),
        scope=str(scope or "").strip(),
        rationale=str(rationale or "").strip(),
    )


def build_citation(
    *,
    citation_id: str,
    finding_ref: str,
    evidence_refs: Iterable[str] | None = None,
) -> Citation:
    """Build a typed citation linking a finding to its evidence refs."""
    return Citation(
        citation_id=str(citation_id).strip(),
        finding_ref=str(finding_ref).strip(),
        evidence_refs=_clean_refs(evidence_refs),
    )


def verify_claim(
    claim: Claim,
    *,
    supporting_evidence_refs: Iterable[str] | None = None,
    contradicting_evidence_refs: Iterable[str] | None = None,
    evidence_kinds_by_ref: Mapping[str, str] | None = None,
    policy_id: str = "",
) -> ClaimVerificationResult:
    """Produce a typed claim-verification result from typed evidence."""
    supporting_list = _clean_refs(supporting_evidence_refs)
    contradicting_list = _clean_refs(contradicting_evidence_refs)
    kinds_by_ref = dict(evidence_kinds_by_ref or {})

    if contradicting_list:
        return ClaimVerificationResult(
            claim_id=claim.claim_id,
            status="contradicted",
            supporting_evidence_refs=supporting_list,
            policy_id=str(policy_id or "").strip(),
        )

    if not supporting_list:
        return ClaimVerificationResult(
            claim_id=claim.claim_id,
            status="unsupported",
            supporting_evidence_refs=[],
            policy_id=str(policy_id or "").strip(),
        )

    required_kinds = {
        k
        for k in (str(item or "").strip() for item in claim.required_evidence_kinds)
        if k
    }
    if required_kinds:
        observed_kinds = {
            str(kinds_by_ref.get(ref, "") or "").strip() for ref in supporting_list
        }
        observed_kinds.discard("")
        if not required_kinds.issubset(observed_kinds):
            return ClaimVerificationResult(
                claim_id=claim.claim_id,
                status="indeterminate",
                supporting_evidence_refs=supporting_list,
                policy_id=str(policy_id or "").strip(),
            )

    return ClaimVerificationResult(
        claim_id=claim.claim_id,
        status="supported",
        supporting_evidence_refs=supporting_list,
        policy_id=str(policy_id or "").strip(),
    )


def build_research_composition(
    *,
    plan: ResearchPlan,
    citations: Iterable[Citation] | None = None,
    verifications: Iterable[ClaimVerificationResult] | None = None,
    findings_count: int = 0,
    evidence_refs_count: int = 0,
) -> ResearchComposition:
    """Build the operator-facing research-composition view."""
    return ResearchComposition(
        plan=plan,
        citations=list(citations or []),
        verifications=list(verifications or []),
        findings_count=max(0, int(findings_count or 0)),
        evidence_refs_count=max(0, int(evidence_refs_count or 0)),
    )


__all__ = [
    "VerificationStatus",
    "ResearchStep",
    "ResearchPlan",
    "Citation",
    "Claim",
    "ClaimVerificationResult",
    "ResearchComposition",
    "build_research_step",
    "build_citation",
    "verify_claim",
    "build_research_composition",
]
