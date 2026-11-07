from typing import Any

from openminion.modules.brain.runner import BrainRunner


from openminion.modules.telemetry.usage.coverage import (
    INPUT_TOKEN_KEYS,
    OUTPUT_TOKEN_KEYS,
    TOTAL_TOKEN_KEYS,
    CACHE_READ_TOKEN_KEYS,
    observed_token_value,
)


def collect_llm_call_events(
    *, runner: BrainRunner, session_id: str, trace_id: str | None, run_id: str = ""
) -> tuple[list[dict[str, Any]], bool]:
    events: list[dict[str, Any]] = []
    read_failed = False  # each kind is read alone: one failure keeps the other's facts
    for kind in ("llm.call.completed", "llm.call.failed"):
        try:
            events += runner.session_api.list_events(
                session_id, event_type=kind, limit=None
            )
        except Exception:  # noqa: BLE001
            read_failed = True
    normalized_trace = str(trace_id or "").strip()
    seen: set[tuple[str, str]] = set()
    calls = []
    for event in events:
        if event.get("type") not in {"llm.call.completed", "llm.call.failed"}:
            continue
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        event_run = str(payload.get("run_id") or "")
        if run_id and event_run:
            if event_run != run_id:
                continue
        elif normalized_trace and event.get("trace_id") != normalized_trace:
            continue
        call_id = str(payload.get("llm_call_id") or event.get("id") or "")
        key = (str(event.get("trace_id") or ""), call_id)
        if call_id and key in seen:
            continue
        if call_id:
            seen.add(key)
        calls.append(event)
    return calls, read_failed


def summarize_llm_usage(events: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        "total_input_tokens_used": 0,
        "total_output_tokens_used": 0,
        "total_tokens_used": 0,
        "cache_read_tokens": 0,
        "max_single_call_input_tokens": 0,
        "max_single_call_output_tokens": 0,
        "max_single_call_total_tokens": 0,
    }
    for event in events:
        usage = event["payload"].get("usage")
        if not isinstance(usage, dict):
            usage = {}
        input_tokens = observed_token_value(usage, INPUT_TOKEN_KEYS).value
        output_tokens = observed_token_value(usage, OUTPUT_TOKEN_KEYS).value
        observed_total = observed_token_value(usage, TOTAL_TOKEN_KEYS)
        total_tokens = (
            observed_total.value
            if observed_total.state == "reported"
            else input_tokens + output_tokens
        )
        for value, total, peak in (
            (
                input_tokens,
                "total_input_tokens_used",
                "max_single_call_input_tokens",
            ),
            (
                output_tokens,
                "total_output_tokens_used",
                "max_single_call_output_tokens",
            ),
            (total_tokens, "total_tokens_used", "max_single_call_total_tokens"),
        ):
            totals[total] += value
            totals[peak] = max(totals[peak], value)
        totals["cache_read_tokens"] += observed_token_value(
            usage, CACHE_READ_TOKEN_KEYS
        ).value
    return totals


def count_llm_calls(events: list[dict[str, Any]], fallback: int) -> dict[str, int]:
    counts = dict.fromkeys(
        "act decide entry judge plan reflect self_compaction skill_selection summarize tool_shortlist follow_up".split(),
        0,
    )
    for event in events:
        purpose = (
            str(event["payload"].get("purpose") or "").strip().lower() or "unknown"
        )
        if purpose == "respond_followup":
            purpose = "follow_up"
        counts[purpose] = counts.get(purpose, 0) + 1
    if not events and fallback > 0:
        counts["unknown"] = fallback
    return counts
