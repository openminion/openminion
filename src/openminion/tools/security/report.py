"""Publish one canonical candidate security-audit report."""

import hashlib
from pathlib import Path
import uuid
from typing import Any, Literal

from pydantic import ValidationError

from openminion.base.redaction import redact_sensitive_text
from openminion.modules.artifact.errors import ArtifactCtlError
from openminion.modules.tool import preferred_artifact_ref
from openminion.modules.tool.errors import ToolRuntimeError

from .config import display_target, resolve_local_target, resolve_security_config
from .interfaces import (
    TOOL_SECURITY_SCAN_ARTIFACT,
    TOOL_SECURITY_SCAN_CODE,
    TOOL_SECURITY_SCAN_DEPENDENCIES,
    TOOL_SECURITY_SCAN_SECRETS,
)
from .providers.trivy import TRIVY_FIXED_FLAGS
from .schemas import (
    ScanError,
    SecurityAuditCheck,
    SecurityAuditCheckResult,
    SecurityAuditFinding,
    SecurityAuditPublishArgs,
    SecurityAuditReport,
    SecurityConfigurationIdentity,
    SecurityScanResult,
)

_TOOL_BY_CHECK: dict[SecurityAuditCheck, str] = {
    "code": TOOL_SECURITY_SCAN_CODE,
    "dependencies": TOOL_SECURITY_SCAN_DEPENDENCIES,
    "iac": TOOL_SECURITY_SCAN_ARTIFACT,
    "secrets": TOOL_SECURITY_SCAN_SECRETS,
}
_SCANNER_BY_CHECK: dict[SecurityAuditCheck, tuple[str, str]] = {
    "code": ("semgrep", "code"),
    "dependencies": ("trivy", "vuln"),
    "iac": ("trivy", "misconfig"),
    "secrets": ("trivy", "secret"),
}


def publish_security_audit(
    args: SecurityAuditPublishArgs,
    *,
    ctx: Any,
) -> dict[str, Any]:
    if str(getattr(ctx, "permission_mode", "") or "") != "readonly":
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "security report publication requires readonly permission mode",
            {"reason_code": "security_report_requires_readonly"},
        )
    artifactctl = getattr(ctx, "artifactctl", None)
    if artifactctl is None:
        raise ToolRuntimeError(
            "INTERNAL_ERROR",
            "security report publication requires canonical artifact storage",
        )

    config = resolve_security_config(ctx)
    target = resolve_local_target(args.scope.target, config)
    target_name = display_target(target, config)
    scope = args.scope.model_copy(
        update={
            "target": target_name,
            "target_revision": args.scope.target_revision.lower(),
        }
    )
    checks, scans_by_ref, redaction_count = _resolve_checks(
        args,
        artifactctl=artifactctl,
        session_id=str(getattr(ctx, "session_id", "") or "").strip(),
        config=config,
        target=target_name,
        target_revision=scope.target_revision,
    )

    findings, finding_redactions = _resolve_findings(args.findings, scans_by_ref)
    redaction_count += finding_redactions

    objective, count = redact_sensitive_text(scope.objective)
    redaction_count += count
    summary, count = redact_sensitive_text(args.summary)
    redaction_count += count
    limitations, count = redact_sensitive_text(args.limitations)
    redaction_count += count
    scope = scope.model_copy(update={"objective": objective})
    execution_status = _execution_status(checks)
    assessment_id = uuid.uuid4().hex
    report = SecurityAuditReport(
        assessment_id=assessment_id,
        scope=scope,
        checks=checks,
        execution_status=execution_status,
        summary=summary,
        limitations=limitations,
        findings=findings,
        redaction_count=redaction_count,
        evidence_refs=[item.evidence_ref for item in args.check_evidence],
    )
    artifact = ctx.write_artifact(
        f"security/audit-{assessment_id}.json",
        report.model_dump_json(exclude_none=True, indent=2).encode(),
        "application/json",
        durable=True,
    )
    report_ref = preferred_artifact_ref(artifact)
    if not report_ref.startswith("artifact://sha256/"):
        raise ToolRuntimeError(
            "INTERNAL_ERROR",
            "security report publication did not produce a canonical artifact",
        )
    candidate_count = sum(item.disposition == "candidate" for item in findings)
    rejected_count = len(findings) - candidate_count
    return {
        "ok": True,
        "assessment_id": assessment_id,
        "schema_version": report.schema_version,
        "execution_status": execution_status,
        "review_status": report.review_status,
        "check_count": len(checks),
        "finding_count": len(findings),
        "candidate_count": candidate_count,
        "rejected_count": rejected_count,
        "redaction_count": redaction_count,
        "report_ref": report_ref,
        "artifact_ref": report_ref,
        "artifact_refs": [report_ref],
        "content": (
            f"Published unreviewed security audit {assessment_id} "
            f"with status={execution_status}"
        ),
    }


def _resolve_checks(
    args: SecurityAuditPublishArgs,
    *,
    artifactctl: Any,
    session_id: str,
    config: Any,
    target: str,
    target_revision: str,
) -> tuple[list[SecurityAuditCheckResult], dict[str, SecurityScanResult], int]:
    evidence_by_check = {item.check: item.evidence_ref for item in args.check_evidence}
    scans_by_ref: dict[str, SecurityScanResult] = {}
    checks: list[SecurityAuditCheckResult] = []
    redaction_count = 0
    for check in args.scope.requested_checks:
        evidence_ref = evidence_by_check[check]
        scan = _read_scan(
            artifactctl,
            evidence_ref,
            expected_tool=_TOOL_BY_CHECK[check],
            session_id=session_id,
        )
        configuration_identity = _validate_scan(
            scan,
            check=check,
            semgrep_config_sha256=(
                _semgrep_config_sha256(config) if check == "code" else ""
            ),
            target=target,
            target_revision=target_revision,
        )
        scans_by_ref[evidence_ref] = scan
        error = None
        if scan.error is not None:
            message, count = redact_sensitive_text(scan.error.message)
            redaction_count += count
            error = ScanError(
                code=scan.error.code,
                message=message,
                exit_code=scan.error.exit_code,
            )
        checks.append(
            SecurityAuditCheckResult(
                check=check,
                tool_id=scan.capability_id,
                permission_mode="readonly",
                evidence_ref=evidence_ref,
                target=scan.target,
                target_revision=scan.target_revision,
                scanner=scan.scanner,
                scanner_version=scan.scanner_version,
                configuration_identity=configuration_identity,
                status=scan.status,
                total_findings=scan.total_findings,
                returned_findings=scan.returned_findings,
                truncated=scan.truncated,
                error=error,
            )
        )
    return checks, scans_by_ref, redaction_count


def _read_scan(
    artifactctl: Any,
    evidence_ref: str,
    *,
    expected_tool: str,
    session_id: str,
) -> SecurityScanResult:
    try:
        metadata = artifactctl.get(evidence_ref)
        if metadata.deleted_at:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                "security scan evidence is deleted",
                {"evidence_ref": evidence_ref},
            )
        artifact_tool = str((metadata.meta_json or {}).get("tool_name") or "")
        if metadata.session_id != session_id or artifact_tool != expected_tool:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                "security scan evidence does not belong to this audit session and tool",
                {"evidence_ref": evidence_ref},
            )
        return SecurityScanResult.model_validate_json(
            artifactctl.read_bytes(evidence_ref)
        )
    except ToolRuntimeError:
        raise
    except (ArtifactCtlError, UnicodeDecodeError, ValidationError) as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "security scan evidence is missing, unreadable, or invalid",
            {"evidence_ref": evidence_ref},
        ) from exc


def _resolve_findings(
    findings: list[SecurityAuditFinding],
    scans_by_ref: dict[str, SecurityScanResult],
) -> tuple[list[SecurityAuditFinding], int]:
    resolved: list[SecurityAuditFinding] = []
    redaction_count = 0
    for finding in findings:
        if finding.basis == "scanner":
            scan = scans_by_ref.get(str(finding.evidence_ref))
            available_ids = (
                {item.finding_id for item in scan.findings} if scan else set()
            )
            if scan is None or not set(finding.scanner_finding_ids) <= available_ids:
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT",
                    "scanner finding IDs must exist in the named scan artifact",
                    {"finding_id": finding.finding_id},
                )
        safe_finding, count = _redact_finding(finding)
        redaction_count += count
        resolved.append(safe_finding)
    return resolved, redaction_count


def _validate_scan(
    scan: SecurityScanResult,
    *,
    check: SecurityAuditCheck,
    semgrep_config_sha256: str,
    target: str,
    target_revision: str,
) -> SecurityConfigurationIdentity:
    configuration = scan.configuration_identity
    scanner, scan_mode = _SCANNER_BY_CHECK[check]
    valid_configuration = (
        configuration is not None
        and configuration.scanner_version == scan.scanner_version
        and configuration.scan_mode == scan_mode
        and (
            configuration.config_sha256 == semgrep_config_sha256
            if check == "code"
            else configuration.fixed_flags == list(TRIVY_FIXED_FLAGS)
        )
    )
    usable = scan.status in {"completed", "partial"}
    coherent_result = (
        scan.ok == usable
        and scan.returned_findings == len(scan.findings)
        and scan.total_findings >= scan.returned_findings
        and ((scan.error is None) if usable else (scan.error is not None))
    )
    if (
        scan.capability_id != _TOOL_BY_CHECK[check]
        or scan.adapter_id != scanner
        or scan.scanner != scanner
        or (not scan.scanner_version and scan.status != "unavailable")
        or scan.target != target
        or scan.target_revision.lower() != target_revision
        or scan.permission_mode != "readonly"
        or not valid_configuration
        or not coherent_result
    ):
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "security scan evidence does not match the requested audit scope",
            {"check": check},
        )
    assert configuration is not None
    return configuration


def _semgrep_config_sha256(config: Any) -> str:
    path = Path(config.semgrep_config)
    if not path.is_absolute():
        path = config.workspace_root / path
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "security report requires the configured local Semgrep rules",
        ) from exc


def _redact_finding(
    finding: SecurityAuditFinding,
) -> tuple[SecurityAuditFinding, int]:
    updates: dict[str, str] = {}
    total = 0
    for field in (
        "title",
        "category",
        "severity",
        "confidence",
        "description",
        "impact",
        "validation",
        "recommendation",
        "evidence_summary",
    ):
        value, count = redact_sensitive_text(str(getattr(finding, field)))
        updates[field] = value
        total += count
    return finding.model_copy(update=updates), total


def _execution_status(
    checks: list[SecurityAuditCheckResult],
) -> Literal["completed", "partial", "blocked"]:
    if all(item.status == "completed" and not item.truncated for item in checks):
        return "completed"
    if any(item.status in {"completed", "partial"} for item in checks):
        return "partial"
    return "blocked"


__all__ = ["publish_security_audit"]
