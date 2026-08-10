from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openminion.base.config import OTELExporterConfig
from openminion.modules.telemetry.config import load_config
from openminion.modules.telemetry.events.catalog import EVENT_TYPES
from openminion.modules.telemetry.export.otel import event_export_dispositions
from openminion.modules.telemetry.service import resolve_telemetry_db_path
from openminion.modules.telemetry.trace.layout import resolve_trace_root

_TRACE_KIND_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("-http-response.json", "http_response"),
    ("-structured.json", "structured"),
    ("-http.json", "http_request"),
)


def build_catalog_report() -> dict[str, Any]:
    dispositions = event_export_dispositions()
    rows = []
    for event_type in sorted(EVENT_TYPES):
        rows.append(
            {
                "event_type": event_type,
                "otel_disposition": dispositions.get(event_type, "log"),
            }
        )
    return {
        "event_count": len(rows),
        "events": rows,
    }


def build_doctor_report(
    *,
    db_path: str | Path | None,
    home_root: str | Path | None,
) -> dict[str, Any]:
    path_info = resolve_telemetry_db_path(
        db_path=str(db_path) if db_path else None,
        home_root=home_root,
    )
    trace_root = resolve_trace_root(home_root=Path(home_root) if home_root else None)
    config = load_config(home_root=home_root)
    database = _database_status(Path(path_info.db_path))
    traces = _directory_status(trace_root)
    exporter = _exporter_status(config.otel_exporter)
    status = (
        "attention"
        if "unavailable" in {database["status"], traces["status"]}
        or exporter["status"] == "incomplete"
        else "ready"
    )
    return {
        "status": status,
        "database": database,
        "otel_exporter": exporter,
        "paths": {
            "db_path": path_info.db_path,
            "path_mode": path_info.path_mode,
            "path_source": path_info.path_source,
            "home_root": path_info.home_root,
            "trace_root": str(trace_root),
        },
        "trace_root": traces,
    }


def list_trace_files(
    *,
    trace_root: Path,
    limit: int,
    agent_id: str = "",
) -> dict[str, Any]:
    root = trace_root.expanduser().resolve(strict=False)
    files = sorted(
        (path for path in (root / "llm").rglob("*.json") if path.is_file()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    normalized_agent = agent_id.strip()
    rows = []
    for path in files:
        rel = path.relative_to(root)
        parts = rel.parts
        if normalized_agent and (len(parts) < 2 or parts[1] != normalized_agent):
            continue
        stat = path.stat()
        rows.append(
            {
                "path": str(rel),
                "kind": _trace_kind(path.name),
                "size_bytes": int(stat.st_size),
                "modified_at": _utc_iso(stat.st_mtime),
            }
        )
        if len(rows) >= limit:
            break
    return {
        "trace_root": str(root),
        "count": len(rows),
        "files": rows,
    }


def read_trace_file(*, trace_root: Path, trace_path: str) -> dict[str, Any]:
    root = trace_root.expanduser().resolve(strict=False)
    candidate = (root / trace_path).expanduser().resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("trace path must stay under trace root") from exc
    if not candidate.is_file():
        raise FileNotFoundError(str(candidate))
    text = candidate.read_text(encoding="utf-8")
    try:
        content: Any = json.loads(text)
    except json.JSONDecodeError:
        content = text
    stat = candidate.stat()
    return {
        "path": str(candidate.relative_to(root)),
        "kind": _trace_kind(candidate.name),
        "size_bytes": int(stat.st_size),
        "modified_at": _utc_iso(stat.st_mtime),
        "content": content,
    }


def _database_status(path: Path) -> dict[str, Any]:
    parent = path.parent
    exists = path.exists()
    writable = path.is_file() and os.access(path, os.R_OK | os.W_OK)
    parent_writable = _is_writable_directory(parent)
    creatable = not exists and _has_writable_ancestor(parent)
    return {
        "status": "ready" if writable or creatable else "unavailable",
        "exists": exists,
        "writable": writable,
        "parent_exists": parent.exists(),
        "parent_writable": parent_writable,
        "creatable": creatable,
    }


def _directory_status(path: Path) -> dict[str, Any]:
    exists = path.exists()
    writable = _is_writable_directory(path)
    creatable = not exists and _has_writable_ancestor(path.parent)
    return {
        "status": "ready" if writable or creatable else "unavailable",
        "exists": exists,
        "writable": writable,
        "creatable": creatable,
    }


def _is_writable_directory(path: Path) -> bool:
    return path.exists() and path.is_dir() and os.access(path, os.W_OK)


def _has_writable_ancestor(path: Path) -> bool:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return _is_writable_directory(candidate)


def _exporter_status(config: OTELExporterConfig) -> dict[str, Any]:
    enabled = config.enabled
    endpoint_configured = bool(config.endpoint.strip())
    if not enabled:
        status = "disabled"
    elif endpoint_configured:
        status = "ready"
    else:
        status = "incomplete"
    return {
        "status": status,
        "enabled": enabled,
        "endpoint_configured": endpoint_configured,
        "protocol": config.protocol,
        "sample_rate": config.sample_rate,
        "include_assistant_body": config.include_assistant_body,
        "noncritical_queue_capacity": config.noncritical_queue_capacity,
        "queue_flush_timeout_seconds": config.queue_flush_timeout_seconds,
    }


def _trace_kind(filename: str) -> str:
    for suffix, kind in _TRACE_KIND_SUFFIXES:
        if filename.endswith(suffix):
            return kind
    return "json"


def _utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(float(timestamp), tz=UTC).isoformat()


__all__ = [
    "build_catalog_report",
    "build_doctor_report",
    "list_trace_files",
    "read_trace_file",
]
