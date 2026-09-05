from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any

from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService
from openminion.modules.telemetry.storage.base import (
    TelemetryEventPageRow,
    TelemetryStore,
)

_STRUCTURAL_RESULT_FIELDS = (
    "result_status",
    "assessment_id",
    "total_findings",
    "returned_findings",
    "check_count",
    "finding_count",
    "candidate_count",
    "rejected_count",
    "redaction_count",
    "duration_ms",
    "artifact_count",
    "artifact_refs",
)


def _safe_structural_tool_result(item: dict[str, Any]) -> dict[str, Any] | None:
    data = item.get("data")
    if item.get("structural_only") is not True or not isinstance(data, dict):
        return None
    return {
        "tool_name": str(item.get("tool_name") or ""),
        "ok": bool(item.get("ok")),
        "verified": bool(item.get("verified")),
        "data": {
            field: data[field] for field in _STRUCTURAL_RESULT_FIELDS if field in data
        },
        "error_code": str(item.get("error_code") or ""),
        "call_id": str(item.get("call_id") or ""),
        "source": str(item.get("source") or ""),
    }


def iter_event_rows(
    store: TelemetryStore,
    *,
    high_water: int,
    invocation_id: str | None = None,
    session_id: str | None = None,
    event_types: tuple[str, ...] = (),
) -> Iterator[TelemetryEventPageRow]:
    before_timestamp: float | None = None
    before_id: int | None = None
    while True:
        page = store.fetch_event_page(
            high_water=high_water,
            invocation_id=invocation_id,
            session_id=session_id,
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


def structural_error_code(data: dict[str, Any]) -> str | None:
    direct_code = data.get("failure_code") or data.get("error_code")
    if str(direct_code or "").strip():
        return str(direct_code).strip()
    error = data.get("error")
    if not isinstance(error, dict):
        return None
    return (
        str(
            error.get("code") or error.get("type") or error.get("category") or ""
        ).strip()
        or None
    )


def safe_event_row(event: TelemetryEvent) -> dict[str, Any]:
    data = event.data
    row: dict[str, Any] = {
        "timestamp": event.timestamp,
        "event_type": event.event_type,
    }
    for field in (
        "status",
        "operation",
        "tool_name",
        "model",
        "llm_call_id",
        "duration_ms",
        "provider_round_trip_ms",
        "assessment_id",
        "result_status",
        "total_findings",
        "returned_findings",
        "check_count",
        "finding_count",
        "candidate_count",
        "rejected_count",
        "redaction_count",
        "artifact_count",
        "artifact_refs",
    ):
        value = data.get(field)
        if value is not None and value != "":
            row[field] = value
    if error_code := structural_error_code(data):
        row["error_code"] = error_code
    for field, value in (
        ("invocation_id", event.invocation_id),
        ("execution_id", event.execution_id),
        ("session_id", event.session_id),
        ("turn_id", event.turn_id),
        ("trace_key", event.trace_key),
        ("event_id", event.event_id),
        ("agent_id", event.agent_id),
    ):
        if value:
            row[field] = value
    tool_results = data.get("tool_results")
    if isinstance(tool_results, list):
        structural_results = [
            safe
            for item in tool_results
            if isinstance(item, dict)
            and (safe := _safe_structural_tool_result(item)) is not None
        ]
        if structural_results:
            row["tool_results"] = structural_results
    return row


def read_invocation_events(
    service: TelemetryService,
    invocation_id: str,
    *,
    session_id: str | None = None,
) -> list[Any]:
    return [
        row.event for row in _scoped_invocation_rows(service, invocation_id, session_id)
    ]


def _scoped_invocation_rows(
    service: TelemetryService,
    invocation_id: str,
    session_id: str | None,
) -> list[TelemetryEventPageRow]:
    high_water = service._store.event_high_water(invocation_id=invocation_id)
    if session_id and not any(
        iter_event_rows(
            service._store,
            high_water=high_water,
            invocation_id=invocation_id,
            session_id=session_id,
        )
    ):
        return []
    return list(
        iter_event_rows(
            service._store,
            high_water=high_water,
            invocation_id=invocation_id,
        )
    )


def read_safe_invocation_event_rows(
    service: TelemetryService,
    invocation_id: str,
    *,
    session_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if limit is not None and not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    rows = _scoped_invocation_rows(service, invocation_id, session_id)
    selected = rows if limit is None else rows[:limit]
    return [safe_event_row(row.event) for row in reversed(selected)]


def _aggregate_snapshot_events(events: list[Any]) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost_usd": 0.0,
        "policy_decisions": {},
        "executions": {},
        "propagation": {"valid": 0, "invalid": 0, "unavailable": 0},
        "log_events": [],
    }
    for event in events:
        data = event.data
        usage = data.get("usage")
        if isinstance(usage, dict):
            totals["input_tokens"] += int(
                usage.get("input_tokens") or usage.get("prompt_tokens") or 0
            )
            totals["output_tokens"] += int(
                usage.get("output_tokens") or usage.get("completion_tokens") or 0
            )
            totals["cache_read_tokens"] += int(
                usage.get("cached_tokens") or usage.get("cache_read_tokens") or 0
            )
            totals["cache_write_tokens"] += int(usage.get("cache_creation_tokens") or 0)
        if data.get("cost_source") and isinstance(data.get("cost_usd"), (int, float)):
            totals["cost_usd"] += float(data["cost_usd"])
        if event.event_type == "policy.decision":
            decision = str(data.get("decision") or "unknown")
            decisions = totals["policy_decisions"]
            decisions[decision] = decisions.get(decision, 0) + 1
        if event.event_type.startswith(
            ("policy.", "safety.", "agent.handoff.", "tool.execution.failed")
        ):
            totals["log_events"].append(event.event_type)
        propagation_status = str(data.get("trace_context_status") or "")
        if propagation_status in totals["propagation"]:
            totals["propagation"][propagation_status] += 1
        execution_id = str(event.execution_id or "")
        if not execution_id:
            continue
        executions = totals["executions"]
        segment = executions.setdefault(
            execution_id,
            {
                "execution_id": execution_id,
                "agent_id": event.agent_id or "",
                "event_count": 0,
                "started_at": None,
                "ended_at": None,
                "status": "",
            },
        )
        segment["event_count"] += 1
        segment["started_at"] = (
            event.timestamp
            if segment["started_at"] is None
            else min(segment["started_at"], event.timestamp)
        )
        segment["ended_at"] = (
            event.timestamp
            if segment["ended_at"] is None
            else max(segment["ended_at"], event.timestamp)
        )
        if data.get("status"):
            segment["status"] = str(data["status"])
    return totals


def build_invocation_snapshot(
    service: TelemetryService,
    invocation_id: str,
    *,
    high_water: int | None = None,
) -> tuple[dict[str, Any], list[Any]]:
    from openminion.modules.telemetry.inspection import _summarize_invocation

    snapshot_high_water = (
        service._store.event_high_water() if high_water is None else int(high_water)
    )
    rows = list(
        iter_event_rows(
            service._store,
            high_water=snapshot_high_water,
            invocation_id=invocation_id,
        )
    )
    events = [row.event for row in rows]
    invocation, _trace_paths = _summarize_invocation(
        invocation_id,
        rows,
        diagnostics=[],
        trace_root=None,
    )
    aggregates = _aggregate_snapshot_events(events)
    executions = aggregates["executions"]
    policy_decisions = aggregates["policy_decisions"]
    return (
        {
            "invocation_id": invocation_id,
            "event_count": len(events),
            "segments": [executions[key] for key in sorted(executions)],
            "summary": {
                "cache_read_tokens": aggregates["cache_read_tokens"],
                "cache_write_tokens": aggregates["cache_write_tokens"],
                "cost_usd": round(aggregates["cost_usd"], 12),
                "duration_ms": invocation.duration_ms or 0,
                "input_tokens": aggregates["input_tokens"],
                "output_tokens": aggregates["output_tokens"],
                "policy_decisions": {
                    key: policy_decisions[key] for key in sorted(policy_decisions)
                },
            },
            "correlated_log_events": sorted(aggregates["log_events"]),
            "diagnostics": {
                "legacy_identity_gap": False,
                "orphan_terminal_events": sum(
                    1
                    for event in events
                    if event.event_type.endswith(
                        (".completed", ".failed", ".cancelled", ".paused")
                    )
                    and not event.execution_id
                ),
                "propagation": aggregates["propagation"],
            },
        },
        events,
    )


def select_invocation_snapshots(
    service: TelemetryService,
    *,
    limit: int,
    agent_id: str = "",
    status: str = "",
    event_type: str = "",
) -> tuple[list[dict[str, Any]], int]:
    high_water = service._store.event_high_water()
    latest_matches: dict[str, float] = {}
    legacy_count = 0
    for row in iter_event_rows(service._store, high_water=high_water):
        event = row.event
        invocation_id = str(event.invocation_id or "")
        if not invocation_id:
            legacy_count += 1
            continue
        if event_type and event.event_type != event_type:
            continue
        if agent_id and event.agent_id != agent_id:
            continue
        if status and str(event.data.get("status") or "") != status:
            continue
        latest_matches[invocation_id] = max(
            latest_matches.get(invocation_id, -math.inf),
            event.timestamp,
        )
    selected = [
        invocation_id
        for invocation_id, _timestamp in sorted(
            latest_matches.items(),
            key=lambda item: (item[1], item[0]),
            reverse=True,
        )[:limit]
    ]
    return (
        [
            build_invocation_snapshot(
                service,
                invocation_id,
                high_water=high_water,
            )[0]
            for invocation_id in selected
        ],
        legacy_count,
    )


__all__ = [
    "build_invocation_snapshot",
    "iter_event_rows",
    "read_invocation_events",
    "read_safe_invocation_event_rows",
    "safe_event_row",
    "select_invocation_snapshots",
    "structural_error_code",
]
