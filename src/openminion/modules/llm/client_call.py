from __future__ import annotations

import json
from typing import Any

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.modules.llm.providers.base import (
    ProviderHistoryMessage,
    ProviderRequest,
    ProviderResponse,
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

_CONTINUATION_PROMPT: str = ACTIVE_TASK_CONTINUATION_PROMPT


def continuation_prompt(*, original_request: str = "") -> str:
    return str(build_active_task_continuation_prompt(original_request=original_request))


def serialize_thinking_blocks_payload(
    raw_blocks: list[Any] | None,
) -> list[dict[str, Any]]:
    return serialize_thinking_blocks(raw_blocks)


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
            "prompt_tokens": getattr(raw_usage, "prompt_tokens", None),
            "completion_tokens": getattr(raw_usage, "completion_tokens", None),
            "total_tokens": getattr(raw_usage, "total_tokens", None),
            "input_tokens": getattr(raw_usage, "input_tokens", None),
            "output_tokens": getattr(raw_usage, "output_tokens", None),
            "cached_tokens": getattr(raw_usage, "cached_tokens", None),
            "cache_creation_tokens": getattr(
                raw_usage,
                "cache_creation_tokens",
                None,
            ),
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


def optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def metadata_user_prompt(metadata: dict[str, str]) -> str:
    for key in ("user_input", "original_user_input", "last_user_input"):
        value = str(metadata.get(key, "") or "").strip()
        if value:
            return continuation_prompt(original_request=value)
    return _CONTINUATION_PROMPT


def successful_tool_names_from_history(
    history_entries: list[tuple[str, str, dict[str, Any]]],
) -> tuple[str, ...]:
    successful: list[str] = []
    for role, content, meta in history_entries:
        if role != "tool":
            continue
        tool_name = str(meta.get("tool_name", "") or "").strip()
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
        if not content:
            continue
        if role not in {"system", "user", "assistant", "tool"}:
            role = "user"
        normalized.append((role, content, dict(getattr(message, "meta", {}) or {})))
    return normalized


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
    if prompt_index >= 0:
        history_entries = list(conversational[:prompt_index]) + list(
            conversational[prompt_index + 1 :]
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
    history = [
        ProviderHistoryMessage(role=role, content=content, meta=dict(meta or {}))
        for role, content, meta in history_entries
    ]
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
    if not history:
        return history
    return history[-2:] if purpose == "decide" else []


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
    assistant_messages = []
    if str(resp.text or "").strip():
        assistant_messages.append(Message(role="assistant", content=str(resp.text)))
    return {
        **structured_fields,
        "ok": True,
        "provider": str(client_name),
        "model": str(resp.model or req.model or ""),
        "output_text": str(resp.text or ""),
        "assistant_messages": assistant_messages,
        "tool_calls": [
            ToolCall(id=tc.id or "call_1", name=tc.name, arguments=tc.arguments)
            for tc in resp.tool_calls
        ],
        "thinking": serialize_thinking_blocks_payload(list(resp.thinking or [])),
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
        "provider_raw": None,
        "telemetry": {"trace_context": trace_context},
    }


__all__ = [
    "extract_structured_response_fields",
    "latest_prompt_and_history",
    "llm_response_kwargs",
    "normalized_messages",
    "normalized_provider_response",
    "provider_tool_choice",
    "provider_tools_from_request",
    "raw_response_model_name",
    "request_metadata",
    "request_mode_name",
    "request_purpose",
    "split_system_and_conversation",
    "token_usage_values",
    "trim_submit_output_history",
    "usage_payload_from_response_usage",
]
