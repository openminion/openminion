from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Error as SQLiteError
from typing import Generic, Literal, TypeVar

from openminion.base.errors.contracts import ErrorInfo

T = TypeVar("T")

OverviewState = Literal[
    "available",
    "degraded",
    "stale",
    "unavailable",
    "unknown",
]


@dataclass(frozen=True)
class OverviewSection(Generic[T]):
    status: OverviewState
    source: str
    observed_at: datetime | None
    data: T | None
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        if (
            self.status in {"available", "degraded", "stale"}
            and self.observed_at is None
        ):
            raise ValueError(f"{self.status} overview sections require observed_at")


@dataclass(frozen=True)
class RuntimeOverview:
    agent_id: str
    provider: str
    model: str
    session_id: str
    working_dir: str
    transport: str


@dataclass(frozen=True)
class WorkItemOverview:
    task_id: str
    title: str
    status: str


@dataclass(frozen=True)
class WorkOverview:
    count: int
    statuses: tuple[tuple[str, int], ...]
    items: tuple[WorkItemOverview, ...]


@dataclass(frozen=True)
class ToolActivityOverview:
    tool_name: str
    event_type: str
    timestamp: str


@dataclass(frozen=True)
class TelemetryOverview:
    invocation_id: str
    outcome: str
    duration_ms: int | None
    trace_count: int | None
    diagnostic_codes: tuple[str, ...]


@dataclass(frozen=True)
class HostOverview:
    system: str
    release: str
    machine: str
    python: str
    memory_used_percent: float | None
    disk_used_percent: float | None
    disk_free_bytes: int | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class OperationsOverview:
    runtime: OverviewSection[RuntimeOverview]
    work: OverviewSection[WorkOverview]
    recent_tools: OverviewSection[tuple[ToolActivityOverview, ...]]
    telemetry: OverviewSection[TelemetryOverview]
    host: OverviewSection[HostOverview]


def build_operations_overview(
    runtime: object,
    *,
    working_dir: str | Path,
    now: datetime | None = None,
) -> OperationsOverview:
    observed_at = now or datetime.now(timezone.utc)
    owner = getattr(runtime, "api_runtime", runtime)
    return OperationsOverview(
        runtime=_runtime_overview(runtime, working_dir=working_dir, now=observed_at),
        work=_work_overview(owner, runtime=runtime, now=observed_at),
        recent_tools=_recent_tools_overview(owner, runtime=runtime, now=observed_at),
        telemetry=_telemetry_overview(runtime, now=observed_at),
        host=_host_overview(working_dir=working_dir, now=observed_at),
    )


def _runtime_overview(
    runtime: object,
    *,
    working_dir: str | Path,
    now: datetime,
) -> OverviewSection[RuntimeOverview]:
    data = RuntimeOverview(
        agent_id=str(getattr(runtime, "agent_id", "") or ""),
        provider=str(getattr(runtime, "provider_name", "") or ""),
        model=str(getattr(runtime, "model_name", "") or ""),
        session_id=str(getattr(runtime, "session_id", "") or ""),
        working_dir=str(working_dir),
        transport=str(getattr(runtime, "transport", "") or ""),
    )
    missing = tuple(
        field
        for field in ("agent_id", "session_id", "working_dir")
        if not getattr(data, field)
    )
    if not missing:
        return OverviewSection("available", "interactive-runtime", now, data)
    return OverviewSection(
        "degraded",
        "interactive-runtime",
        now,
        data,
        ErrorInfo(
            code="OVERVIEW_RUNTIME_PARTIAL",
            message="Some runtime identity fields are unavailable.",
            details={"missing_fields": list(missing)},
        ),
    )


def _work_overview(
    owner: object,
    *,
    runtime: object,
    now: datetime,
) -> OverviewSection[WorkOverview]:
    from openminion.modules.task.surface import (
        build_task_surface,
        resolve_task_surface_source,
    )

    source = resolve_task_surface_source(owner)
    if source is None:
        return _unavailable("task-surface", "OVERVIEW_TASK_SOURCE_UNAVAILABLE")
    payload = build_task_surface(
        source,
        agent_id=str(getattr(runtime, "agent_id", "") or ""),
        session_id=str(getattr(runtime, "session_id", "") or ""),
        limit=20,
    ).inventory()
    tasks = list(payload.get("tasks", []))
    statuses = Counter(str(task.get("status", "unknown")) for task in tasks)
    data = WorkOverview(
        count=len(tasks),
        statuses=tuple(sorted(statuses.items())),
        items=tuple(
            WorkItemOverview(
                task_id=str(task.get("id", "")),
                title=str(task.get("title", "")),
                status=str(task.get("status", "unknown")),
            )
            for task in tasks[:5]
        ),
    )
    return OverviewSection("available", "task-surface", now, data)


def _recent_tools_overview(
    owner: object,
    *,
    runtime: object,
    now: datetime,
) -> OverviewSection[tuple[ToolActivityOverview, ...]]:
    session_store = getattr(owner, "sessions", None)
    getter = getattr(session_store, "get_recent_tool_events", None)
    session_id = str(getattr(runtime, "session_id", "") or "")
    if not callable(getter) or not session_id:
        return _unavailable(
            "session-store", "OVERVIEW_TOOL_ACTIVITY_SOURCE_UNAVAILABLE"
        )
    try:
        events = list(getter(session_id, 5) or [])
    except (
        AttributeError,
        OSError,
        RuntimeError,
        SQLiteError,
        TypeError,
        ValueError,
    ) as exc:
        return _failed(
            "session-store",
            now,
            "OVERVIEW_TOOL_ACTIVITY_READ_FAILED",
            exc,
        )
    data = tuple(
        ToolActivityOverview(
            tool_name=str(event.get("tool_name", "tool")),
            event_type=str(event.get("event_type", "")),
            timestamp=str(event.get("timestamp", "")),
        )
        for event in events
    )
    return OverviewSection("available", "session-store", now, data)


def _telemetry_overview(
    runtime: object,
    *,
    now: datetime,
) -> OverviewSection[TelemetryOverview]:
    from openminion.cli.presentation.telemetry import load_telemetry_report

    report = load_telemetry_report(
        runtime,
        selector_kind="latest",
        invocation_id=None,
    )
    if report.error is not None:
        return OverviewSection(
            "unavailable",
            "telemetry-inspection",
            None,
            None,
            ErrorInfo(
                code=report.error.code,
                message="Telemetry inspection is unavailable.",
                details={"category": report.error.category},
            ),
        )
    invocation = report.invocation
    if invocation is None:
        return OverviewSection(
            "available",
            "telemetry-inspection",
            now,
            TelemetryOverview("", "empty", None, 0, ()),
        )
    diagnostics = tuple(
        item.code
        for item in report.diagnostics
        if item.severity in {"error", "warning"}
    )
    status: OverviewState = "degraded" if diagnostics else "available"
    error = (
        ErrorInfo(
            code="OVERVIEW_TELEMETRY_DEGRADED",
            message="Telemetry reported diagnostics.",
            details={"diagnostic_codes": list(diagnostics)},
        )
        if diagnostics
        else None
    )
    return OverviewSection(
        status,
        "telemetry-inspection",
        now,
        TelemetryOverview(
            invocation_id=invocation.invocation_id,
            outcome=invocation.outcome,
            duration_ms=invocation.duration_ms,
            trace_count=invocation.trace_count,
            diagnostic_codes=diagnostics,
        ),
        error,
    )


def _host_overview(
    *,
    working_dir: str | Path,
    now: datetime,
) -> OverviewSection[HostOverview]:
    from openminion.tools.host import collect_host_metrics

    try:
        payload, warnings = collect_host_metrics(Path(working_dir))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return _failed("host.metrics", now, "OVERVIEW_HOST_READ_FAILED", exc)
    platform = dict(payload.get("platform", {}) or {})
    memory = dict(payload.get("memory", {}) or {})
    disks = list(payload.get("disk", []) or [])
    disk = dict(disks[0]) if disks else {}
    data = HostOverview(
        system=str(platform.get("system", "")),
        release=str(platform.get("release", "")),
        machine=str(platform.get("machine", "")),
        python=str(platform.get("python", "")),
        memory_used_percent=_optional_float(memory.get("used_percent")),
        disk_used_percent=_optional_float(disk.get("used_percent")),
        disk_free_bytes=_optional_int(disk.get("free_bytes")),
        warnings=tuple(warnings),
    )
    status: OverviewState = "degraded" if warnings else "available"
    error = (
        ErrorInfo(
            code="OVERVIEW_HOST_PARTIAL",
            message="Some host metrics are unavailable.",
            details={"warnings": list(warnings)},
        )
        if warnings
        else None
    )
    return OverviewSection(status, "host.metrics", now, data, error)


def render_operations_overview(snapshot: OperationsOverview) -> str:
    sections = [
        _render_runtime(snapshot.runtime),
        _render_work(snapshot.work),
        _render_tools(snapshot.recent_tools),
        _render_telemetry(snapshot.telemetry),
        _render_host(snapshot.host),
    ]
    return "\n\n".join(sections)


def _render_runtime(section: OverviewSection[RuntimeOverview]) -> str:
    lines = _section_header("Runtime", section)
    if section.data is not None:
        data = section.data
        lines.extend(
            (
                f"agent/model  {data.agent_id or '-'} · {data.provider or '-'}/{data.model or '-'}",
                f"session      {data.session_id or '-'}",
                f"workspace    {data.working_dir or '-'}",
                f"transport    {data.transport or '-'}",
            )
        )
    return "\n".join(lines)


def _render_work(section: OverviewSection[WorkOverview]) -> str:
    lines = _section_header("Active work", section)
    if section.data is not None:
        data = section.data
        status_text = ", ".join(f"{name}={count}" for name, count in data.statuses)
        lines.append(
            f"tasks        {data.count}" + (f" · {status_text}" if status_text else "")
        )
        lines.extend(
            f"  {item.status:<10} {item.task_id}  {item.title}" for item in data.items
        )
    return "\n".join(lines)


def _render_tools(
    section: OverviewSection[tuple[ToolActivityOverview, ...]],
) -> str:
    lines = _section_header("Recent tools", section)
    if section.data is not None:
        if not section.data:
            lines.append("none")
        lines.extend(
            f"  {item.timestamp or '-'}  {item.tool_name}  {item.event_type}"
            for item in section.data
        )
    return "\n".join(lines)


def _render_telemetry(section: OverviewSection[TelemetryOverview]) -> str:
    lines = _section_header("Telemetry", section)
    if section.data is not None:
        data = section.data
        duration = f"{data.duration_ms}ms" if data.duration_ms is not None else "-"
        lines.append(
            f"latest       {data.outcome or '-'} · duration={duration} · traces={data.trace_count if data.trace_count is not None else '-'}"
        )
        if data.invocation_id:
            lines.append(f"invocation   {data.invocation_id}")
    return "\n".join(lines)


def _render_host(section: OverviewSection[HostOverview]) -> str:
    lines = _section_header("Host", section)
    if section.data is not None:
        data = section.data
        lines.append(
            f"platform     {' '.join(part for part in (data.system, data.release, data.machine) if part) or '-'}"
        )
        lines.append(f"python       {data.python or '-'}")
        lines.append(f"memory       {_format_percent(data.memory_used_percent)} used")
        lines.append(
            f"workspace    {_format_percent(data.disk_used_percent)} used · {_format_bytes(data.disk_free_bytes)} free"
        )
    return "\n".join(lines)


def _section_header(name: str, section: OverviewSection[T]) -> list[str]:
    observed = (
        section.observed_at.isoformat() if section.observed_at else "not observed"
    )
    lines = [
        f"{name}  [{section.status}]",
        f"source       {section.source} · {observed}",
    ]
    if section.error is not None:
        lines.append(f"note         {section.error.code}: {section.error.message}")
    return lines


def _unavailable(source: str, code: str) -> OverviewSection[T]:
    return OverviewSection(
        "unavailable",
        source,
        None,
        None,
        ErrorInfo(
            code=code, message="The current runtime does not expose this source."
        ),
    )


def _failed(
    source: str,
    now: datetime,
    code: str,
    error: Exception,
) -> OverviewSection[T]:
    return OverviewSection(
        "degraded",
        source,
        now,
        None,
        ErrorInfo(
            code=code,
            message="The source could not be read.",
            details={"error_type": type(error).__name__},
        ),
    )


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float, str)) else None


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float, str)) else None


def _format_percent(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.1f}%"


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    amount = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024.0 or unit == "TiB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024.0
    return "unknown"


__all__ = [
    "HostOverview",
    "OperationsOverview",
    "OverviewSection",
    "OverviewState",
    "RuntimeOverview",
    "TelemetryOverview",
    "ToolActivityOverview",
    "WorkItemOverview",
    "WorkOverview",
    "build_operations_overview",
    "render_operations_overview",
]
