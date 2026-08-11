from __future__ import annotations

from datetime import UTC, datetime
import math
import re
import time
from typing import Any

from .inspection import parse_invocation_id
from .schemas import TelemetryDebugDiagnostic, TelemetryRetentionPlan
from .service import TelemetryService

_DURATION_RE = re.compile(r"([1-9][0-9]*)([mhdw])\Z")
_TERMINAL_TYPES = frozenset(
    {
        "agent.invocation.completed",
        "agent.invocation.failed",
        "agent.invocation.cancelled",
    }
)
_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
APPLY_BLOCKER = "cross_store_retention_fence_unavailable"


def parse_retention_selector(
    *, older_than: str | None, keep_last: str | None
) -> dict[str, int | str | None]:
    if bool(older_than) == bool(keep_last):
        raise ValueError("exactly one retention selector is required")
    if older_than:
        match = _DURATION_RE.fullmatch(str(older_than))
        if match is None:
            raise ValueError("invalid duration")
        seconds = int(match.group(1)) * _SECONDS[match.group(2)]
        return {
            "kind": "older_than",
            "older_than_seconds": seconds,
            "keep_last": None,
        }
    if not str(keep_last).isdigit() or int(str(keep_last)) < 1:
        raise ValueError("invalid keep-last count")
    return {
        "kind": "keep_last",
        "older_than_seconds": None,
        "keep_last": int(str(keep_last)),
    }


def _retention_inventory(
    rows: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(str(row.event.invocation_id or ""), []).append(row)
    terminal_rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for invocation_id, invocation_rows in grouped.items():
        reasons: list[str] = []
        try:
            parse_invocation_id(invocation_id)
        except ValueError:
            reasons.append("UNADDRESSABLE_INVOCATION_ID")
        sessions = sorted(
            {
                str(row.event.session_id)
                for row in invocation_rows
                if row.event.session_id
            }
        )
        if not sessions:
            reasons.append("SESSION_MAPPING_UNAVAILABLE")
        terminals = [
            row for row in invocation_rows if row.event.event_type in _TERMINAL_TYPES
        ]
        valid = [
            row
            for row in terminals
            if row.timestamp_valid and math.isfinite(float(row.event.timestamp))
        ]
        if not terminals:
            reasons.append("ACTIVE_INVOCATION")
        elif not valid:
            reasons.append("MALFORMED_TERMINAL_TIMESTAMP")
        winner = (
            max(valid, key=lambda row: (row.event.timestamp, row.row_id))
            if valid
            else None
        )
        item = _retention_row(invocation_id, winner, sessions)
        if reasons:
            item["reason_codes"] = sorted(set(reasons))
            exclusions.append(item)
        elif winner is not None:
            terminal_rows.append(item)
    return terminal_rows, exclusions


def build_retention_plan(
    service: TelemetryService | None,
    *,
    selector: dict[str, Any],
    now: float | None = None,
) -> TelemetryRetentionPlan:
    created_epoch = time.time() if now is None else float(now)
    created_at = datetime.fromtimestamp(created_epoch, tz=UTC).isoformat()
    diagnostics = [
        TelemetryDebugDiagnostic(
            "CROSS_STORE_RETENTION_FENCE_UNAVAILABLE", "warning", {}
        )
    ]
    if service is None:
        diagnostics.append(
            TelemetryDebugDiagnostic("NO_RETENTION_CANDIDATES", "info", {})
        )
        return TelemetryRetentionPlan(
            status="empty",
            selector=selector,
            created_at=created_at,
            high_water_storage_sequence=None,
            candidates=[],
            exclusions=[],
            diagnostics=sorted(diagnostics, key=lambda item: item.code),
        )
    high_water = service._store.event_high_water()
    rows = _all_rows(service, high_water)
    terminal_rows, exclusions = _retention_inventory(rows)
    candidates: list[dict[str, Any]] = []
    if selector["kind"] == "older_than":
        cutoff = created_epoch - int(selector["older_than_seconds"])
        for item in terminal_rows:
            if float.fromhex(item["terminal_epoch_hex"]) <= cutoff:
                candidates.append(item)
            else:
                exclusions.append({**item, "reason_codes": ["AGE_THRESHOLD_NOT_MET"]})
    else:
        ranked = sorted(
            terminal_rows,
            key=lambda item: (
                float.fromhex(item["terminal_epoch_hex"]),
                item["invocation_id"],
            ),
            reverse=True,
        )
        protected = int(selector["keep_last"])
        for index, item in enumerate(ranked):
            if index < protected:
                exclusions.append({**item, "reason_codes": ["KEEP_LAST_PROTECTED"]})
            else:
                candidates.append(item)
    candidates.sort(key=_retention_sort)
    exclusions.sort(key=_retention_sort)
    status = "ready" if candidates else "empty"
    if not candidates:
        diagnostics.append(
            TelemetryDebugDiagnostic("NO_RETENTION_CANDIDATES", "info", {})
        )
    return TelemetryRetentionPlan(
        status=status,
        selector=selector,
        created_at=created_at,
        high_water_storage_sequence=high_water,
        candidates=candidates,
        exclusions=exclusions,
        diagnostics=sorted(diagnostics, key=lambda item: item.code),
    )


def retention_error(
    code: str,
    category: str,
    *,
    selector: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> TelemetryRetentionPlan:
    return TelemetryRetentionPlan(
        status="error",
        selector=selector,
        created_at=created_at,
        high_water_storage_sequence=None,
        candidates=[],
        exclusions=[],
        diagnostics=[],
        error={"code": code, "category": category},
    )


def _all_rows(service: TelemetryService, high_water: int) -> list[Any]:
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


def _retention_row(
    invocation_id: str,
    terminal: Any | None,
    sessions: list[str],
) -> dict[str, Any]:
    epoch = float(terminal.event.timestamp) if terminal is not None else None
    return {
        "invocation_id": invocation_id,
        "terminal_at": (
            datetime.fromtimestamp(epoch, tz=UTC).isoformat()
            if epoch is not None
            else None
        ),
        "terminal_epoch_hex": epoch.hex() if epoch is not None else None,
        "session_ids": sessions,
    }


def _retention_sort(item: dict[str, Any]) -> tuple[bool, float, str]:
    value = item.get("terminal_epoch_hex")
    return (
        value is None,
        float.fromhex(value) if value else math.inf,
        item["invocation_id"],
    )


__all__ = [
    "APPLY_BLOCKER",
    "build_retention_plan",
    "parse_retention_selector",
    "retention_error",
]
