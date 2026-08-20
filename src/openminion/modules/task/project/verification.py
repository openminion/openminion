from __future__ import annotations

import shlex
import shutil
import subprocess
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openminion.modules.task.autonomy import (
    TestEvidence,
    TestEvidenceStatus,
    now_ms,
)


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
    verifier_failed: bool = False

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
    if evidence.verifier_failed:
        return ProjectVerificationClosure(
            domain=contract.domain,
            status=ProjectDomainVerificationStatus.FAILED,
            reason="verifier_failed",
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


def validate_project_verifier(
    commands: tuple[str, ...],
    *,
    workspace: Path,
    required: bool,
) -> None:
    if not workspace.is_dir():
        raise ValueError(f"project workspace is not a directory: {workspace}")
    if required and not commands:
        raise ValueError("project verification commands are required")
    for command in commands:
        try:
            argv = tuple(shlex.split(command))
        except ValueError as exc:
            raise ValueError(
                f"verification command could not be parsed: {exc}"
            ) from exc
        if not argv:
            raise ValueError("verification command must not be empty")
        executable = Path(argv[0]).expanduser()
        if executable.is_absolute() or len(executable.parts) > 1:
            candidate = (
                executable if executable.is_absolute() else workspace / executable
            )
            if not candidate.is_file():
                raise ValueError(f"verification executable is unavailable: {argv[0]}")
        elif shutil.which(argv[0]) is None:
            raise ValueError(f"verification executable is unavailable: {argv[0]}")


def run_project_verification_commands(
    commands: tuple[str, ...],
    *,
    workspace: Path,
) -> tuple[TestEvidence, ...]:
    return tuple(
        _run_project_verification(command, workspace=workspace) for command in commands
    )


def _run_project_verification(command: str, *, workspace: Path) -> TestEvidence:
    started = now_ms()
    argv = tuple(shlex.split(command))
    try:
        completed = subprocess.run(
            argv,
            cwd=workspace,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return TestEvidence(
            command=argv,
            cwd_ref=str(workspace),
            started_at_ms=started,
            ended_at_ms=now_ms(),
            exit_code=None,
            status=TestEvidenceStatus.FAILED,
            summary=f"verification command failed to run: {type(exc).__name__}",
        )
    output = str(completed.stdout or "").strip()
    first_line = next(
        (line.strip() for line in output.splitlines() if line.strip()), ""
    )
    passed = completed.returncode == 0
    return TestEvidence(
        command=argv,
        cwd_ref=str(workspace),
        started_at_ms=started,
        ended_at_ms=now_ms(),
        exit_code=completed.returncode,
        passed=1 if passed else 0,
        failed=0 if passed else 1,
        status=TestEvidenceStatus.PASSED if passed else TestEvidenceStatus.FAILED,
        summary=first_line
        or (
            "verification command passed"
            if passed
            else f"verification command failed with exit code {completed.returncode}"
        ),
    )


__all__ = [
    "ProjectDomainVerificationContract",
    "ProjectDomainVerificationEvidence",
    "ProjectDomainVerificationStatus",
    "ProjectVerificationClosure",
    "ProjectVerificationDomain",
    "evaluate_project_verification_closure",
    "run_project_verification_commands",
    "validate_project_verifier",
]
