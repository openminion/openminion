"""Trivy local dependency, IaC, and secret scan adapter."""

import hashlib
import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openminion.tools.security.config import SecurityConfig, display_target
from openminion.tools.security.interfaces import (
    TOOL_SECURITY_SCAN_ARTIFACT,
    TOOL_SECURITY_SCAN_DEPENDENCIES,
    TOOL_SECURITY_SCAN_SECRETS,
)
from openminion.tools.security.process import ScannerProcessResult, run_scanner
from openminion.tools.security.results import (
    bounded_message,
    build_scan_result,
    first_version_line,
    positive_int,
    relative_path,
)
from openminion.tools.security.schemas import (
    FindingLocation,
    LocalScanArgs,
    ScanError,
    SecurityConfigurationIdentity,
    SecurityFinding,
    SecurityScanResult,
)

_SEVERITIES = {
    "UNKNOWN": "unknown",
    "LOW": "low",
    "MEDIUM": "medium",
    "HIGH": "high",
    "CRITICAL": "critical",
}
TRIVY_FIXED_FLAGS = (
    "--format",
    "json",
    "--quiet",
    "--no-progress",
    "--exit-code",
    "1",
    "--skip-db-update",
    "--skip-java-db-update",
    "--skip-check-update",
)


def scan_dependencies(
    args: LocalScanArgs,
    *,
    target: Path,
    config: SecurityConfig,
    process_runner=run_scanner,
) -> SecurityScanResult:
    return _scan(
        args,
        target=target,
        config=config,
        capability_id=TOOL_SECURITY_SCAN_DEPENDENCIES,
        scanner_kind="vuln",
        process_runner=process_runner,
    )


def scan_artifact(
    args: LocalScanArgs,
    *,
    target: Path,
    config: SecurityConfig,
    process_runner=run_scanner,
) -> SecurityScanResult:
    return _scan(
        args,
        target=target,
        config=config,
        capability_id=TOOL_SECURITY_SCAN_ARTIFACT,
        scanner_kind="misconfig",
        process_runner=process_runner,
    )


def scan_secrets(
    args: LocalScanArgs,
    *,
    target: Path,
    config: SecurityConfig,
    process_runner=run_scanner,
) -> SecurityScanResult:
    return _scan(
        args,
        target=target,
        config=config,
        capability_id=TOOL_SECURITY_SCAN_SECRETS,
        scanner_kind="secret",
        process_runner=process_runner,
    )


def _scan(
    args: LocalScanArgs,
    *,
    target: Path,
    config: SecurityConfig,
    capability_id: str,
    scanner_kind: str,
    process_runner,
) -> SecurityScanResult:
    started = time.monotonic()
    started_at = datetime.now(UTC).isoformat()
    target_name = display_target(target, config)
    executable = shutil.which(config.trivy_executable)
    if not executable:
        return _trivy_result(
            capability_id=capability_id,
            target=target_name,
            started_at=started_at,
            started=started,
            status="unavailable",
            error=ScanError(code="DEPENDENCY_MISSING", message="Trivy is unavailable"),
        )

    version = process_runner(
        (executable, "--version"), cwd=config.workspace_root, timeout_seconds=10
    )
    if version.return_code != 0:
        return _trivy_result(
            capability_id=capability_id,
            target=target_name,
            started_at=started_at,
            started=started,
            status="unavailable",
            configuration_identity=SecurityConfigurationIdentity(
                scan_mode=scanner_kind,
                fixed_flags=list(TRIVY_FIXED_FLAGS),
            ),
            error=ScanError(
                code="DEPENDENCY_MISSING",
                message=bounded_message(version.stderr or "Trivy version check failed"),
                exit_code=version.return_code,
            ),
        )

    scanner_version = first_version_line(version.stdout)
    process = process_runner(
        (
            executable,
            "fs",
            *TRIVY_FIXED_FLAGS,
            "--scanners",
            scanner_kind,
            str(target),
        ),
        cwd=config.workspace_root,
        timeout_seconds=args.timeout_seconds,
    )
    return _parse_trivy_result(
        process,
        capability_id=capability_id,
        scanner_kind=scanner_kind,
        target=target,
        target_name=target_name,
        max_findings=args.max_findings,
        scanner_version=scanner_version,
        configuration_identity=SecurityConfigurationIdentity(
            scanner_version=scanner_version,
            scan_mode=scanner_kind,
            fixed_flags=list(TRIVY_FIXED_FLAGS),
        ),
        started_at=started_at,
        started=started,
    )


def _parse_trivy_result(
    process: ScannerProcessResult,
    *,
    capability_id: str,
    scanner_kind: str,
    target: Path,
    target_name: str,
    max_findings: int,
    scanner_version: str,
    configuration_identity: SecurityConfigurationIdentity,
    started_at: str,
    started: float,
) -> SecurityScanResult:
    if process.timed_out:
        return _trivy_result(
            capability_id=capability_id,
            target=target_name,
            started_at=started_at,
            started=started,
            scanner_version=scanner_version,
            configuration_identity=configuration_identity,
            status="timed_out",
            truncated=process.truncated,
            error=ScanError(
                code="TIMEOUT", message="Trivy scan timed out", exit_code=124
            ),
        )
    if process.cancelled:
        return _trivy_result(
            capability_id=capability_id,
            target=target_name,
            started_at=started_at,
            started=started,
            scanner_version=scanner_version,
            configuration_identity=configuration_identity,
            status="cancelled",
            truncated=process.truncated,
            error=ScanError(
                code="EXEC_ERROR",
                message="Trivy scan was cancelled",
                exit_code=process.return_code,
            ),
        )
    try:
        payload = json.loads(process.stdout)
        results = payload.get("Results", [])
        if not isinstance(results, list):
            raise TypeError("Results must be a list")
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        return _trivy_result(
            capability_id=capability_id,
            target=target_name,
            started_at=started_at,
            started=started,
            scanner_version=scanner_version,
            configuration_identity=configuration_identity,
            status="failed",
            truncated=process.truncated,
            error=ScanError(
                code="INVALID_RESPONSE",
                message=f"Trivy returned invalid JSON: {exc}",
                exit_code=process.return_code,
            ),
        )

    findings = _findings(results, scanner_kind=scanner_kind, target=target)
    returned = findings[:max_findings]
    status = (
        "completed"
        if process.return_code in {0, 1}
        else "partial"
        if findings
        else "failed"
    )
    reason = (
        ""
        if status == "completed"
        else f"Trivy exited with status {process.return_code}"
    )
    return _trivy_result(
        capability_id=capability_id,
        target=target_name,
        started_at=started_at,
        started=started,
        scanner_version=scanner_version,
        configuration_identity=configuration_identity,
        status=status,
        findings=returned,
        total_findings=len(findings),
        truncated=process.truncated or len(findings) > len(returned),
        partial_reason=reason,
        error=(
            ScanError(code="EXEC_ERROR", message=reason, exit_code=process.return_code)
            if status == "failed"
            else None
        ),
    )


def _findings(
    results: list[Any], *, scanner_kind: str, target: Path
) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        result_target = relative_path(str(result.get("Target") or ""), target)
        if scanner_kind == "vuln":
            findings.extend(
                _vulnerability(row, result_target)
                for row in _rows(result, "Vulnerabilities")
            )
        elif scanner_kind == "misconfig":
            findings.extend(
                _misconfiguration(row, result_target)
                for row in _rows(result, "Misconfigurations")
            )
        else:
            findings.extend(
                _secret(row, result_target) for row in _rows(result, "Secrets")
            )
    return findings


def _rows(result: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = result.get(key, [])
    return (
        [row for row in value if isinstance(row, dict)]
        if isinstance(value, list)
        else []
    )


def _vulnerability(row: dict[str, Any], path: str) -> SecurityFinding:
    rule_id = str(row.get("VulnerabilityID") or "trivy.unknown")
    component = str(row.get("PkgName") or "")
    severity = str(row.get("Severity") or "UNKNOWN").upper()
    references = (
        row.get("References") if isinstance(row.get("References"), list) else []
    )
    primary_url = str(row.get("PrimaryURL") or "")
    if primary_url:
        references = [primary_url, *references]
    return _trivy_finding(
        rule_id=rule_id,
        category="dependency",
        severity=severity,
        message=str(row.get("Title") or row.get("Description") or rule_id),
        path=path,
        component=component,
        installed_version=str(row.get("InstalledVersion") or ""),
        fixed_version=str(row.get("FixedVersion") or ""),
        references=references,
    )


def _misconfiguration(row: dict[str, Any], path: str) -> SecurityFinding:
    metadata = (
        row.get("CauseMetadata") if isinstance(row.get("CauseMetadata"), dict) else {}
    )
    rule_id = str(row.get("ID") or row.get("AVDID") or "trivy.unknown")
    severity = str(row.get("Severity") or "UNKNOWN").upper()
    references = (
        row.get("References") if isinstance(row.get("References"), list) else []
    )
    primary_url = str(row.get("PrimaryURL") or "")
    if primary_url:
        references = [primary_url, *references]
    return _trivy_finding(
        rule_id=rule_id,
        category="misconfiguration",
        severity=severity,
        message=str(row.get("Title") or row.get("Message") or rule_id),
        path=path,
        line=positive_int(metadata.get("StartLine")),
        end_line=positive_int(metadata.get("EndLine")),
        component=str(metadata.get("Resource") or ""),
        references=references,
    )


def _secret(row: dict[str, Any], path: str) -> SecurityFinding:
    rule_id = str(row.get("RuleID") or "trivy.secret")
    severity = str(row.get("Severity") or "UNKNOWN").upper()
    return _trivy_finding(
        rule_id=rule_id,
        category="secret",
        severity=severity,
        message=str(row.get("Title") or row.get("Category") or "Secret detected"),
        path=path,
        line=positive_int(row.get("StartLine")),
        end_line=positive_int(row.get("EndLine")),
    )


def _trivy_finding(
    *,
    rule_id: str,
    category: str,
    severity: str,
    message: str,
    path: str,
    line: int | None = None,
    end_line: int | None = None,
    component: str = "",
    installed_version: str = "",
    fixed_version: str = "",
    references: list[Any] | None = None,
) -> SecurityFinding:
    finding_id = hashlib.sha256(
        f"{rule_id}:{path}:{line or 0}:{component}".encode()
    ).hexdigest()[:24]
    return SecurityFinding(
        finding_id=finding_id,
        rule_id=rule_id,
        category=category,
        severity=severity,
        normalized_severity=_SEVERITIES.get(severity, "unknown"),
        message=bounded_message(message),
        location=FindingLocation(path=path, line=line, end_line=end_line),
        component=component,
        installed_version=installed_version,
        fixed_version=fixed_version,
        references=[
            str(value) for value in (references or [])[:20] if str(value).strip()
        ],
    )


def _trivy_result(
    *,
    capability_id: str,
    target: str,
    started_at: str,
    started: float,
    status: str,
    scanner_version: str = "",
    configuration_identity: SecurityConfigurationIdentity | None = None,
    findings: list[SecurityFinding] | None = None,
    total_findings: int = 0,
    truncated: bool = False,
    partial_reason: str = "",
    error: ScanError | None = None,
) -> SecurityScanResult:
    return build_scan_result(
        capability_id=capability_id,
        adapter_id="trivy",
        target=target,
        started_at=started_at,
        started=started,
        status=status,
        scanner_version=scanner_version,
        configuration_identity=configuration_identity,
        total_findings=total_findings,
        truncated=truncated,
        partial_reason=partial_reason,
        findings=findings,
        error=error,
    )


__all__ = [
    "TRIVY_FIXED_FLAGS",
    "scan_artifact",
    "scan_dependencies",
    "scan_secrets",
]
