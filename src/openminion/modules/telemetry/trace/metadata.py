"""Stable metadata assembly for provider trace results."""

import json
from typing import Any, cast


_SENSITIVE_FIELDS = frozenset(
    {
        "content",
        "system_prompt",
        "system_instructions",
        "user_message",
        "history",
        "input_messages",
        "output_messages",
        "output_text",
        "arguments",
        "result",
        "tool_definitions",
        "file_path",
        "path",
        "command",
        "diff_body",
        "itinerary_details",
        "raw_source",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.system_instructions",
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
        "gen_ai.tool.definitions",
    }
)
_PROHIBITED_FIELDS = frozenset(
    {
        "api_key",
        "access_token",
        "approval_token",
        "auth_header",
        "authorization",
        "cookie",
        "credentials",
        "environment",
        "env",
        "headers",
        "hidden_reasoning",
        "hidden_chain_of_thought",
        "private_key",
        "password",
        "refresh_token",
        "raw_env",
        "reasoning_content",
        "reasoning_summary",
        "thinking",
        "thinking_blocks",
        "gen_ai.reasoning",
    }
)


def apply_content_policy(
    payload: dict[str, Any],
    *,
    allow_sensitive_content: bool,
    allowed_sensitive_fields: frozenset[str] = frozenset(),
    max_string_length: int = 4096,
) -> dict[str, Any]:
    removed: list[str] = []
    truncated: list[dict[str, int | str]] = []

    def clean(value: Any, path: str) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for raw_key, item in value.items():
                key = str(raw_key)
                field = key.lower()
                item_path = f"{path}.{key}" if path else key
                if field in _PROHIBITED_FIELDS or field.endswith("_secret"):
                    removed.append(item_path)
                    continue
                if (
                    field in _SENSITIVE_FIELDS
                    and not allow_sensitive_content
                    and field not in allowed_sensitive_fields
                ):
                    removed.append(item_path)
                    continue
                result[key] = clean(item, item_path)
            return result
        if isinstance(value, list):
            return [clean(item, f"{path}[]") for item in value]
        if isinstance(value, str) and len(value) > max_string_length:
            truncated.append(
                {
                    "field": path,
                    "original_size": len(value),
                    "retained_size": max_string_length,
                }
            )
            return value[:max_string_length]
        return value

    cleaned = clean(dict(payload or {}), "")
    cleaned["_telemetry_policy"] = {
        "sensitive_content": "included" if allow_sensitive_content else "omitted",
        "removed_fields": sorted(set(removed)),
        "truncations": truncated,
    }
    return cast(dict[str, Any], cleaned)


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


__all__ = ["apply_content_policy", "merge_trace_metadata"]
