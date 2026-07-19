"""Stable metadata assembly for provider trace results."""

import json


def merge_trace_metadata(
    metadata: dict[str, str],
    *,
    model: str | None,
    provider_name: str,
    inference_steps: int,
    untrusted_metadata: dict[str, str],
    untrusted_events: list[dict[str, str]],
    self_improvement_metadata: dict[str, str],
) -> dict[str, str]:
    merged = dict(metadata)
    merged.setdefault("model_tool_name", "")
    merged.setdefault("runtime_binding_id", "")
    merged.setdefault("runtime_tool_name", "")
    merged.setdefault("runtime_fallback_chain", "[]")
    merged.setdefault("runtime_fallback_used", "false")
    merged.setdefault("runtime_resolution_source", "")
    if model and not merged.get("model"):
        merged["model"] = str(model)
    merged.setdefault("provider", provider_name)
    merged["inference_steps"] = str(inference_steps)
    merged.update(untrusted_metadata)
    merged.update(self_improvement_metadata)
    events: list[dict[str, str]] = []
    raw_events = str(merged.get("security_events", "")).strip()
    if raw_events:
        try:
            parsed = json.loads(raw_events)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            events.extend(
                {str(key): str(value) for key, value in item.items()}
                for item in parsed
                if isinstance(item, dict)
            )
    events.extend(untrusted_events)
    if events:
        merged["security_events"] = json.dumps(events, sort_keys=True)
    return merged


__all__ = ["merge_trace_metadata"]
