from __future__ import annotations

import math
import re
from typing import Any

from .schemas import (
    TelemetryCorrelationReport,
    TelemetryDebugDiagnostic,
    TelemetryTimingReport,
)
from .service import TelemetryService

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
CORRELATION_FIELDS = (
    "invocation_id",
    "execution_id",
    "trace_key",
    "run_id",
    "agent_id",
    "session_id",
    "llm_call_id",
    "tool_call_id",
)


def parse_report_scope(
    *,
    session_id: str | None,
    recent: str | None,
    limit: str | None,
) -> dict[str, Any]:
    normalized_session = str(session_id or "").strip()
    if normalized_session and recent is not None:
        raise ValueError("selectors are mutually exclusive")
    if limit is not None and not normalized_session:
        raise ValueError("limit requires session scope")
    if normalized_session:
        if _ID_RE.fullmatch(normalized_session) is None:
            raise ValueError("invalid session ID")
        selected_limit = _bounded_count(limit or "20")
        return {
            "kind": "session",
            "session_id": normalized_session,
            "limit": selected_limit,
        }
    selected_limit = _bounded_count(recent or "20")
    return {"kind": "recent", "session_id": None, "limit": selected_limit}


def build_correlation_report(
    service: TelemetryService | None,
    *,
    scope: dict[str, Any],
) -> TelemetryCorrelationReport:
    if service is None:
        return TelemetryCorrelationReport("empty", scope, 0, _coverage_rows([], {}), [])
    high_water = service._store.event_high_water()
    rows = _snapshot_rows(service, high_water)
    selected = _select_invocations(rows, scope)
    values: dict[str, dict[str, set[str]]] = {
        invocation_id: {field: set() for field in CORRELATION_FIELDS}
        for invocation_id in selected
    }
    for row in rows:
        event = row.event
        invocation_id = str(event.invocation_id or "")
        if invocation_id not in values:
            continue
        payload = event.data
        direct = {
            "invocation_id": event.invocation_id,
            "execution_id": event.execution_id,
            "trace_key": event.trace_key,
            "run_id": payload.get("run_id"),
            "agent_id": event.agent_id,
            "session_id": event.session_id,
            "llm_call_id": payload.get("llm_call_id"),
            "tool_call_id": payload.get("tool_call_id"),
        }
        for field, value in direct.items():
            normalized = str(value or "").strip()
            if normalized:
                values[invocation_id][field].add(normalized)
    return TelemetryCorrelationReport(
        "ready" if selected else "empty",
        scope,
        len(selected),
        _coverage_rows(selected, values),
        [],
    )


def _timing_samples(
    timing_rows: list[Any],
) -> tuple[dict[str, list[int]], dict[tuple[str | None, str | None], list[int]], bool]:
    phase_samples: dict[str, list[int]] = {}
    provider_samples: dict[tuple[str | None, str | None], list[int]] = {}
    malformed = False
    for row in timing_rows:
        payload = row.event.data
        instrumented = payload.get("phases_instrumented")
        if isinstance(instrumented, list):
            for phase_value in instrumented:
                phase = str(phase_value or "").strip()
                sample = payload.get(f"{phase}_ms")
                if (
                    phase
                    and isinstance(sample, int)
                    and not isinstance(sample, bool)
                    and sample >= 0
                ):
                    phase_samples.setdefault(phase, []).append(sample)
                else:
                    malformed = True
        else:
            malformed = True
        attempts = payload.get("provider_attempts")
        if attempts is None:
            continue
        if not isinstance(attempts, list):
            malformed = True
            continue
        for attempt in attempts:
            if not isinstance(attempt, dict):
                malformed = True
                continue
            sample = attempt.get("latency_ms")
            if not isinstance(sample, int) or isinstance(sample, bool) or sample < 0:
                malformed = True
                continue
            provider = str(attempt.get("provider") or "").strip() or None
            model = str(attempt.get("model") or "").strip() or None
            provider_samples.setdefault((provider, model), []).append(sample)
    return phase_samples, provider_samples, malformed


def build_timing_report(
    service: TelemetryService | None,
    *,
    scope: dict[str, Any],
) -> TelemetryTimingReport:
    if service is None:
        return TelemetryTimingReport("empty", scope, 0, 0, [], [], [])
    high_water = service._store.event_high_water()
    rows = _snapshot_rows(service, high_water)
    selected = _select_invocations(rows, scope)
    if not selected:
        return TelemetryTimingReport("empty", scope, 0, 0, [], [], [])
    selected_set = set(selected)
    pairs: set[tuple[str, str]] = set()
    for row in rows:
        event = row.event
        if event.invocation_id in selected_set and event.session_id and event.turn_id:
            pairs.add((event.session_id, event.turn_id))
    if len(pairs) > 1000:
        return _timing_error(scope, "TIMING_PAIR_LIMIT_EXCEEDED")
    ownership: dict[tuple[str, str], set[str]] = {pair: set() for pair in pairs}
    for row in rows:
        event = row.event
        pair = (event.session_id, event.turn_id)
        if pair in ownership and event.invocation_id:
            ownership[pair].add(event.invocation_id)
    diagnostics: list[TelemetryDebugDiagnostic] = []
    admitted_pairs = {
        pair
        for pair, owners in ownership.items()
        if len(owners) == 1 and next(iter(owners)) in selected_set
    }
    if len(admitted_pairs) != len(pairs):
        diagnostics.append(
            TelemetryDebugDiagnostic("AMBIGUOUS_TURN_CORRELATION", "warning", {})
        )
    timing_rows = [
        row
        for row in rows
        if row.event.event_type == "chat.phase_timing"
        and (row.event.session_id, row.event.turn_id) in admitted_pairs
    ]
    phase_samples, provider_samples, malformed = _timing_samples(timing_rows)
    if malformed:
        diagnostics.append(
            TelemetryDebugDiagnostic("MALFORMED_PHASE_TIMING_SAMPLE", "warning", {})
        )
    phases = [
        {"phase": phase, **_rollup(samples)} for phase, samples in phase_samples.items()
    ]
    phases.sort(key=lambda item: (-item["p95_ms"], item["phase"]))
    providers = [
        {"provider": provider, "model": model, **_rollup(samples)}
        for (provider, model), samples in provider_samples.items()
    ]
    providers.sort(
        key=lambda item: (
            -item["p95_ms"],
            item["provider"] is None,
            item["provider"] or "",
            item["model"] is None,
            item["model"] or "",
        )
    )
    if not timing_rows:
        diagnostics.append(
            TelemetryDebugDiagnostic("NO_PHASE_TIMING_FACTS", "info", {})
        )
    return TelemetryTimingReport(
        "ready",
        scope,
        len(selected),
        len(timing_rows),
        phases,
        providers,
        sorted(diagnostics, key=lambda item: item.code),
    )


def correlation_error(
    scope: dict[str, Any] | None = None,
    *,
    code: str = "INVALID_ARGUMENT",
    category: str = "argument",
) -> TelemetryCorrelationReport:
    return TelemetryCorrelationReport(
        "error", scope, 0, [], [], {"code": code, "category": category}
    )


def timing_error(
    scope: dict[str, Any] | None = None,
    *,
    code: str = "INVALID_ARGUMENT",
    category: str = "argument",
) -> TelemetryTimingReport:
    return _timing_error(scope, code, category=category)


def _timing_error(
    scope: dict[str, Any] | None,
    code: str,
    *,
    category: str = "internal",
) -> TelemetryTimingReport:
    return TelemetryTimingReport(
        "error", scope, 0, 0, [], [], [], {"code": code, "category": category}
    )


def _snapshot_rows(service: TelemetryService, high_water: int) -> list[Any]:
    rows: list[Any] = []
    before_timestamp: float | None = None
    before_id: int | None = None
    while True:
        page = service._store.fetch_event_page(
            high_water=high_water,
            limit=1000,
            before_timestamp=before_timestamp,
            before_id=before_id,
        )
        if not page:
            break
        rows.extend(page)
        last = page[-1]
        before_timestamp = last.event.timestamp
        before_id = last.row_id
        if len(page) < 1000:
            break
    return rows


def _select_invocations(rows: list[Any], scope: dict[str, Any]) -> list[str]:
    starts: dict[str, float] = {}
    session_ids: dict[str, set[str]] = {}
    for row in rows:
        event = row.event
        invocation_id = str(event.invocation_id or "")
        if not invocation_id:
            continue
        session_ids.setdefault(invocation_id, set()).add(str(event.session_id or ""))
        if event.event_type == "agent.invocation.started" and row.timestamp_valid:
            starts[invocation_id] = max(
                starts.get(invocation_id, -math.inf), event.timestamp
            )
    ordered = [
        invocation_id
        for invocation_id, _ in sorted(
            starts.items(), key=lambda item: (item[1], item[0]), reverse=True
        )
    ]
    if scope["kind"] == "session":
        ordered = [
            invocation_id
            for invocation_id in ordered
            if scope["session_id"] in session_ids.get(invocation_id, set())
        ]
    return ordered[: int(scope["limit"])]


def _coverage_rows(
    selected: list[str],
    values: dict[str, dict[str, set[str]]],
) -> list[dict[str, Any]]:
    total = len(selected)
    rows = []
    for field in CORRELATION_FIELDS:
        present = sum(bool(values[item][field]) for item in selected) if total else 0
        rows.append(
            {
                "field": field,
                "present": present,
                "missing": total - present,
                "total": total,
                "coverage": f"{present / total:.4f}" if total else None,
            }
        )
    return rows


def _rollup(samples: list[int]) -> dict[str, int]:
    ordered = sorted(samples)
    return {
        "sample_count": len(ordered),
        "p50_ms": _nearest_rank(ordered, 0.50),
        "p95_ms": _nearest_rank(ordered, 0.95),
        "max_ms": ordered[-1],
    }


def _nearest_rank(samples: list[int], percentile: float) -> int:
    rank = math.ceil(percentile * len(samples))
    return samples[rank - 1]


def _bounded_count(value: str) -> int:
    if not str(value).isdigit():
        raise ValueError("invalid report count")
    count = int(value)
    if count < 1 or count > 1000:
        raise ValueError("invalid report count")
    return count


__all__ = [
    "CORRELATION_FIELDS",
    "build_correlation_report",
    "build_timing_report",
    "correlation_error",
    "parse_report_scope",
    "timing_error",
]
