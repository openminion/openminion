from __future__ import annotations

import json
from typing import Any

from ..schemas import TelemetryEvent

_PROSE_KEYS = frozenset(
    {
        "assistant_body",
        "assistant_text",
        "body",
        "content",
        "final_text",
        "message",
        "summary",
        "system_prompt",
        "text",
        "user_message",
    }
)

_GEN_AI_LLM_EVENT_TYPES = frozenset(
    {
        "llm.call.started",
        "llm.call.completed",
        "llm.call.failed",
        "llm.request.started",
        "llm.ensemble.completed",
        "llm.candidate.finished",
        "llm.judge.completed",
        "llm_call",
    }
)
_GEN_AI_INPUT_TOKEN_KEYS = ("input_tokens", "prompt_tokens")
_GEN_AI_OUTPUT_TOKEN_KEYS = ("output_tokens", "completion_tokens")


def attributes_for_event(
    event: TelemetryEvent,
    *,
    include_assistant_body: bool,
) -> dict[str, Any]:
    flattened: dict[str, Any] = {
        "openminion.event_type": str(event.event_type or ""),
        "openminion.session_id": str(event.session_id or ""),
        "openminion.turn_id": str(event.turn_id or ""),
    }
    for field_name in ("invocation_id", "execution_id", "agent_id"):
        value = getattr(event, field_name)
        if value:
            flattened[f"openminion.{field_name}"] = str(value)
    if event.mode:
        flattened["openminion.mode"] = str(event.mode)
    _flatten_payload(
        event.data,
        prefix="openminion.payload",
        out=flattened,
        include_assistant_body=include_assistant_body,
    )
    flattened.update(_gen_ai_attributes_for_event(event))
    flattened.update(tool_attributes_for_event(event))
    flattened.update(agent_attributes_for_event(event))
    return flattened


def _gen_ai_attributes_for_event(event: TelemetryEvent) -> dict[str, Any]:
    event_type = str(event.event_type or "").strip()
    if event_type not in _GEN_AI_LLM_EVENT_TYPES:
        return {}

    payload = event.data if isinstance(event.data, dict) else {}
    attributes: dict[str, Any] = {"gen_ai.operation.name": "chat"}

    model = payload.get("model") or payload.get("model_id")
    if model:
        attributes["gen_ai.request.model"] = str(model)

    provider = (
        payload.get("provider") or payload.get("provider_name") or payload.get("vendor")
    )
    if provider:
        attributes["gen_ai.provider.name"] = str(provider)

    response_model = payload.get("response_model")
    if response_model:
        attributes["gen_ai.response.model"] = str(response_model)

    purpose = payload.get("purpose")
    if purpose:
        attributes["openminion.model.purpose"] = str(purpose)

    usage = payload.get("usage")
    if isinstance(usage, dict):
        input_tokens = _first_int(usage, _GEN_AI_INPUT_TOKEN_KEYS)
        if input_tokens is not None:
            attributes["gen_ai.usage.input_tokens"] = input_tokens
        output_tokens = _first_int(usage, _GEN_AI_OUTPUT_TOKEN_KEYS)
        if output_tokens is not None:
            attributes["gen_ai.usage.output_tokens"] = output_tokens
        for source_key, target_key in (
            ("cached_tokens", "gen_ai.usage.cache_read.input_tokens"),
            ("cache_creation_tokens", "gen_ai.usage.cache_write.input_tokens"),
            ("reasoning_tokens", "gen_ai.usage.reasoning_tokens"),
        ):
            value = _first_int(usage, (source_key,))
            if value is not None:
                attributes[target_key] = value

    response_id = (
        payload.get("response_id")
        or payload.get("llm_call_id")
        or payload.get("request_id")
    )
    if response_id:
        attributes["gen_ai.response.id"] = str(response_id)

    finish_reason = payload.get("finish_reason") or payload.get("stop_reason")
    if finish_reason:
        attributes["gen_ai.response.finish_reasons"] = json.dumps(
            [str(finish_reason)],
            ensure_ascii=True,
            separators=(",", ":"),
        )

    for source_key, target_key in (
        ("provider_round_trip_ms", "openminion.model.provider_round_trip_ms"),
        ("time_to_first_token_ms", "openminion.model.time_to_first_token_ms"),
        ("queue_time_ms", "openminion.model.queue_time_ms"),
        ("reasoning_duration_ms", "openminion.model.reasoning_duration_ms"),
        ("retry_count", "openminion.model.retry_count"),
        ("configured_output_limit", "gen_ai.request.max_tokens"),
        ("request_bytes", "openminion.model.request_bytes"),
        ("response_bytes", "openminion.model.response_bytes"),
        ("message_count", "openminion.model.message_count"),
        ("context_tokens", "openminion.model.context_tokens"),
        ("entry_tool_spec_count", "openminion.model.tool_schema_count"),
    ):
        value = payload.get(source_key)
        if isinstance(value, (int, float)):
            attributes[target_key] = value

    cost = payload.get("cost_usd")
    cost_source = payload.get("cost_source")
    if isinstance(cost, (int, float)) and cost_source:
        attributes["gen_ai.usage.cost.usd"] = float(cost)
        attributes["openminion.model.cost_source"] = str(cost_source)

    status = str(payload.get("status") or "").strip()
    if status:
        attributes["openminion.status"] = status
    error = payload.get("error")
    if isinstance(error, dict):
        error_type = error.get("type") or error.get("code") or error.get("category")
        if error_type:
            attributes["error.type"] = str(error_type)
        for key in ("code", "category", "message"):
            value = error.get(key)
            if value:
                attributes[f"openminion.error.{key}"] = str(value)
    elif error:
        attributes["error.type"] = str(error)

    return attributes


def model_span_name(event: TelemetryEvent) -> str:
    model = str((event.data or {}).get("model") or "").strip()
    return f"chat {model}" if model else "chat"


def tool_span_name(event: TelemetryEvent) -> str:
    tool_name = str((event.data or {}).get("tool_name") or "").strip()
    return f"execute_tool {tool_name}" if tool_name else "execute_tool"


def tool_attributes_for_event(event: TelemetryEvent) -> dict[str, Any]:
    if not str(event.event_type or "").startswith("tool.execution."):
        return {}
    payload = event.data if isinstance(event.data, dict) else {}
    attributes: dict[str, Any] = {"gen_ai.operation.name": "execute_tool"}
    tool_name = payload.get("tool_name")
    if tool_name:
        attributes["gen_ai.tool.name"] = str(tool_name)
    status = payload.get("status")
    if status:
        attributes["openminion.status"] = str(status)
    error = payload.get("error")
    if isinstance(error, dict):
        error_type = error.get("type") or error.get("code")
        if error_type:
            attributes["error.type"] = str(error_type)
    return attributes


def agent_span_name(event: TelemetryEvent) -> str:
    payload = event.data if isinstance(event.data, dict) else {}
    event_type = str(event.event_type or "")
    agent_name = str(
        payload.get("target_agent") or payload.get("agent_name") or event.agent_id or ""
    ).strip()
    if event_type.startswith("agent.turn."):
        return "openminion.turn"
    if event_type.startswith("agent.phase."):
        phase = str(payload.get("phase") or "").strip()
        if phase == "plan":
            return f"plan {agent_name}" if agent_name else "plan"
        return f"openminion.phase {phase}" if phase else "openminion.phase"
    operation = str(payload.get("operation") or "invoke_agent").strip()
    return f"{operation} {agent_name}" if agent_name else operation


def span_kind_for_event(event: TelemetryEvent) -> str:
    payload = event.data if isinstance(event.data, dict) else {}
    if str(event.event_type or "") == "llm.call.started":
        return "CLIENT"
    if (
        str(event.event_type or "") == "agent.handoff.started"
        and str(payload.get("handoff_role") or "").lower() == "caller"
    ):
        return "CLIENT"
    return "INTERNAL"


def agent_attributes_for_event(event: TelemetryEvent) -> dict[str, Any]:
    if not str(event.event_type or "").startswith("agent."):
        return {}
    payload = event.data if isinstance(event.data, dict) else {}
    attributes: dict[str, Any] = {}
    if str(event.event_type).startswith(("agent.execution.", "agent.handoff.")):
        attributes["gen_ai.operation.name"] = str(
            payload.get("operation") or "invoke_agent"
        )
    agent_name = payload.get("target_agent") or payload.get("agent_name")
    if agent_name:
        attributes["gen_ai.agent.name"] = str(agent_name)
    phase = payload.get("phase")
    if phase:
        attributes["openminion.phase"] = str(phase)
    return attributes


def _first_int(source: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _flatten_payload(
    value: Any,
    *,
    prefix: str,
    out: dict[str, Any],
    include_assistant_body: bool,
) -> None:
    key_name = prefix.rsplit(".", 1)[-1].lower()
    if isinstance(value, dict):
        for key, item in value.items():
            clean_key = str(key or "").strip()
            if not clean_key:
                continue
            _flatten_payload(
                item,
                prefix=f"{prefix}.{clean_key}",
                out=out,
                include_assistant_body=include_assistant_body,
            )
        return
    if isinstance(value, (list, tuple)):
        if not include_assistant_body and key_name in _PROSE_KEYS:
            return
        out[prefix] = json.dumps(
            _normalize_otel_json_value(
                list(value),
                include_assistant_body=include_assistant_body,
            ),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return
    if isinstance(value, bool | int | float):
        out[prefix] = value
        return
    if value is None:
        return
    text = str(value)
    if not include_assistant_body and key_name in _PROSE_KEYS:
        return
    out[prefix] = text


def _normalize_otel_json_value(
    value: Any,
    *,
    include_assistant_body: bool,
) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            clean_key = str(key or "").strip()
            if not clean_key:
                continue
            if not include_assistant_body and clean_key.lower() in _PROSE_KEYS:
                continue
            normalized[clean_key] = _normalize_otel_json_value(
                item,
                include_assistant_body=include_assistant_body,
            )
        return normalized
    if isinstance(value, (list, tuple)):
        return [
            _normalize_otel_json_value(
                item,
                include_assistant_body=include_assistant_body,
            )
            for item in value
        ]
    if isinstance(value, bool | int | float) or value is None:
        return value
    return str(value)


__all__ = [
    "agent_span_name",
    "attributes_for_event",
    "model_span_name",
    "span_kind_for_event",
    "tool_span_name",
]
