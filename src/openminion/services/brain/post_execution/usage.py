from __future__ import annotations

from typing import Any

from openminion.modules.brain.runner import BrainRunner


def event_usage_int(payload: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return max(0, int(value))
        if isinstance(value, str):
            try:
                return max(0, int(float(value.strip())))
            except ValueError:
                continue
    return 0


def collect_llm_usage_totals_from_events(
    *,
    runner: BrainRunner,
    session_id: str,
    trace_id: str | None,
) -> tuple[int, int, int]:
    try:
        events = runner.session_api.list_events(session_id)
    except Exception:  # noqa: BLE001
        events = []

    normalized_trace = str(trace_id or "").strip()
    input_total = 0
    output_total = 0
    explicit_total = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("type", "")).strip() != "llm.call.completed":
            continue
        if normalized_trace:
            event_trace = str(event.get("trace_id", "")).strip()
            if event_trace and event_trace != normalized_trace:
                continue
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        input_total += event_usage_int(usage, ("input_tokens", "prompt_tokens"))
        output_total += event_usage_int(
            usage,
            ("output_tokens", "completion_tokens"),
        )
        explicit_total += event_usage_int(usage, ("total_tokens",))
    total = explicit_total or (input_total + output_total)
    return input_total, output_total, total
