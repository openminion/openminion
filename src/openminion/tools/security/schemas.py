"""Typed security scan requests and normalized evidence."""

import json
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ScanStatus = Literal[
    "completed",
    "partial",
    "unavailable",
    "failed",
    "cancelled",
    "timed_out",
]
SecurityAuditCheck = Literal["code", "dependencies", "iac", "secrets"]
_CANONICAL_REF_PATTERN = r"^artifact://sha256/[0-9a-f]{64}$"


class LocalScanArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1)
    max_findings: int = Field(default=100, ge=1, le=500)
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    include_evidence_artifact: bool = False
    expected_target_revision: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{40}$",
    )


class SecurityConfigurationIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scanner_version: str = Field(default="", max_length=120)
    scan_mode: Literal["code", "vuln", "misconfig", "secret"]
    config_sha256: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    fixed_flags: list[str] = Field(default_factory=list)


class FindingLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    line: int | None = None
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None


class SecurityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    rule_id: str
    category: str
    severity: str
    normalized_severity: Literal["unknown", "low", "medium", "high", "critical"]
    message: str
    location: FindingLocation
    confidence: str = ""
    component: str = ""
    installed_version: str = ""
    fixed_version: str = ""
    references: list[str] = Field(default_factory=list)


class ScanError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    exit_code: int | None = None


class SecurityScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    capability_id: str
    adapter_id: Literal["semgrep", "trivy"]
    scanner: Literal["semgrep", "trivy"]
    scanner_version: str = ""
    target: str
    target_revision: str = ""
    permission_mode: str = ""
    configuration_identity: SecurityConfigurationIdentity | None = None
    started_at: str
    duration_ms: int
    status: ScanStatus
    total_findings: int = 0
    returned_findings: int = 0
    truncated: bool = False
    partial_reason: str = ""
    findings: list[SecurityFinding] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    error: ScanError | None = None

    def as_tool_result(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude_none=True)
        payload["artifact_refs"] = list(self.evidence_refs)
        payload["verified"] = self.status in {"completed", "partial"}
        evidence = f" evidence={self.evidence_refs[0]}" if self.evidence_refs else ""
        payload["content"] = (
            f"{self.capability_id} status={self.status} "
            f"findings={self.returned_findings}/{self.total_findings}{evidence}"
        )
        return payload


class SecurityAuditScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=500)
    target_revision: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    objective: str = Field(min_length=1, max_length=2000)
    requested_checks: list[SecurityAuditCheck] = Field(min_length=1, max_length=4)
    activity_class: Literal["source_readonly"] = "source_readonly"

    @field_validator("requested_checks")
    @classmethod
    def _unique_checks(
        cls, checks: list[SecurityAuditCheck]
    ) -> list[SecurityAuditCheck]:
        if len(checks) != len(set(checks)):
            raise ValueError("requested_checks must be unique")
        return checks


class SecurityAuditEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: SecurityAuditCheck
    evidence_ref: str = Field(pattern=_CANONICAL_REF_PATTERN)


class SecurityAuditFindingLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @field_validator("path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        path = value.strip()
        if (
            not path
            or PurePosixPath(path).is_absolute()
            or PureWindowsPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or "\\" in path
        ):
            raise ValueError("finding paths must be target-relative POSIX paths")
        return PurePosixPath(path).as_posix()


class SecurityAuditFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    basis: Literal["scanner", "manual"]
    disposition: Literal["candidate", "rejected"]
    title: str = Field(min_length=1, max_length=300)
    category: str = Field(min_length=1, max_length=120)
    severity: str = Field(min_length=1, max_length=40)
    confidence: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=4000)
    impact: str = Field(default="", max_length=3000)
    validation: str = Field(default="", max_length=3000)
    recommendation: str = Field(default="", max_length=3000)
    locations: list[SecurityAuditFindingLocation] = Field(min_length=1, max_length=20)
    evidence_ref: str | None = Field(default=None, pattern=_CANONICAL_REF_PATTERN)
    scanner_finding_ids: list[str] = Field(default_factory=list, max_length=100)
    evidence_summary: str = Field(default="", max_length=3000)

    @model_validator(mode="after")
    def _basis_evidence(self) -> "SecurityAuditFinding":
        if self.basis == "scanner":
            if self.evidence_ref is None or not self.scanner_finding_ids:
                raise ValueError(
                    "scanner findings require evidence_ref and scanner_finding_ids"
                )
            if self.evidence_summary:
                raise ValueError("scanner findings must not use evidence_summary")
        else:
            if not self.evidence_summary:
                raise ValueError("manual findings require evidence_summary")
            if self.evidence_ref is not None or self.scanner_finding_ids:
                raise ValueError("manual findings must not use scanner evidence fields")
        return self


class SecurityAuditPublishArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: SecurityAuditScope
    check_evidence: list[SecurityAuditEvidenceRef] = Field(min_length=1, max_length=4)
    findings: list[SecurityAuditFinding] = Field(default_factory=list, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    limitations: str = Field(default="", max_length=4000)

    @field_validator("scope", mode="before")
    @classmethod
    def _decode_scope(cls, value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _exact_checks_and_unique_findings(self) -> "SecurityAuditPublishArgs":
        evidence_checks = [item.check for item in self.check_evidence]
        if len(evidence_checks) != len(set(evidence_checks)):
            raise ValueError("check_evidence must contain one ref per check")
        if set(evidence_checks) != set(self.scope.requested_checks):
            raise ValueError("check_evidence must exactly match requested_checks")
        finding_ids = [item.finding_id for item in self.findings]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("finding_id values must be unique")
        return self


class SecurityAuditCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: SecurityAuditCheck
    tool_id: str
    permission_mode: Literal["readonly"]
    evidence_ref: str = Field(pattern=_CANONICAL_REF_PATTERN)
    target: str
    target_revision: str
    scanner: Literal["semgrep", "trivy"]
    scanner_version: str
    configuration_identity: SecurityConfigurationIdentity
    status: ScanStatus
    total_findings: int
    returned_findings: int
    truncated: bool
    error: ScanError | None = None


class SecurityAuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["security-audit-report/v1"] = "security-audit-report/v1"
    assessment_id: str
    scope: SecurityAuditScope
    checks: list[SecurityAuditCheckResult]
    execution_status: Literal["completed", "partial", "blocked"]
    review_status: Literal["unreviewed"] = "unreviewed"
    summary: str
    limitations: str
    findings: list[SecurityAuditFinding]
    redaction_count: int = 0
    evidence_refs: list[str]


__all__ = [
    "FindingLocation",
    "LocalScanArgs",
    "ScanError",
    "ScanStatus",
    "SecurityAuditCheck",
    "SecurityAuditCheckResult",
    "SecurityAuditEvidenceRef",
    "SecurityAuditFinding",
    "SecurityAuditFindingLocation",
    "SecurityAuditPublishArgs",
    "SecurityAuditReport",
    "SecurityAuditScope",
    "SecurityConfigurationIdentity",
    "SecurityFinding",
    "SecurityScanResult",
]
