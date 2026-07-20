"""Project canonical turn events into one proportional-cost envelope."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


_DELIVERY_EVENT_TYPES = frozenset(
    {"turn.assistant", "gateway.final_message", "assistant.final", "turn.completed"}
)


@dataclass(frozen=True, slots=True)
class TurnCostEnvelope:
    schema_version: str = "openminion.turn_cost.v1"
    run_id: str = ""
    turn_id: str = ""
    session_id: str = ""
    provider_calls_critical_path: int = 0
    provider_calls_post_delivery: int = 0
    provider_calls_total: int = 0
    provider_calls_auxiliary: int = 0
    provider_retries: int = 0
    call_purposes: tuple[str, ...] = field(default_factory=tuple)
    delivery_boundary_timestamp: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_bytes: int | None = None
    response_bytes: int | None = None
    context_segment_count: int | None = None
    context_tokens: int | None = None
    context_bytes: int | None = None
    tool_schema_count: int | None = None
    tool_schema_bytes: int | None = None
    exposed_tool_count: int | None = None
    invoked_tool_count: int = 0
    duplicate_tool_calls: int = 0
    policy_denials: int = 0
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    provider_token_ttft_ms: int | None = None
    visible_text_ttft_ms: int | None = None
    total_wall_ms: int | None = None
    task_success: bool | None = None
    final_truthful: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def project_turn_cost(
    events: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    run_id: str = "",
    turn_id: str = "",
    session_id: str = "",
) -> TurnCostEnvelope:
    normalized = sorted((_normalize_event(event) for event in events), key=_sort_key)
    (
        delivery_timestamp,
        completed,
        post_delivery,
        purposes,
        timing,
        quality,
        provider_calls_total,
    ) = _turn_cost_inputs(normalized)

    return TurnCostEnvelope(
        run_id=str(run_id or ""),
        turn_id=str(turn_id or timing.get("turn_id") or ""),
        session_id=str(session_id or timing.get("session_id") or ""),
        provider_calls_critical_path=provider_calls_total - post_delivery,
        provider_calls_post_delivery=post_delivery,
        provider_calls_total=provider_calls_total,
        provider_calls_auxiliary=sum(
            1 for event in completed if bool(event["payload"].get("auxiliary"))
        ),
        provider_retries=sum(
            1 for event in normalized if event["event_type"] == "llm.call.retry"
        ),
        call_purposes=purposes,
        delivery_boundary_timestamp=delivery_timestamp,
        input_tokens=_prefer_total(
            timing.get("provider_input_tokens"),
            _sum_usage(completed, "input_tokens"),
        ),
        output_tokens=_prefer_total(
            timing.get("provider_output_tokens"),
            _sum_usage(completed, "output_tokens"),
        ),
        request_bytes=_prefer_total(
            timing.get("provider_request_bytes"),
            _sum_optional(completed, "request_bytes"),
        ),
        response_bytes=_prefer_total(
            timing.get("provider_response_bytes"),
            _sum_optional(completed, "response_bytes"),
        ),
        context_segment_count=_sum_optional(completed, "context_segment_count"),
        context_tokens=_sum_optional(completed, "context_tokens"),
        context_bytes=_sum_optional(completed, "context_bytes"),
        tool_schema_count=_prefer_total(
            timing.get("tool_schema_count_max"),
            _max_optional(completed, "tool_schema_count"),
        ),
        tool_schema_bytes=_prefer_total(
            timing.get("tool_schema_bytes_total"),
            _sum_optional(completed, "tool_schema_bytes"),
        ),
        exposed_tool_count=_max_optional(completed, "exposed_tool_count"),
        invoked_tool_count=sum(
            1
            for event in normalized
            if event["event_type"] in {"tool.request", "tool.requested", "tool.started"}
        ),
        duplicate_tool_calls=sum(
            int(event["payload"].get("duplicate_call_count", 0) or 0)
            for event in normalized
            if event["event_type"].startswith("tool.")
        ),
        policy_denials=sum(
            1
            for event in normalized
            if event["event_type"] in {"policy.denied", "tool.call.blocked"}
        ),
        cache_read_tokens=_sum_usage(completed, "cached_tokens"),
        cache_write_tokens=_sum_usage(completed, "cache_creation_tokens"),
        provider_token_ttft_ms=_optional_int(timing.get("provider_token_ttft_ms")),
        visible_text_ttft_ms=_optional_int(timing.get("time_to_first_text_ms")),
        total_wall_ms=_optional_int(timing.get("total_turn_ms")),
        task_success=_optional_bool(quality.get("task_success")),
        final_truthful=_optional_bool(quality.get("final_truthful")),
    )


def _turn_cost_inputs(
    normalized: list[dict[str, Any]],
) -> tuple[
    str | None,
    list[dict[str, Any]],
    int,
    tuple[str, ...],
    dict[str, Any],
    dict[str, Any],
    int,
]:
    delivery = next(
        (event for event in normalized if event["event_type"] in _DELIVERY_EVENT_TYPES),
        None,
    )
    delivery_timestamp = delivery["timestamp"] if delivery is not None else None
    delivery_dt = _parse_timestamp(delivery_timestamp)

    completed = [
        event for event in normalized if event["event_type"] == "llm.call.completed"
    ]
    post_delivery = sum(
        1
        for event in completed
        if delivery_dt is not None
        and (event_dt := _parse_timestamp(event["timestamp"])) is not None
        and event_dt > delivery_dt
    )
    purposes = tuple(
        str(event["payload"].get("purpose", "") or "").strip()
        for event in completed
        if str(event["payload"].get("purpose", "") or "").strip()
    )
    timing = next(
        (
            event["payload"]
            for event in reversed(normalized)
            if event["event_type"] == "chat.phase_timing"
        ),
        {},
    )
    quality = next(
        (
            event["payload"]
            for event in reversed(normalized)
            if event["event_type"] in {"turn.quality", "turn.completed"}
        ),
        {},
    )
    timing_call_total = _optional_int(timing.get("provider_calls_total")) or 0
    provider_calls_total = max(len(completed), timing_call_total)
    timing_purposes = tuple(
        str(item or "").strip()
        for item in list(timing.get("provider_call_purposes") or [])
        if str(item or "").strip()
    )
    if len(timing_purposes) > len(purposes):
        purposes = timing_purposes
    return (
        delivery_timestamp,
        completed,
        post_delivery,
        purposes,
        timing,
        quality,
        provider_calls_total,
    )


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = event.get("data")
    return {
        "event_type": str(event.get("event_type") or event.get("type") or ""),
        "timestamp": str(
            event.get("timestamp") or event.get("created_at") or event.get("ts") or ""
        ),
        "payload": payload if isinstance(payload, dict) else {},
        "seq": event.get("seq"),
    }


def _sort_key(event: dict[str, Any]) -> tuple[datetime, int]:
    parsed = _parse_timestamp(event["timestamp"]) or datetime.min
    try:
        seq = int(event.get("seq") or 0)
    except (TypeError, ValueError):
        seq = 0
    return parsed.replace(tzinfo=None), seq


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _prefer_total(primary: Any, fallback: int | None) -> int | None:
    value = _optional_int(primary)
    return value if value is not None else fallback


def _sum_optional(events: list[dict[str, Any]], key: str) -> int | None:
    values = [
        value
        for event in events
        if (value := _optional_int(event["payload"].get(key))) is not None
    ]
    return sum(values) if values else None


def _max_optional(events: list[dict[str, Any]], key: str) -> int | None:
    values = [
        value
        for event in events
        if (value := _optional_int(event["payload"].get(key))) is not None
    ]
    return max(values) if values else None


def _sum_usage(events: list[dict[str, Any]], key: str) -> int | None:
    values: list[int] = []
    for event in events:
        usage = event["payload"].get("usage")
        if not isinstance(usage, dict):
            continue
        value = _optional_int(usage.get(key))
        if value is not None:
            values.append(value)
    return sum(values) if values else None


__all__ = ["TurnCostEnvelope", "project_turn_cost"]
