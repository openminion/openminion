"""Semgrep source-code scan adapter."""

import hashlib
import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openminion.tools.security.config import SecurityConfig, display_target
from openminion.tools.security.interfaces import TOOL_SECURITY_SCAN_CODE
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

_SEVERITIES = {"INFO": "low", "WARNING": "medium", "ERROR": "high"}


def scan_code(
    args: LocalScanArgs,
    *,
    target: Path,
    config: SecurityConfig,
    process_runner=run_scanner,
) -> SecurityScanResult:
    started = time.monotonic()
    started_at = datetime.now(UTC).isoformat()
    target_name = display_target(target, config)
    executable = shutil.which(config.semgrep_executable)
    if not executable:
        return _semgrep_result(
            target=target_name,
            started_at=started_at,
            started=started,
            status="unavailable",
            error=ScanError(
                code="DEPENDENCY_MISSING", message="Semgrep is unavailable"
            ),
        )
    if not config.semgrep_config:
        return _semgrep_result(
            target=target_name,
            started_at=started_at,
            started=started,
            status="unavailable",
            error=ScanError(
                code="DEPENDENCY_MISSING",
                message="Semgrep rule configuration is not configured",
            ),
        )

    config_path = Path(config.semgrep_config)
    config_sha256 = (
        hashlib.sha256(config_path.read_bytes()).hexdigest()
        if config_path.is_file()
        else ""
    )

    version = process_runner(
        (executable, "--version"), cwd=config.workspace_root, timeout_seconds=10
    )
    if version.return_code != 0:
        return _semgrep_result(
            target=target_name,
            started_at=started_at,
            started=started,
            status="unavailable",
            configuration_identity=SecurityConfigurationIdentity(
                scan_mode="code",
                config_sha256=config_sha256,
            ),
            error=ScanError(
                code="DEPENDENCY_MISSING",
                message=bounded_message(
                    version.stderr or "Semgrep version check failed"
                ),
                exit_code=version.return_code,
            ),
        )

    scanner_version = first_version_line(version.stdout)
    process = process_runner(
        (
            executable,
            "scan",
            "--json",
            "--quiet",
            "--metrics=off",
            "--error",
            "--config",
            config.semgrep_config,
            str(target),
        ),
        cwd=config.workspace_root,
        timeout_seconds=args.timeout_seconds,
    )
    return _parse_semgrep_result(
        process,
        target=target,
        target_name=target_name,
        max_findings=args.max_findings,
        scanner_version=scanner_version,
        configuration_identity=SecurityConfigurationIdentity(
            scanner_version=scanner_version,
            scan_mode="code",
            config_sha256=config_sha256,
        ),
        started_at=started_at,
        started=started,
    )


def _parse_semgrep_result(
    process: ScannerProcessResult,
    *,
    target: Path,
    target_name: str,
    max_findings: int,
    scanner_version: str,
    configuration_identity: SecurityConfigurationIdentity,
    started_at: str,
    started: float,
) -> SecurityScanResult:
    if process.timed_out:
        return _semgrep_result(
            target=target_name,
            started_at=started_at,
            started=started,
            scanner_version=scanner_version,
            configuration_identity=configuration_identity,
            status="timed_out",
            truncated=process.truncated,
            error=ScanError(
                code="TIMEOUT", message="Semgrep scan timed out", exit_code=124
            ),
        )
    if process.cancelled:
        return _semgrep_result(
            target=target_name,
            started_at=started_at,
            started=started,
            scanner_version=scanner_version,
            configuration_identity=configuration_identity,
            status="cancelled",
            truncated=process.truncated,
            error=ScanError(
                code="EXEC_ERROR",
                message="Semgrep scan was cancelled",
                exit_code=process.return_code,
            ),
        )
    try:
        payload = json.loads(process.stdout)
        rows = payload["results"]
        errors = payload.get("errors", [])
        if not isinstance(rows, list) or not isinstance(errors, list):
            raise TypeError("results and errors must be lists")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return _semgrep_result(
            target=target_name,
            started_at=started_at,
            started=started,
            scanner_version=scanner_version,
            configuration_identity=configuration_identity,
            status="failed",
            truncated=process.truncated,
            error=ScanError(
                code="INVALID_RESPONSE",
                message=f"Semgrep returned invalid JSON: {exc}",
                exit_code=process.return_code,
            ),
        )

    findings = [_semgrep_finding(row, target) for row in rows if isinstance(row, dict)]
    returned = findings[:max_findings]
    partial_reason = ""
    status = "completed"
    if errors:
        status = "partial" if findings else "failed"
        partial_reason = f"Semgrep reported {len(errors)} scan errors"
    elif process.return_code not in {0, 1}:
        status = "partial" if findings else "failed"
        partial_reason = f"Semgrep exited with status {process.return_code}"
    return _semgrep_result(
        target=target_name,
        started_at=started_at,
        started=started,
        scanner_version=scanner_version,
        configuration_identity=configuration_identity,
        status=status,
        findings=returned,
        total_findings=len(findings),
        truncated=process.truncated or len(findings) > len(returned),
        partial_reason=partial_reason,
        error=(
            ScanError(
                code="EXEC_ERROR",
                message=partial_reason,
                exit_code=process.return_code,
            )
            if status == "failed"
            else None
        ),
    )


def _semgrep_finding(row: dict[str, Any], target: Path) -> SecurityFinding:
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    start = row.get("start") if isinstance(row.get("start"), dict) else {}
    end = row.get("end") if isinstance(row.get("end"), dict) else {}
    metadata = extra.get("metadata") if isinstance(extra.get("metadata"), dict) else {}
    rule_id = str(row.get("check_id") or "semgrep.unknown")
    path = relative_path(str(row.get("path") or ""), target)
    line = positive_int(start.get("line"))
    fingerprint = str(extra.get("fingerprint") or "")
    finding_id = (
        fingerprint
        or hashlib.sha256(f"{rule_id}:{path}:{line or 0}".encode()).hexdigest()[:24]
    )
    severity = str(extra.get("severity") or "UNKNOWN").upper()
    references = metadata.get("references") or metadata.get("reference") or []
    if isinstance(references, str):
        references = [references]
    if not isinstance(references, list):
        references = []
    return SecurityFinding(
        finding_id=finding_id,
        rule_id=rule_id,
        category="code",
        severity=severity,
        normalized_severity=_SEVERITIES.get(severity, "unknown"),
        message=bounded_message(str(extra.get("message") or rule_id)),
        confidence=str(metadata.get("confidence") or ""),
        location=FindingLocation(
            path=path,
            line=line,
            column=positive_int(start.get("col")),
            end_line=positive_int(end.get("line")),
            end_column=positive_int(end.get("col")),
        ),
        references=[str(value) for value in references[:20] if str(value).strip()],
    )


def _semgrep_result(
    *,
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
        capability_id=TOOL_SECURITY_SCAN_CODE,
        adapter_id="semgrep",
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


__all__ = ["scan_code"]
