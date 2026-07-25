from typing import Any

from openminion.modules.brain.runner import BrainRunner

LLMUsageEventTotals = tuple[int, int, int, int, int, int]


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
    input_total, output_total, total, *_ = collect_llm_usage_summary_from_events(
        runner=runner,
        session_id=session_id,
        trace_id=trace_id,
    )
    return input_total, output_total, total


def collect_llm_usage_summary_from_events(
    *,
    runner: BrainRunner,
    session_id: str,
    trace_id: str | None,
) -> LLMUsageEventTotals:
    try:
        events = runner.session_api.list_events(session_id)
    except Exception:  # noqa: BLE001
        events = []

    normalized_trace = str(trace_id or "").strip()
    input_total = output_total = explicit_total = 0
    max_input = max_output = max_total = 0
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
        input_tokens = event_usage_int(usage, ("input_tokens", "prompt_tokens"))
        output_tokens = event_usage_int(
            usage,
            ("output_tokens", "completion_tokens"),
        )
        total_tokens = event_usage_int(usage, ("total_tokens",))
        input_total += input_tokens
        output_total += output_tokens
        explicit_total += total_tokens
        max_input = max(max_input, input_tokens)
        max_output = max(max_output, output_tokens)
        max_total = max(max_total, total_tokens or input_tokens + output_tokens)
    total = explicit_total or (input_total + output_total)
    return input_total, output_total, total, max_input, max_output, max_total
