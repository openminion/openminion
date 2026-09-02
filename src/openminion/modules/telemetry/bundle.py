from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Any
from uuid import uuid4

from openminion.base.generated_paths import resolve_generated_root

from .inspection import (
    build_catalog_report,
    build_telemetry_debug_report,
)
from .invocation_inspection import read_safe_invocation_event_rows
from .service import TelemetryService

BUNDLE_SCHEMA_V1 = "openminion.telemetry_debug_bundle.v1"
BUNDLE_RESULT_SCHEMA_V1 = "openminion.telemetry_debug_bundle_result.v1"
REDACTION_POLICY_V1 = "openminion.telemetry_bundle_redaction.v1"
_OMITTED = sorted(
    {
        "absolute_paths",
        "content",
        "direct_identifiers",
        "endpoint_headers",
        "free_form_errors",
        "host_user_process",
        "raw_trace_payloads",
    }
)
_README = """# OpenMinion telemetry debug bundle

This bundle contains structural telemetry only. Direct identifiers, local
paths, endpoint metadata, free-form errors, and raw content were omitted.

Start with invocation-summary.json, then invocation-graph.json and
trace-summaries.json. Re-run telemetryctl debug invocation with the original
local identifier when trusted local access is available.
"""


class BundleError(RuntimeError):
    def __init__(self, code: str, category: str) -> None:
        super().__init__(code)
        self.code = code
        self.category = category


@dataclass(frozen=True)
class BundleResult:
    status: str
    bundle: dict[str, str] | None
    diagnostics: list[dict[str, Any]]
    error: dict[str, str] | None
    schema_version: str = BUNDLE_RESULT_SCHEMA_V1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "bundle": self.bundle,
            "diagnostics": self.diagnostics,
            "error": self.error,
        }


def create_debug_bundle(
    service: TelemetryService | None,
    *,
    invocation_id: str,
    data_root: Path,
    trace_root: Path,
    home_root: Path | None = None,
    output: str | None = None,
) -> BundleResult:
    report = build_telemetry_debug_report(
        service,
        selector_kind="invocation_id",
        invocation_id=invocation_id,
        trace_root=trace_root,
    )
    if report.error:
        raise BundleError(report.error.code, report.error.category)
    if service is None or report.invocation is None:
        raise BundleError("INVOCATION_NOT_FOUND", "not_found")
    bundle_id = f"bundle-{uuid4().hex}"
    root = data_root.expanduser().resolve(strict=False)
    final_path = _resolve_output(
        output,
        data_root=root,
        default_root=resolve_generated_root(home_root),
        bundle_id=bundle_id,
    )
    _validate_destination(final_path, data_root=root)
    temporary = final_path.parent / f".{final_path.name}.tmp-{uuid4().hex}"
    aliases: dict[str, str] = {}
    try:
        final_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _validate_destination(final_path, data_root=root)
        temporary.mkdir(mode=0o700)
        payloads = _bundle_payloads(
            service,
            report=report,
            invocation_id=invocation_id,
            aliases=aliases,
        )
        files: list[dict[str, Any]] = []
        for filename, section, payload in payloads:
            path = temporary / filename
            content = payload if isinstance(payload, str) else _json(payload)
            _write_private(path, content)
            files.append(_manifest_file(path, filename, section))
        manifest = {
            "schema_version": BUNDLE_SCHEMA_V1,
            "bundle_id": bundle_id,
            "files": sorted(files, key=lambda item: item["path"]),
            "manifest_mode": "0600",
            "omitted_categories": _OMITTED,
            "redaction_policy_version": REDACTION_POLICY_V1,
            "complete": True,
        }
        _write_private(temporary / "manifest.json", _json(manifest))
        os.replace(temporary, final_path)
        destination = final_path.relative_to(root).as_posix()
        return BundleResult(
            status="created",
            bundle={
                "bundle_id": bundle_id,
                "destination_relative": destination,
                "manifest_relative": f"{destination}/manifest.json",
            },
            diagnostics=[],
            error=None,
        )
    except PermissionError as exc:
        raise BundleError("BUNDLE_PERMISSION_FAILURE", "filesystem") from exc
    except OSError as exc:
        raise BundleError("BUNDLE_WRITE_FAILURE", "filesystem") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def error_result(error: BundleError) -> BundleResult:
    return BundleResult(
        status="error",
        bundle=None,
        diagnostics=[],
        error={"code": error.code, "category": error.category},
    )


def bundle_exit(result: BundleResult) -> int:
    if result.error is None:
        return 0
    return {
        "not_found": 1,
        "argument": 2,
        "security": 2,
        "storage": 3,
        "filesystem": 3,
        "internal": 3,
    }.get(result.error["category"], 3)


def _bundle_payloads(
    service: TelemetryService,
    *,
    report: Any,
    invocation_id: str,
    aliases: dict[str, str],
) -> list[tuple[str, str, dict[str, Any] | str]]:
    invocation = report.invocation
    alias = _alias(invocation_id, aliases)
    summary = {
        "invocation_id": alias,
        "outcome": invocation.outcome,
        "failure_code": invocation.failure_code,
        "started_at": invocation.started_at,
        "terminal_at": invocation.terminal_at,
        "duration_ms": invocation.duration_ms,
        "execution_count": invocation.execution_count,
        "trace_count": invocation.trace_count,
        "provider": invocation.provider,
        "model": invocation.model,
    }
    graph_rows = read_safe_invocation_event_rows(service, invocation_id)
    for row in graph_rows:
        for field in (
            "event_id",
            "invocation_id",
            "execution_id",
            "session_id",
            "turn_id",
            "trace_key",
            "agent_id",
            "llm_call_id",
        ):
            if field in row:
                row[field] = _alias(str(row[field]), aliases)
    traces = [
        {"trace_alias": f"trace-{index:03d}", "kind": _bundle_trace_kind(path)}
        for index, path in enumerate(report.links.trace_paths, start=1)
    ]
    doctor = {
        "status": report.status,
        "export": {
            "state": report.export_health.state,
            "enabled": report.export_health.enabled,
            "endpoint_configured": report.export_health.endpoint_configured,
            "protocol": report.export_health.protocol,
        },
    }
    payloads: list[tuple[str, str, dict[str, Any] | str]] = [
        ("README.md", "readme", _README),
        ("catalog.json", "catalog", build_catalog_report()),
        ("doctor.json", "doctor", doctor),
        (
            "invocation-graph.json",
            "invocation_graph",
            {"invocation_id": alias, "events": graph_rows},
        ),
        ("invocation-summary.json", "invocation_summary", summary),
        ("trace-summaries.json", "trace_summaries", {"traces": traces}),
    ]
    if invocation.usage is not None:
        payloads.append(
            (
                "token-summary.json",
                "token_summary",
                invocation.usage.to_dict(),
            )
        )
    return payloads


def _resolve_output(
    output: str | None,
    *,
    data_root: Path,
    default_root: Path,
    bundle_id: str,
) -> Path:
    if output is None:
        return default_root / "telemetry-debug-bundles" / bundle_id
    value = str(output)
    posix = PurePosixPath(value)
    if not value or ".." in posix.parts:
        raise BundleError("INVALID_ARGUMENT", "argument")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = data_root / candidate
    try:
        relative = candidate.relative_to(data_root)
    except ValueError as exc:
        raise BundleError("BUNDLE_OUTPUT_OUTSIDE_ROOT", "security") from exc
    current = data_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise BundleError("BUNDLE_SYMLINK_REFUSED", "security")
    return candidate


def _validate_destination(path: Path, *, data_root: Path) -> None:
    try:
        relative = path.relative_to(data_root)
    except ValueError as exc:
        raise BundleError("BUNDLE_OUTPUT_OUTSIDE_ROOT", "security") from exc
    current = data_root
    if current.is_symlink():
        raise BundleError("BUNDLE_SYMLINK_REFUSED", "security")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise BundleError("BUNDLE_SYMLINK_REFUSED", "security")
    if path.exists():
        raise BundleError("BUNDLE_DESTINATION_EXISTS", "security")


def _write_private(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    path.chmod(0o600)


def _manifest_file(path: Path, relative: str, section: str) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "path": relative,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "mode": "0600",
        "section": section,
    }


def _alias(value: Any, aliases: dict[str, str]) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if normalized not in aliases:
        aliases[normalized] = f"id-{len(aliases) + 1:03d}"
    return aliases[normalized]


def _bundle_trace_kind(value: str) -> str:
    if value.endswith("-raw.txt"):
        return "raw_request_omitted"
    if value.endswith("-http-response.json"):
        return "http_response_summary"
    if value.endswith("-http.json"):
        return "http_request_summary"
    return "structured_summary"


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


__all__ = [
    "BUNDLE_RESULT_SCHEMA_V1",
    "BUNDLE_SCHEMA_V1",
    "BundleError",
    "BundleResult",
    "bundle_exit",
    "create_debug_bundle",
    "error_result",
]
