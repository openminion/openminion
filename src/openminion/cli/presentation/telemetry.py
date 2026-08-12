from __future__ import annotations

from pathlib import Path, PurePosixPath
import shlex
from typing import Any

from openminion.modules.telemetry.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH
from openminion.modules.telemetry.inspection import (
    TELEMETRY_INSPECTION_EXCEPTIONS,
    build_telemetry_debug_error,
    build_telemetry_debug_report,
    open_telemetry_inspection,
    read_trace_file,
    telemetry_storage_error_code,
)
from openminion.modules.telemetry.schemas import TelemetryDebugReport

TELEMETRY_USAGE = "usage: /telemetry [latest|failed|invocation <invocation-id>]"
TRACE_USAGE = "usage: /trace [list [--limit <1..100>]|show <relative-path>]"


def render_telemetry_slash(args: str, *, runtime: Any) -> str:
    try:
        parts = shlex.split(str(args or ""))
    except ValueError:
        return TELEMETRY_USAGE
    if not parts or parts == ["latest"]:
        selector_kind, invocation_id = "latest", None
    elif parts == ["failed"]:
        selector_kind, invocation_id = "failed", None
    elif len(parts) == 2 and parts[0] == "invocation":
        selector_kind, invocation_id = "invocation_id", parts[1]
    else:
        return TELEMETRY_USAGE
    report = _load_report(
        runtime,
        selector_kind=selector_kind,
        invocation_id=invocation_id,
    )
    if report.selection and report.selection.selected_invocation_id:
        setattr(
            runtime,
            "_interactive_telemetry_invocation_id",
            report.selection.selected_invocation_id,
        )
    return _render_card(report)


def render_trace_slash(args: str, *, runtime: Any) -> str:
    try:
        parts = shlex.split(str(args or ""))
    except ValueError:
        return TRACE_USAGE
    if not parts:
        parts = ["list", "--limit", "20"]
    if parts[0] == "list":
        limit = _trace_limit(parts[1:])
        if limit is None:
            return TRACE_USAGE
        report = _selected_report(runtime)
        if report.error:
            return f"telemetry: error\nerror: {report.error.code}"
        paths = report.links.trace_paths[:limit]
        if not paths:
            return "trace files: none"
        return "trace files:\n" + "\n".join(f"  {path}" for path in paths)
    if len(parts) == 2 and parts[0] == "show":
        report = _selected_report(runtime)
        if report.error:
            return f"telemetry: error\nerror: {report.error.code}"
        path = parts[1]
        if path not in report.links.trace_paths or not _relative_trace_name(path):
            return TRACE_USAGE
        try:
            payload = read_trace_file(
                trace_root=_runtime_data_root(runtime) / "traces",
                trace_path=path,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return f"trace: error\nerror: {telemetry_storage_error_code(exc)}"
        return (
            f"trace: {payload['path']}\n"
            f"kind: {payload['kind']}\n"
            f"size_bytes: {payload['size_bytes']}\n"
            f"modified_at: {payload['modified_at']}"
        )
    return TRACE_USAGE


def _selected_report(runtime: Any) -> TelemetryDebugReport:
    invocation_id = str(
        getattr(runtime, "_interactive_telemetry_invocation_id", "") or ""
    ).strip()
    return _load_report(
        runtime,
        selector_kind="invocation_id" if invocation_id else "latest",
        invocation_id=invocation_id or None,
    )


def _load_report(
    runtime: Any,
    *,
    selector_kind: str,
    invocation_id: str | None,
) -> TelemetryDebugReport:
    try:
        data_root = _runtime_data_root(runtime)
        with open_telemetry_inspection(
            db_path=data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH,
        ) as service:
            return build_telemetry_debug_report(
                service,
                selector_kind=selector_kind,
                invocation_id=invocation_id,
                trace_root=data_root / "traces",
            )
    except TELEMETRY_INSPECTION_EXCEPTIONS as exc:
        return build_telemetry_debug_error(
            telemetry_storage_error_code(exc),
            "storage",
        )


def _runtime_data_root(runtime: Any) -> Path:
    owner = getattr(runtime, "api_runtime", runtime)
    value = getattr(owner, "data_root", None)
    if value is None:
        raise RuntimeError("telemetry data root is unavailable")
    return Path(str(value)).expanduser().resolve(strict=False)


def _render_card(report: TelemetryDebugReport) -> str:
    if report.error:
        return f"telemetry: error\nerror: {report.error.code}"
    invocation = report.invocation
    if invocation is None:
        return "telemetry: empty"
    usage = invocation.usage
    duration = (
        f"{invocation.duration_ms / 1000:.1f}s"
        if invocation.duration_ms is not None
        else "-"
    )
    model = (
        f"{invocation.provider}/{invocation.model}"
        if invocation.provider and invocation.model
        else "-"
    )
    failure = next(
        (item.code for item in report.diagnostics if item.severity == "error"),
        "-",
    )
    input_tokens = usage.input_tokens if usage else "-"
    output_tokens = usage.output_tokens if usage else "-"
    cost = usage.cost_usd if usage and usage.cost_usd is not None else "-"
    return "\n".join(
        (
            "latest failed invocation",
            f"id: {invocation.invocation_id}",
            f"agent: {invocation.agent_ids[0] if invocation.agent_ids else '-'}",
            f"session: {invocation.session_ids[0] if invocation.session_ids else '-'}",
            f"status: {invocation.outcome}",
            f"duration: {duration}",
            f"model: {model}",
            f"tokens: input={input_tokens} output={output_tokens} cost={cost}",
            f"failure: {failure}",
            f"trace files: {invocation.trace_count if invocation.trace_count is not None else '-'}",
            "next: open graph | show traces | copy bundle command",
        )
    )


def _trace_limit(parts: list[str]) -> int | None:
    if not parts:
        return 20
    if len(parts) != 2 or parts[0] != "--limit":
        return None
    try:
        limit = int(parts[1])
    except ValueError:
        return None
    return limit if 1 <= limit <= 100 else None


def _relative_trace_name(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value and not path.is_absolute() and ".." not in path.parts)


__all__ = [
    "TELEMETRY_USAGE",
    "TRACE_USAGE",
    "render_telemetry_slash",
    "render_trace_slash",
]
