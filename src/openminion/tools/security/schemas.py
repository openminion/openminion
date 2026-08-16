"""Typed security scan requests and normalized evidence."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ScanStatus = Literal[
    "completed",
    "partial",
    "unavailable",
    "failed",
    "cancelled",
    "timed_out",
]


class LocalScanArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1)
    max_findings: int = Field(default=100, ge=1, le=500)
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    include_evidence_artifact: bool = False


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
        payload["verified"] = self.status in {"completed", "partial"}
        payload["content"] = (
            f"{self.capability_id} status={self.status} "
            f"findings={self.returned_findings}/{self.total_findings}"
        )
        return payload


__all__ = [
    "FindingLocation",
    "LocalScanArgs",
    "ScanError",
    "ScanStatus",
    "SecurityFinding",
    "SecurityScanResult",
]
