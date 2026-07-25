from __future__ import annotations

from typing import Any, Mapping

__all__ = ["format_chat_phase_timing_report"]

_PHASE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("context", ("memory_retrieval", "context_pack_build", "gateway_session_context")),
    ("provider", ("provider_request_build", "provider_round_trip")),
    ("tools", ("tool_schema_serialization", "tool_calls")),
    ("approval", ("approval_wait",)),
    (
        "finalization",
        ("response_normalization", "response_persistence", "memory_write"),
    ),
    ("delivery", ("cli_render_delivery",)),
)


def format_chat_phase_timing_report(payload: Mapping[str, Any] | None) -> str:
    if not payload:
        return ""
    total_ms = _optional_int(payload.get("total_turn_ms"))
    if total_ms is None:
        return ""

    parts = [f"total {_format_ms(total_ms)}"]
    first_text = _optional_int(payload.get("time_to_first_text_ms"))
    if first_text is not None:
        parts.append(f"first text {_format_ms(first_text)}")
    provider_token = _optional_int(payload.get("provider_token_ttft_ms"))
    if provider_token is not None:
        parts.append(f"provider token {_format_ms(provider_token)}")

    phase_rows = _phase_rows(payload)
    if phase_rows:
        return "Timing: " + "; ".join(parts) + "\nPhases: " + ", ".join(phase_rows)
    return "Timing: " + "; ".join(parts)


def _phase_rows(payload: Mapping[str, Any]) -> list[str]:
    instrumented = {
        str(item) for item in list(payload.get("phases_instrumented") or [])
    }
    rows: list[str] = []
    for label, phase_names in _PHASE_GROUPS:
        values = [
            _optional_int(payload.get(f"{name}_ms"))
            for name in phase_names
            if name in instrumented or _optional_int(payload.get(f"{name}_ms"))
        ]
        if not values:
            continue
        rows.append(f"{label} {_format_ms(sum(value or 0 for value in values))}")
    return rows


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _format_ms(value: int) -> str:
    if value < 1000:
        return f"{value}ms"
    seconds = value / 1000
    return f"{seconds:.1f}s"
