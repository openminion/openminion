from __future__ import annotations

import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from openminion.base.config import OTELExporterConfig
from openminion.base.constants import OPENMINION_MODULE_STANDALONE_ENV
from openminion.modules.telemetry.config import load_config
from openminion.modules.telemetry.debug_usage import aggregate_debug_usage
from openminion.modules.telemetry.events.catalog import EVENT_TYPES
from openminion.modules.telemetry.export.otel import event_export_dispositions
from openminion.modules.telemetry.export.health import build_debug_export_health
from openminion.modules.telemetry.schemas import (
    TelemetryDebugDiagnostic,
    TelemetryDebugError,
    TelemetryDebugExportHealth,
    TelemetryDebugInvocation,
    TelemetryDebugLinks,
    TelemetryDebugReport,
    TelemetryDebugSelection,
)
from openminion.modules.telemetry.service import resolve_telemetry_db_path
from openminion.modules.telemetry.service import TelemetryService
from openminion.modules.telemetry.storage.base import (
    TelemetryEventPageRow,
    TelemetryStore,
    telemetry_event_sort_key,
)
from openminion.modules.telemetry.trace.layout import resolve_trace_root

_TRACE_KIND_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("-raw.txt", "raw_request"),
    ("-http-response.json", "http_response"),
    ("-structured.json", "structured"),
    ("-http.json", "http_request"),
)
_INVOCATION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_TERMINAL_TYPES = (
    "agent.invocation.completed",
    "agent.invocation.failed",
    "agent.invocation.cancelled",
)
_TERMINAL_RANK = {
    "agent.invocation.completed": 1,
    "agent.invocation.cancelled": 2,
    "agent.invocation.failed": 3,
}
TELEMETRY_INSPECTION_EXCEPTIONS = (
    OSError,
    RuntimeError,
    sqlite3.Error,
    TypeError,
    ValueError,
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


@contextmanager
def open_telemetry_inspection(
    *,
    db_path: str | Path | None = None,
    home_root: str | Path | None = None,
) -> Iterator[TelemetryService | None]:
    path_info = resolve_telemetry_db_path(
        db_path=str(db_path) if db_path else None,
        home_root=home_root,
    )
    path = Path(path_info.db_path)
    if not path.is_file():
        if path.exists():
            raise IsADirectoryError(str(path))
        if not path.parent.is_dir() or not os.access(path.parent, os.R_OK | os.X_OK):
            raise PermissionError(str(path.parent))
        yield None
        return
    if not os.access(path, os.R_OK):
        raise PermissionError(str(path))
    wal_path = Path(f"{path}-wal")
    shm_path = Path(f"{path}-shm")
    if wal_path.exists() != shm_path.exists():
        raise RuntimeError("partial SQLite WAL sidecars")
    for sidecar in (wal_path, shm_path):
        if sidecar.exists() and not os.access(sidecar, os.R_OK):
            raise PermissionError(str(sidecar))
    snapshot = tempfile.TemporaryDirectory(prefix="openminion-telemetry-inspection-")
    inspection_path = Path(snapshot.name) / path.name
    sources = (path, wal_path, shm_path) if wal_path.exists() else (path,)
    for source in sources:
        suffix = source.name.removeprefix(path.name)
        shutil.copy2(source, Path(f"{inspection_path}{suffix}"))
    service = TelemetryService(
        db_path=str(inspection_path),
        env={OPENMINION_MODULE_STANDALONE_ENV: "1"},
        read_only=True,
    )
    try:
        yield service
    finally:
        service.close_sync()
        snapshot.cleanup()


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
                "kind": trace_kind(path.name),
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


def read_trace_file(
    *,
    trace_root: Path,
    trace_path: str,
    include_content: bool = False,
) -> dict[str, Any]:
    root = trace_root.expanduser()
    if not _safe_trace_path(root, trace_path):
        raise ValueError(
            "trace path must name a readable, non-symlinked trace artifact"
        )
    candidate = root.joinpath(*PurePosixPath(trace_path).parts)
    stat = candidate.stat()
    payload: dict[str, Any] = {
        "path": str(candidate.relative_to(root)),
        "kind": trace_kind(candidate.name),
        "size_bytes": int(stat.st_size),
        "modified_at": _utc_iso(stat.st_mtime),
    }
    if include_content:
        text = candidate.read_text(encoding="utf-8")
        try:
            payload["content"] = json.loads(text)
        except json.JSONDecodeError:
            payload["content"] = text
    return payload


def _debug_argument_error(
    selector_kind: str,
    invocation_id: str | None,
    export_health: TelemetryDebugExportHealth,
) -> TelemetryDebugReport | None:
    valid_selector = selector_kind in {"latest", "failed", "invocation_id"}
    valid_id = selector_kind != "invocation_id" or bool(
        _INVOCATION_ID_RE.fullmatch(str(invocation_id or ""))
    )
    unexpected_id = selector_kind != "invocation_id" and invocation_id is not None
    if valid_selector and valid_id and not unexpected_id:
        return None
    return _debug_error_report("INVALID_ARGUMENT", "argument", export_health)


def _selected_debug_report(
    *,
    selector_kind: str,
    source: str,
    selected_id: str,
    high_water: int,
    invocation: TelemetryDebugInvocation,
    trace_paths: list[str],
    diagnostics: list[TelemetryDebugDiagnostic],
    export_health: TelemetryDebugExportHealth,
) -> TelemetryDebugReport:
    if _INVOCATION_ID_RE.fullmatch(selected_id):
        commands = [
            f"telemetryctl invocation graph {selected_id}",
            f"telemetryctl invocation show {selected_id}",
        ]
    else:
        diagnostics.append(
            TelemetryDebugDiagnostic(
                "UNADDRESSABLE_INVOCATION_ID", "warning", {"invocation_id": selected_id}
            )
        )
        commands = []
    needs_attention = invocation.outcome in {"failed", "cancelled"} or any(
        item.code == "CONFLICTING_TERMINALS" for item in diagnostics
    )
    return TelemetryDebugReport(
        status="attention" if needs_attention else "ready",
        selection=TelemetryDebugSelection(
            selector_kind, source, selected_id, high_water
        ),
        invocation=invocation,
        diagnostics=_sorted_diagnostics(diagnostics),
        links=TelemetryDebugLinks(sorted(commands), trace_paths),
        export_health=export_health,
    )


def _empty_debug_report(
    selector_kind: str,
    export_health: TelemetryDebugExportHealth,
    diagnostics: list[TelemetryDebugDiagnostic] | None = None,
) -> TelemetryDebugReport:
    if selector_kind == "invocation_id":
        return _debug_error_report("INVOCATION_NOT_FOUND", "not_found", export_health)
    code = "NO_INVOCATIONS" if selector_kind == "latest" else "NO_FAILED_INVOCATION"
    items = list(diagnostics or [])
    items.append(TelemetryDebugDiagnostic(code, "info"))
    return TelemetryDebugReport(
        status="empty" if selector_kind == "latest" else "ready",
        selection=None,
        invocation=None,
        diagnostics=_sorted_diagnostics(items),
        links=TelemetryDebugLinks(),
        export_health=export_health,
    )


def build_telemetry_debug_report(
    service: TelemetryService | None,
    *,
    selector_kind: str = "latest",
    invocation_id: str | None = None,
    trace_root: Path | None = None,
    exporter_config: OTELExporterConfig | None = None,
    live_queue_stats: dict[str, int] | None = None,
) -> TelemetryDebugReport:
    diagnostics: list[TelemetryDebugDiagnostic] = []
    export_health = build_debug_export_health(
        exporter_config or load_config().otel_exporter,
        diagnostics,
        live_queue_stats=live_queue_stats,
    )
    if argument_error := _debug_argument_error(
        selector_kind, invocation_id, export_health
    ):
        return argument_error
    if service is None:
        return _empty_debug_report(selector_kind, export_health)

    store = service._store
    try:
        global_high_water = store.event_high_water()
        selected_id, source = _select_invocation(
            store,
            selector_kind=selector_kind,
            invocation_id=invocation_id,
            high_water=global_high_water,
            diagnostics=diagnostics,
        )
    except RuntimeError as exc:
        code = (
            "AMBIGUOUS_INVOCATION_ID"
            if str(exc) == "AMBIGUOUS_INVOCATION_ID"
            else _debug_query_error_code(exc)
        )
        return _debug_error_report(code, "storage", export_health)
    except TELEMETRY_INSPECTION_EXCEPTIONS as exc:
        return _debug_error_report(
            _debug_query_error_code(exc),
            "storage",
            export_health,
        )
    if selected_id is None:
        return _empty_debug_report(selector_kind, export_health, diagnostics)

    try:
        high_water = store.event_high_water(invocation_id=selected_id)
        rows = list(
            _iter_event_rows(
                store,
                high_water=high_water,
                invocation_id=selected_id,
            )
        )
        invocation, trace_paths = _summarize_invocation(
            selected_id,
            rows,
            diagnostics,
            trace_root=trace_root,
        )
    except TELEMETRY_INSPECTION_EXCEPTIONS as exc:
        return _debug_error_report(
            _debug_query_error_code(exc),
            "storage",
            export_health,
        )
    return _selected_debug_report(
        selector_kind=selector_kind,
        source=source,
        selected_id=selected_id,
        high_water=high_water,
        invocation=invocation,
        trace_paths=trace_paths,
        diagnostics=diagnostics,
        export_health=export_health,
    )


def build_telemetry_debug_error(
    code: str,
    category: str,
    *,
    exporter_config: OTELExporterConfig | None = None,
) -> TelemetryDebugReport:
    diagnostics: list[TelemetryDebugDiagnostic] = []
    export_health = build_debug_export_health(
        exporter_config or OTELExporterConfig(),
        diagnostics,
    )
    report = _debug_error_report(code, category, export_health)
    return TelemetryDebugReport(
        status=report.status,
        selection=report.selection,
        invocation=report.invocation,
        diagnostics=_sorted_diagnostics(diagnostics),
        links=report.links,
        export_health=report.export_health,
        error=report.error,
    )


def parse_invocation_id(value: str) -> str:
    token = str(value or "")
    if not _INVOCATION_ID_RE.fullmatch(token):
        raise ValueError("invalid invocation ID")
    return token


def read_invocation_events(
    service: TelemetryService,
    invocation_id: str,
) -> list[Any]:
    high_water = service._store.event_high_water(invocation_id=invocation_id)
    return [
        row.event
        for row in _iter_event_rows(
            service._store,
            high_water=high_water,
            invocation_id=invocation_id,
        )
    ]


def telemetry_debug_exit(report: TelemetryDebugReport) -> int:
    if report.error is None:
        return 0
    return {
        "not_found": 1,
        "argument": 2,
        "storage": 3,
        "internal": 3,
    }[report.error.category]


def telemetry_storage_error_code(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, sqlite3.DatabaseError) and any(
        token in text for token in ("malformed", "not a database", "corrupt")
    ):
        return "TELEMETRY_STORAGE_CORRUPT"
    if isinstance(exc, sqlite3.OperationalError) and any(
        token in text for token in ("no such table", "no such column")
    ):
        return "TELEMETRY_SCHEMA_INCOMPATIBLE"
    if isinstance(exc, (PermissionError, IsADirectoryError, OSError, RuntimeError)):
        return "TELEMETRY_STORAGE_UNAVAILABLE"
    return "TELEMETRY_STORAGE_FAILURE"


def _debug_query_error_code(exc: Exception) -> str:
    code = telemetry_storage_error_code(exc)
    return (
        "TELEMETRY_STORAGE_FAILURE" if code == "TELEMETRY_STORAGE_UNAVAILABLE" else code
    )


def select_recent_invocation_ids(
    service: TelemetryService,
    *,
    limit: int = 20,
) -> list[str]:
    safe_limit = int(limit)
    if safe_limit < 1 or safe_limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    high_water = service._store.event_high_water()
    starts = _iter_event_rows(
        service._store,
        high_water=high_water,
        event_types=("agent.invocation.started",),
    )
    latest_by_invocation: dict[str, float] = {}
    for row in starts:
        invocation = str(row.event.invocation_id or "")
        if not invocation or not row.timestamp_valid:
            continue
        latest_by_invocation[invocation] = max(
            latest_by_invocation.get(invocation, -math.inf),
            row.event.timestamp,
        )
    return [
        invocation
        for invocation, _ in sorted(
            latest_by_invocation.items(),
            key=lambda item: (item[1], item[0]),
            reverse=True,
        )[:safe_limit]
    ]


def _select_invocation(
    store: TelemetryStore,
    *,
    selector_kind: str,
    invocation_id: str | None,
    high_water: int,
    diagnostics: list[TelemetryDebugDiagnostic],
) -> tuple[str | None, str]:
    if selector_kind == "invocation_id":
        matches = _invocation_lookup_matches(store, str(invocation_id), high_water)
        if len(matches) > 1:
            raise RuntimeError("AMBIGUOUS_INVOCATION_ID")
        return (next(iter(matches), None), "explicit")
    if selector_kind == "failed":
        winners = _terminal_winners(store, high_water, diagnostics)
        failed = [
            (key, invocation)
            for invocation, (key, event_type) in winners.items()
            if event_type == "agent.invocation.failed"
        ]
        if not failed:
            return None, "canonical"
        return max(failed)[1], "canonical"

    starts = _iter_event_rows(
        store,
        high_water=high_water,
        event_types=("agent.invocation.started",),
    )
    candidates: list[tuple[float, str]] = []
    for row in starts:
        invocation = str(row.event.invocation_id or "")
        if not invocation:
            continue
        if not row.timestamp_valid:
            _malformed_timestamp(row, diagnostics)
            continue
        candidates.append((row.event.timestamp, invocation))
    if candidates:
        return max(candidates)[1], "canonical"

    earliest: dict[str, float] = {}
    uncorrelated = 0
    for row in _iter_event_rows(store, high_water=high_water):
        invocation = str(row.event.invocation_id or "")
        if not invocation:
            uncorrelated += 1
            continue
        if not row.timestamp_valid:
            _malformed_timestamp(row, diagnostics)
            continue
        earliest[invocation] = min(
            earliest.get(invocation, math.inf),
            row.event.timestamp,
        )
    if uncorrelated:
        diagnostics.append(
            TelemetryDebugDiagnostic(
                "UNCORRELATED_LEGACY_EVENT",
                "warning",
                {"event_count": uncorrelated},
            )
        )
    if not earliest:
        return None, "legacy_fallback"
    diagnostics.append(TelemetryDebugDiagnostic("LEGACY_SELECTION", "warning"))
    return max((timestamp, invocation) for invocation, timestamp in earliest.items())[
        1
    ], ("legacy_fallback")


def _terminal_winners(
    store: TelemetryStore,
    high_water: int,
    diagnostics: list[TelemetryDebugDiagnostic],
) -> dict[str, tuple[tuple[float, int, str, int, str], str]]:
    grouped: dict[str, list[TelemetryEventPageRow]] = {}
    for row in _iter_event_rows(
        store, high_water=high_water, event_types=_TERMINAL_TYPES
    ):
        invocation = str(row.event.invocation_id or "")
        if not invocation:
            continue
        if not row.timestamp_valid:
            _malformed_timestamp(row, diagnostics)
            continue
        grouped.setdefault(invocation, []).append(row)
    winners = {}
    for invocation, rows in grouped.items():
        types = {row.event.event_type for row in rows}
        if len(types) > 1:
            diagnostics.append(
                TelemetryDebugDiagnostic(
                    "CONFLICTING_TERMINALS",
                    "error",
                    {"invocation_id": invocation, "terminal_types": sorted(types)},
                )
            )
        winner = max(rows, key=_terminal_key)
        key = (*_terminal_key(winner), invocation)
        winners[invocation] = (key, winner.event.event_type)
    return winners


def _invocation_lookup_matches(
    store: TelemetryStore,
    token: str,
    high_water: int,
) -> set[str]:
    candidates = {token}
    try:
        parsed = uuid.UUID(token)
    except ValueError:
        parsed = None
    if parsed is not None:
        candidates.update({parsed.hex, str(parsed)})
    return {
        candidate
        for candidate in candidates
        if any(
            _iter_event_rows(
                store,
                high_water=high_water,
                invocation_id=candidate,
            )
        )
    }


def _iter_event_rows(
    store: TelemetryStore,
    *,
    high_water: int,
    invocation_id: str | None = None,
    event_types: tuple[str, ...] = (),
) -> Iterator[TelemetryEventPageRow]:
    before_timestamp: float | None = None
    before_id: int | None = None
    while True:
        page = store.fetch_event_page(
            high_water=high_water,
            invocation_id=invocation_id,
            event_types=event_types,
            before_timestamp=before_timestamp,
            before_id=before_id,
            limit=1000,
        )
        if not page:
            return
        yield from page
        before_timestamp = page[-1].event.timestamp
        before_id = page[-1].row_id


def _summarize_invocation(
    invocation_id: str,
    rows: list[TelemetryEventPageRow],
    diagnostics: list[TelemetryDebugDiagnostic],
    *,
    trace_root: Path | None,
) -> tuple[TelemetryDebugInvocation, list[str]]:
    valid_rows = []
    for row in rows:
        if row.timestamp_valid:
            valid_rows.append(row)
        else:
            _malformed_timestamp(row, diagnostics)
    starts = [
        row for row in valid_rows if row.event.event_type == "agent.invocation.started"
    ]
    terminals = [row for row in valid_rows if row.event.event_type in _TERMINAL_TYPES]
    start = min(starts, key=telemetry_event_sort_key) if starts else None
    terminal = max(terminals, key=_terminal_key) if terminals else None
    terminal_types = {row.event.event_type for row in terminals}
    if len(terminal_types) > 1:
        diagnostics.append(
            TelemetryDebugDiagnostic(
                "CONFLICTING_TERMINALS",
                "error",
                {
                    "invocation_id": invocation_id,
                    "terminal_types": sorted(terminal_types),
                },
            )
        )
    outcome = {
        "agent.invocation.completed": "completed",
        "agent.invocation.failed": "failed",
        "agent.invocation.cancelled": "cancelled",
    }.get(
        terminal.event.event_type if terminal else "", "active" if start else "unknown"
    )
    started_at = _event_time(start, diagnostics)
    terminal_at = _event_time(terminal, diagnostics)
    duration_ms = None
    if start and terminal:
        delta = (terminal.event.timestamp - start.event.timestamp) * 1000
        if math.isfinite(delta) and delta >= 0:
            duration_ms = int(round(delta))
    usage = aggregate_debug_usage(valid_rows, diagnostics)
    trace_count, trace_paths = _trace_facts(valid_rows, trace_root, diagnostics)
    terminal_data = terminal.event.data if terminal else {}
    provider = str(terminal_data.get("provider") or "").strip() or None
    model = str(terminal_data.get("model") or "").strip() or None
    return (
        TelemetryDebugInvocation(
            invocation_id=invocation_id,
            outcome=outcome,
            started_at=started_at,
            terminal_at=terminal_at,
            session_ids=sorted(
                {row.event.session_id for row in rows if row.event.session_id}
            ),
            agent_ids=sorted(
                {str(row.event.agent_id) for row in rows if row.event.agent_id}
            ),
            execution_count=len(
                {str(row.event.execution_id) for row in rows if row.event.execution_id}
            ),
            trace_count=trace_count,
            duration_ms=duration_ms,
            provider=provider,
            model=model,
            usage=usage,
        ),
        trace_paths,
    )


def _trace_facts(
    rows: list[TelemetryEventPageRow],
    trace_root: Path | None,
    diagnostics: list[TelemetryDebugDiagnostic],
) -> tuple[int | None, list[str]]:
    calls: dict[str, list[TelemetryEventPageRow]] = {}
    started_only = False
    for row in rows:
        if row.event.event_type not in {
            "llm.call.started",
            "llm.call.completed",
            "llm.call.failed",
        }:
            continue
        call_id = str(row.event.data.get("llm_call_id") or "").strip()
        if not call_id:
            continue
        calls.setdefault(call_id, []).append(row)
    paths: set[str] = set()
    complete = True
    for call_rows in calls.values():
        terminals = [
            row
            for row in call_rows
            if row.event.event_type in {"llm.call.completed", "llm.call.failed"}
        ]
        if not terminals:
            started_only = True
            complete = False
            continue
        winner = max(terminals, key=telemetry_event_sort_key)
        raw_paths = winner.event.data.get("trace_artifact_paths")
        terminal_complete = winner.event.data.get("trace_artifacts_complete")
        if not isinstance(raw_paths, list) or not isinstance(terminal_complete, bool):
            complete = False
            continue
        complete = complete and terminal_complete
        for raw_path in raw_paths:
            if isinstance(raw_path, str):
                paths.add(raw_path)
            else:
                complete = False
    if started_only:
        diagnostics.append(
            TelemetryDebugDiagnostic("INCOMPLETE_TRACE_FACTS", "warning")
        )
    if len(paths) > 1000:
        diagnostics.append(
            TelemetryDebugDiagnostic(
                "TRACE_PATH_LIMIT_EXCEEDED",
                "warning",
                {"path_count": len(paths)},
            )
        )
        complete = False
    candidates = sorted(paths)[:1001]
    verified: list[str] = []
    for candidate in candidates:
        if _safe_trace_path(trace_root, candidate):
            verified.append(candidate)
        else:
            complete = False
            diagnostics.append(
                TelemetryDebugDiagnostic(
                    "UNAVAILABLE_TRACE_ARTIFACT",
                    "warning",
                    {"path": candidate},
                )
            )
    if not complete:
        diagnostics.append(
            TelemetryDebugDiagnostic("INCOMPLETE_TRACE_FACTS", "warning")
        )
    if len(verified) > 100:
        diagnostics.append(
            TelemetryDebugDiagnostic(
                "TRACE_LINKS_TRUNCATED",
                "info",
                {"trace_count": len(verified)},
            )
        )
    trace_count = len(paths) if complete and len(paths) <= 1000 else None
    return trace_count, verified[:100]


def _safe_trace_path(trace_root: Path | None, value: str) -> bool:
    if not value or "\\" in value:
        return False
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        return False
    if not (value.endswith(".json") or value.endswith("-raw.txt")):
        return False
    if trace_root is None or trace_root.is_symlink():
        return False
    root = trace_root.expanduser()
    candidate = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return False
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError, OSError):
        return False
    return candidate.is_file() and os.access(candidate, os.R_OK)


def _debug_error_report(
    code: str,
    category: str,
    export_health: TelemetryDebugExportHealth,
) -> TelemetryDebugReport:
    return TelemetryDebugReport(
        status="error",
        selection=None,
        invocation=None,
        diagnostics=[],
        links=TelemetryDebugLinks(),
        export_health=export_health,
        error=TelemetryDebugError(code, category),
    )


def _terminal_key(row: TelemetryEventPageRow) -> tuple[float, int, str, int]:
    return (
        row.event.timestamp,
        _TERMINAL_RANK[row.event.event_type],
        str(row.event.event_id or ""),
        row.row_id,
    )


def _event_time(
    row: TelemetryEventPageRow | None,
    diagnostics: list[TelemetryDebugDiagnostic],
) -> str | None:
    if row is None:
        return None
    try:
        return (
            datetime.fromtimestamp(row.event.timestamp, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (OverflowError, OSError, ValueError):
        diagnostics.append(
            TelemetryDebugDiagnostic(
                "UNREPRESENTABLE_EVENT_TIMESTAMP",
                "warning",
                {"event_id": str(row.event.event_id or "")},
            )
        )
        return None


def _malformed_timestamp(
    row: TelemetryEventPageRow,
    diagnostics: list[TelemetryDebugDiagnostic],
) -> None:
    diagnostics.append(
        TelemetryDebugDiagnostic(
            "MALFORMED_EVENT_TIMESTAMP",
            "warning",
            {"event_id": str(row.event.event_id or "")},
        )
    )


def _sorted_diagnostics(
    diagnostics: list[TelemetryDebugDiagnostic],
) -> list[TelemetryDebugDiagnostic]:
    unique = {
        (item.code, item.severity, json.dumps(item.details, sort_keys=True)): item
        for item in diagnostics
    }
    return sorted(
        unique.values(),
        key=lambda item: (item.code, json.dumps(item.details, sort_keys=True)),
    )


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


def trace_kind(filename: str) -> str:
    for suffix, kind in _TRACE_KIND_SUFFIXES:
        if filename.endswith(suffix):
            return kind
    return "json"


def _utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(float(timestamp), tz=UTC).isoformat()


__all__ = [
    "build_catalog_report",
    "build_doctor_report",
    "build_telemetry_debug_error",
    "build_telemetry_debug_report",
    "TELEMETRY_INSPECTION_EXCEPTIONS",
    "list_trace_files",
    "open_telemetry_inspection",
    "parse_invocation_id",
    "read_invocation_events",
    "read_trace_file",
    "select_recent_invocation_ids",
    "telemetry_debug_exit",
    "telemetry_storage_error_code",
    "trace_kind",
]
