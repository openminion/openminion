from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProjectVerificationDomain(StrEnum):
    CODING = "coding"
    RESEARCH = "research"
    OPERATIONS = "operations"
    CROSS_APPLICATION = "cross_application"


class ProjectDomainVerificationStatus(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    NEEDS_USER = "needs_user"
    FAILED = "failed"


class ProjectDomainVerificationContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: ProjectVerificationDomain
    required_evidence_kinds: tuple[str, ...] = Field(min_length=1)
    verifier_ref: str = Field(min_length=1)


class ProjectDomainVerificationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: ProjectVerificationDomain
    evidence_kinds: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    unsupported_reason: str | None = None
    needs_user_reason: str | None = None
    malformed: bool = False
    prose_only_completion: bool = False

    @model_validator(mode="after")
    def _require_evidence_refs(self) -> "ProjectDomainVerificationEvidence":
        if self.evidence_kinds and not self.evidence_refs:
            raise ValueError(
                "evidence_refs are required when evidence_kinds are present"
            )
        return self


class ProjectVerificationClosure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: ProjectVerificationDomain
    status: ProjectDomainVerificationStatus
    reason: str
    evidence_refs: tuple[str, ...] = ()
    missing_evidence_kinds: tuple[str, ...] = ()


def evaluate_project_verification_closure(
    contract: ProjectDomainVerificationContract,
    evidence: ProjectDomainVerificationEvidence,
) -> ProjectVerificationClosure:
    if evidence.domain != contract.domain:
        raise ValueError("verification evidence domain must match contract domain")
    if evidence.malformed or evidence.prose_only_completion:
        return ProjectVerificationClosure(
            domain=contract.domain,
            status=ProjectDomainVerificationStatus.FAILED,
            reason="malformed_or_prose_only_evidence",
            evidence_refs=evidence.evidence_refs,
        )
    unsupported_reason = (evidence.unsupported_reason or "").strip()
    if unsupported_reason:
        return ProjectVerificationClosure(
            domain=contract.domain,
            status=ProjectDomainVerificationStatus.BLOCKED,
            reason=f"unsupported_verification:{unsupported_reason}",
            evidence_refs=evidence.evidence_refs,
        )
    needs_user_reason = (evidence.needs_user_reason or "").strip()
    if needs_user_reason:
        return ProjectVerificationClosure(
            domain=contract.domain,
            status=ProjectDomainVerificationStatus.NEEDS_USER,
            reason=f"needs_user:{needs_user_reason}",
            evidence_refs=evidence.evidence_refs,
        )

    missing = tuple(
        kind
        for kind in contract.required_evidence_kinds
        if kind not in set(evidence.evidence_kinds)
    )
    if missing:
        return ProjectVerificationClosure(
            domain=contract.domain,
            status=ProjectDomainVerificationStatus.PARTIAL,
            reason="missing_required_evidence",
            evidence_refs=evidence.evidence_refs,
            missing_evidence_kinds=missing,
        )
    return ProjectVerificationClosure(
        domain=contract.domain,
        status=ProjectDomainVerificationStatus.VERIFIED,
        reason="required_evidence_present",
        evidence_refs=evidence.evidence_refs,
    )


__all__ = [
    "ProjectDomainVerificationContract",
    "ProjectDomainVerificationEvidence",
    "ProjectDomainVerificationStatus",
    "ProjectVerificationClosure",
    "ProjectVerificationDomain",
    "evaluate_project_verification_closure",
]
