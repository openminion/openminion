from __future__ import annotations

import json
import math
from typing import Any

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.modules.llm.providers.base import (
    ProviderHistoryMessage,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderToolSpec,
)
from openminion.modules.llm.providers.normalization import normalize_provider_response
from openminion.modules.llm.schemas import Message, ToolCall, UsageInfo
from openminion.modules.llm.thinking import serialize_thinking_blocks
from openminion.modules.prompting.continuation import (
    ACTIVE_TASK_CONTINUATION_PROMPT,
    build_active_task_continuation_prompt,
    build_successful_tool_continuation_prompt,
)

_STRUCTURED_RESPONSE_FIELD_NAMES: tuple[str, ...] = (
    "pending_turn_context",
    "confident_complete",
    STATE_KEY_FINALIZATION_STATUS,
    "meta_rule_preference",
    "memory_consolidation",
    "watch_outcome",
    "session_work_summary",
    "goal_declaration",
    "goal_revision",
    "delegation_context",
    "delegation_result_summary",
    "task_plan",
    "task_plan_step_completed",
    "task_plan_step_blocked",
    "task_plan_revision",
    "task_plan_abandoned",
    "task_plan_completed",
)


def extract_structured_response_fields(raw_response: Any) -> dict[str, Any]:
    if raw_response is None:
        return {}
    extracted: dict[str, Any] = {}
    for field_name in _STRUCTURED_RESPONSE_FIELD_NAMES:
        if isinstance(raw_response, dict):
            value = raw_response.get(field_name)
        else:
            value = getattr(raw_response, field_name, None)
        if value is not None:
            extracted[field_name] = value
    return extracted


def _normalized_total_source(source: dict[str, Any]) -> str:
    normalized = str(
        source.get("total_source") or source.get("total_tokens_source") or ""
    ).strip()
    return normalized if normalized in {"provider", "derived"} else ""


def usage_payload_from_response_usage(raw_usage: Any) -> dict[str, Any]:
    if raw_usage is None:
        return {}
    if isinstance(raw_usage, dict):
        source = raw_usage
    elif hasattr(raw_usage, "model_dump"):
        dumped = raw_usage.model_dump(mode="json")
        source = dumped if isinstance(dumped, dict) else {}
    else:
        source = {
            key: getattr(raw_usage, key, None)
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "total_source",
                "total_tokens_source",
                "input_tokens",
                "output_tokens",
                "cached_tokens",
                "cache_read_input_tokens",
                "cache_creation_tokens",
                "cache_creation_input_tokens",
            )
        }

    usage: dict[str, Any] = {}
    key_pairs = (
        ("prompt_tokens", ("prompt_tokens", "input_tokens")),
        ("completion_tokens", ("completion_tokens", "output_tokens")),
        ("total_tokens", ("total_tokens",)),
        ("cached_tokens", ("cached_tokens", "cache_read_input_tokens")),
        (
            "cache_creation_tokens",
            ("cache_creation_tokens", "cache_creation_input_tokens"),
        ),
    )
    for output_key, candidate_keys in key_pairs:
        for key in candidate_keys:
            value = source.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                usage[output_key] = max(0, int(value))
                break
    total_source = _normalized_total_source(source)
    if "total_tokens" in usage:
        usage["total_source"] = total_source or "provider"
    elif "prompt_tokens" in usage or "completion_tokens" in usage:
        usage["total_tokens"] = int(usage.get("prompt_tokens", 0)) + int(
            usage.get("completion_tokens", 0)
        )
        usage["total_source"] = "derived"
    return usage


def response_cost_payload(response: Any) -> dict[str, Any]:
    raw_cost = (
        response.get("cost_usd")
        if isinstance(response, dict)
        else getattr(response, "cost_usd", None)
    )
    if isinstance(raw_cost, bool):
        return {}
    try:
        cost = float(raw_cost)
    except (TypeError, ValueError):
        return {}
    if cost < 0 or not math.isfinite(cost):
        return {}
    return {"cost_usd": cost, "cost_source": "provider"}


def optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def metadata_user_prompt(metadata: dict[str, str]) -> str:
    for key in ("user_input", "original_user_input", "last_user_input"):
        value = str(metadata.get(key, "") or "").strip()
        if value:
            return build_active_task_continuation_prompt(original_request=value)
    return ACTIVE_TASK_CONTINUATION_PROMPT


def successful_tool_names_from_history(
    history_entries: list[tuple[str, str, dict[str, Any]]],
) -> tuple[str, ...]:
    successful: list[str] = []
    call_names: dict[str, str] = {}
    for role, content, meta in history_entries:
        if role == "assistant":
            for call in meta.get("tool_calls", []):
                if not isinstance(call, dict):
                    continue
                call_id = str(call.get("id", "") or "").strip()
                name = str(call.get("name", "") or "").strip()
                if call_id and name:
                    call_names[call_id] = name
            continue
        if role != "tool":
            continue
        call_id = str(meta.get("tool_call_id", "") or "").strip()
        tool_name = (
            call_names.get(call_id) or str(meta.get("tool_name", "") or "").strip()
        )
        if not tool_name:
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("status", "") or "").strip().lower() != "success":
            continue
        successful.append(tool_name)
    return tuple(successful)


def continuation_prompt_with_history(
    *,
    metadata: dict[str, str],
    history_entries: list[tuple[str, str, dict[str, Any]]],
) -> str:
    return build_successful_tool_continuation_prompt(
        base_prompt=metadata_user_prompt(metadata),
        successful_tools=successful_tool_names_from_history(history_entries),
    )


def request_metadata(req: Any) -> dict[str, str]:
    if not isinstance(getattr(req, "metadata", None), dict):
        return {}
    return {
        str(key): str(value) for key, value in req.metadata.items() if str(key).strip()
    }


def normalized_messages(req: Any) -> list[tuple[str, str, dict[str, Any]]]:
    normalized: list[tuple[str, str, dict[str, Any]]] = []
    for message in list(getattr(req, "messages", []) or []):
        role = str(getattr(message, "role", "")).strip().lower()
        content = str(getattr(message, "content", "")).strip()
        meta = dict(getattr(message, "meta", {}) or {})
        tool_calls = list(getattr(message, "tool_calls", []) or [])
        tool_call_id = str(getattr(message, "tool_call_id", "") or "").strip()
        tool_status = str(getattr(message, "tool_status", "") or "").strip()
        if tool_calls:
            meta["tool_calls"] = [
                {
                    "id": getattr(call, "id", None),
                    "name": str(getattr(call, "name", "") or ""),
                    "arguments": dict(getattr(call, "arguments", {}) or {}),
                    **(
                        {"batch_index": int(getattr(call, "batch_index", 0))}
                        if int(getattr(call, "batch_index", 0))
                        else {}
                    ),
                    **(
                        {"depends_on": list(getattr(call, "depends_on", []) or [])}
                        if getattr(call, "depends_on", None)
                        else {}
                    ),
                }
                for call in tool_calls
            ]
        if tool_call_id:
            meta["tool_call_id"] = tool_call_id
        if tool_status:
            meta["tool_status"] = tool_status
        tool_output = getattr(message, "tool_output", None)
        tool_error = getattr(message, "tool_error", None)
        if tool_output is not None:
            meta["tool_output"] = tool_output
        if tool_error is not None:
            meta["tool_error"] = dict(tool_error)
        if not content and not tool_calls and not tool_call_id:
            continue
        if role not in {"system", "user", "assistant", "tool"}:
            role = "user"
        normalized.append((role, content, meta))
    return normalized


def provider_history_payload(message: ProviderHistoryMessage) -> dict[str, Any] | None:
    role = message.role.strip().lower()
    if role not in {"system", "user", "assistant", "tool"}:
        role = "user"
    content = message.content.strip()
    tool_call_id = str(message.tool_call_id or "").strip()
    if not content and not message.tool_calls and not tool_call_id:
        return None

    payload: dict[str, Any] = {"role": role, "content": content}
    if message.meta:
        payload["meta"] = dict(message.meta)
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": str(call.id or ""),
                "name": call.name,
                "arguments": dict(call.arguments),
                "depends_on": list(call.depends_on),
            }
            for call in message.tool_calls
        ]
    if tool_call_id:
        payload.update(
            {
                "tool_call_id": tool_call_id,
                "tool_status": str(message.tool_status or ""),
                "tool_output": message.tool_output,
                "tool_error": message.tool_error,
            }
        )
    return payload


def response_telemetry_event_fields(response: Any) -> dict[str, Any]:
    telemetry = response.telemetry
    trace_context = telemetry.get("trace_context", {})
    payload = {
        "trace_artifact_paths": trace_context.get("trace_artifact_paths", []),
        "trace_artifacts_complete": trace_context.get(
            "trace_artifacts_complete", True
        ),
    }
    request_id = str(telemetry.get("request_id") or "").strip()
    if request_id:
        payload["request_id"] = request_id
    return payload


def split_system_and_conversation(
    messages: list[tuple[str, str, dict[str, Any]]],
) -> tuple[str, list[tuple[str, str, dict[str, Any]]]]:
    system_chunks: list[str] = []
    conversational: list[tuple[str, str, dict[str, Any]]] = []
    for role, content, meta in messages:
        if role == "system":
            system_chunks.append(content)
        else:
            conversational.append((role, content, meta))
    return "\n\n".join(
        chunk for chunk in system_chunks if chunk.strip()
    ).strip(), conversational


def latest_prompt_and_history(
    *,
    conversational: list[tuple[str, str, dict[str, Any]]],
    metadata: dict[str, str],
) -> tuple[str, list[ProviderHistoryMessage]]:
    latest_msg = ""
    prompt_index = -1
    for idx in range(len(conversational) - 1, -1, -1):
        role, content, _meta = conversational[idx]
        if role == "user":
            latest_msg = content
            prompt_index = idx
            break
    if prompt_index == len(conversational) - 1:
        history_entries = (
            conversational[:prompt_index] + conversational[prompt_index + 1 :]
        )
    elif conversational:
        history_entries = list(conversational)
        latest_msg = continuation_prompt_with_history(
            metadata=metadata,
            history_entries=history_entries,
        )
    else:
        history_entries = []
    while (
        history_entries
        and history_entries[-1][0] == "user"
        and history_entries[-1][1].strip() == latest_msg.strip()
    ):
        history_entries.pop()
    history = []
    for role, content, meta in history_entries:
        meta_value = dict(meta or {})
        tool_calls = []
        for raw_call in meta_value.pop("tool_calls", []):
            if not isinstance(raw_call, dict):
                continue
            name = str(raw_call.get("name", "") or "").strip()
            if not name:
                continue
            arguments = raw_call.get("arguments")
            tool_calls.append(
                ProviderToolCall(
                    id=str(raw_call.get("id", "") or ""),
                    name=name,
                    arguments=dict(arguments) if isinstance(arguments, dict) else {},
                    source=str(raw_call.get("source", "") or "native"),
                    depends_on=[
                        str(item)
                        for item in raw_call.get("depends_on", [])
                        if str(item).strip()
                    ],
                )
            )
        tool_call_id = str(meta_value.pop("tool_call_id", "") or "") or None
        tool_status = str(meta_value.pop("tool_status", "") or "") or None
        tool_output = meta_value.pop("tool_output", None)
        raw_tool_error = meta_value.pop("tool_error", None)
        history.append(
            ProviderHistoryMessage(
                role=role,
                content=content,
                meta=meta_value,
                tool_calls=tool_calls,
                tool_call_id=tool_call_id,
                tool_status=tool_status,
                tool_output=tool_output,
                tool_error=raw_tool_error if isinstance(raw_tool_error, dict) else None,
            )
        )
    return latest_msg, history


def provider_tools_from_request(req: Any) -> list[ProviderToolSpec]:
    return [
        ProviderToolSpec(
            name=tool.name,
            description=tool.description,
            parameters=tool.input_schema,
        )
        for tool in list(getattr(req, "tools", []) or [])
    ]


def provider_tool_choice(req: Any) -> str | dict[str, Any]:
    raw_tool_choice = getattr(req, "tool_choice", None)
    if isinstance(raw_tool_choice, str):
        normalized_choice = raw_tool_choice.strip().lower()
        if normalized_choice in {"auto", "none", "required"}:
            return normalized_choice
    if isinstance(raw_tool_choice, dict):
        return dict(raw_tool_choice)
    return "auto"


def request_purpose(metadata: dict[str, str]) -> str:
    return str(metadata.get("purpose", "")).strip().lower()


def request_mode_name(metadata: dict[str, str]) -> str | None:
    return (
        str(metadata.get("mode_name") or metadata.get("mode") or "").strip().lower()
        or None
    )


def trim_submit_output_history(
    *,
    tools: list[ProviderToolSpec],
    history: list[ProviderHistoryMessage],
    purpose: str,
) -> list[ProviderHistoryMessage]:
    if not tools or not all(
        str(spec.name).strip() == "submit_output" for spec in tools
    ):
        return history
    return history if purpose == "decide" else []


def raw_response_model_name(raw_response: Any) -> str:
    if isinstance(raw_response, dict):
        return str(raw_response.get("model", "") or "")
    return str(getattr(raw_response, "model", "") or "")


def normalized_provider_response(
    *,
    raw_response: Any,
    provider_name: str,
    provider_request: ProviderRequest,
) -> ProviderResponse:
    if isinstance(raw_response, ProviderResponse):
        return raw_response
    return normalize_provider_response(
        raw_response,
        provider_name=provider_name,
        model_name=raw_response_model_name(raw_response),
        allowed_tool_names=[
            spec.name for spec in provider_request.tools if str(spec.name).strip()
        ],
    )


def token_usage_values(
    usage_payload: dict[str, Any],
) -> tuple[int | None, int | None, int, int, int, int]:
    prompt_tokens = usage_payload.get("prompt_tokens")
    completion_tokens = usage_payload.get("completion_tokens")
    total_tokens = usage_payload.get("total_tokens")
    if total_tokens is None:
        total_tokens = (
            sum(
                int(value)
                for value in usage_payload.values()
                if isinstance(value, (int, float))
            )
            or 0
        )
    input_tokens = int(prompt_tokens) if isinstance(prompt_tokens, (int, float)) else 0
    output_tokens = (
        int(completion_tokens) if isinstance(completion_tokens, (int, float)) else 0
    )
    cached_tokens = int(usage_payload.get("cached_tokens", 0) or 0)
    return (
        prompt_tokens,
        completion_tokens,
        total_tokens,
        input_tokens,
        output_tokens,
        cached_tokens,
    )


def llm_response_kwargs(
    *,
    resp: ProviderResponse,
    req: Any,
    client_name: str,
    structured_fields: dict[str, Any],
    trace_context: dict[str, Any],
) -> dict[str, Any]:
    usage_payload = usage_payload_from_response_usage(resp.usage)
    prompt_tokens, completion_tokens, total_tokens, _input, _output, cached_tokens = (
        token_usage_values(usage_payload)
    )
    tool_calls = [
        ToolCall(
            id=tc.id or f"call_{index + 1}",
            name=tc.name,
            arguments=tc.arguments,
            batch_index=index,
            depends_on=list(tc.depends_on),
        )
        for index, tc in enumerate(resp.tool_calls)
    ]
    assistant_messages = []
    if str(resp.text or "").strip() or tool_calls:
        assistant_messages.append(
            Message(
                role="assistant",
                content=str(resp.text or ""),
                tool_calls=tool_calls,
            )
        )
    return {
        **structured_fields,
        "ok": True,
        "provider": str(client_name),
        "model": str(resp.model or req.model or ""),
        "output_text": str(resp.text or ""),
        "assistant_messages": assistant_messages,
        "tool_calls": tool_calls,
        "thinking": serialize_thinking_blocks(list(resp.thinking or [])),
        "usage": UsageInfo(
            input_tokens=optional_int(prompt_tokens),
            output_tokens=optional_int(completion_tokens),
            total_tokens=optional_int(total_tokens),
            total_source=str(usage_payload.get("total_source") or "") or None,
            cached_tokens=cached_tokens,
            cache_creation_tokens=usage_payload.get("cache_creation_tokens"),
        ),
        "latency_ms": 0,
        "finish_reason": str(resp.finish_reason or ""),
        "empty_payload_recovered": (
            resp.normalization.get("empty_payload_recovered") is True
        ),
        "provider_raw": None,
        "telemetry": {"trace_context": trace_context},
    }


__all__ = [
    "extract_structured_response_fields",
    "latest_prompt_and_history",
    "llm_response_kwargs",
    "normalized_messages",
    "provider_history_payload",
    "normalized_provider_response",
    "provider_tool_choice",
    "provider_tools_from_request",
    "raw_response_model_name",
    "request_metadata",
    "request_mode_name",
    "request_purpose",
    "response_cost_payload",
    "response_telemetry_event_fields",
    "split_system_and_conversation",
    "token_usage_values",
    "trim_submit_output_history",
    "usage_payload_from_response_usage",
]
