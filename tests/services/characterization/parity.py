from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, Dict, Set


# SRR-0-02: Default ignored dynamic fields
DEFAULT_IGNORED_FIELDS: Set[str] = {
    "timestamp",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
    "id",
    "request_id",
    "session_id",
    "conversation_id",
    "turn_id",
    "message_id",
    "duration_ms",
    "latency_ms",
    "trace_id",
    "span_id",
}


def normalize_for_comparison(obj: Any, ignored_fields: Set[str] | None = None) -> Any:
    if ignored_fields is None:
        ignored_fields = DEFAULT_IGNORED_FIELDS
    if obj is None:
        return None
    if is_dataclass(obj) and not isinstance(obj, type):
        return {
            f.name: normalize_for_comparison(getattr(obj, f.name), ignored_fields)
            for f in fields(obj)
            if f.name not in ignored_fields
        }
    if isinstance(obj, dict):
        return {
            k: normalize_for_comparison(v, ignored_fields)
            for k, v in obj.items()
            if k not in ignored_fields
        }
    if isinstance(obj, (list, tuple)):
        return type(obj)(normalize_for_comparison(item, ignored_fields) for item in obj)
    return obj


def assert_structural_parity(
    actual: Any, expected: Any, ignored_fields: Set[str] | None = None, msg: str = ""
) -> None:
    if ignored_fields is None:
        ignored_fields = DEFAULT_IGNORED_FIELDS
    actual_norm = normalize_for_comparison(actual, ignored_fields)
    expected_norm = normalize_for_comparison(expected, ignored_fields)
    if actual_norm != expected_norm:
        raise AssertionError(
            msg or f"Parity failed:\nExpected: {expected_norm}\nActual: {actual_norm}"
        )


def capture_snapshot(
    obj: Any, ignored_fields: Set[str] | None = None
) -> Dict[str, Any]:
    return normalize_for_comparison(obj, ignored_fields or DEFAULT_IGNORED_FIELDS)
