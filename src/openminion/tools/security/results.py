"""Shared mechanical normalization for scanner results."""

import time
from pathlib import Path
from typing import Any, Literal

from .schemas import (
    ScanError,
    ScanStatus,
    SecurityConfigurationIdentity,
    SecurityFinding,
    SecurityScanResult,
)


def build_scan_result(
    *,
    capability_id: str,
    adapter_id: Literal["semgrep", "trivy"],
    target: str,
    started_at: str,
    started: float,
    status: ScanStatus,
    scanner_version: str = "",
    configuration_identity: SecurityConfigurationIdentity | None = None,
    findings: list[SecurityFinding] | None = None,
    total_findings: int = 0,
    truncated: bool = False,
    partial_reason: str = "",
    error: ScanError | None = None,
) -> SecurityScanResult:
    returned = findings or []
    return SecurityScanResult(
        ok=status in {"completed", "partial"},
        capability_id=capability_id,
        adapter_id=adapter_id,
        scanner=adapter_id,
        scanner_version=scanner_version,
        configuration_identity=configuration_identity,
        target=target,
        started_at=started_at,
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        status=status,
        total_findings=total_findings,
        returned_findings=len(returned),
        truncated=truncated,
        partial_reason=partial_reason,
        findings=returned,
        error=error,
    )


def relative_path(raw_path: str, target: Path) -> str:
    if not raw_path:
        return "."
    path = Path(raw_path)
    if not path.is_absolute():
        return path.as_posix()
    base = target if target.is_dir() else target.parent
    try:
        return path.resolve(strict=False).relative_to(base).as_posix()
    except ValueError:
        return path.name


def positive_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) and value > 0 else None


def bounded_message(value: str, limit: int = 2000) -> str:
    return value.strip()[:limit]


def first_version_line(output: str) -> str:
    line = next((line.strip() for line in output.splitlines() if line.strip()), "")
    return bounded_message(line, 120)


__all__ = [
    "bounded_message",
    "build_scan_result",
    "first_version_line",
    "positive_int",
    "relative_path",
]
