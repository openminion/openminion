"""Provider message payload builders and response parsers."""

import base64
import json
import mimetypes
from pathlib import Path
import re
from typing import Any, Dict, List, Mapping

from openminion.base.config.paths import resolve_home_root
from ..errors import LLMCtlError
from ..schemas import ImageContentPart, LLMRequest, Message, ToolCall, UsageInfo
from ..schemas import TextContentPart
from ..constants import LLM_TOOL_CALL_STATUS_PARSED
from .tool_calling import (
    build_fallback_tool_call_instruction,
    is_schema_only_submit_output_tools,
)
from openminion.base.config.parse import _as_float as _base_as_float, _as_int
from .transport.http import http_json_get, http_json_post

_as_float = _base_as_float

_SCHEMA_ONLY_SUBMIT_OUTPUT_NATIVE_INSTRUCTION = (
    "Schema-only control phase:\n"
    "1. This phase returns structured control output; it must not execute the "
    "user's task.\n"
    "2. Use only the `submit_output` tool. Do not call, describe, or wrap any "
    "other tool.\n"
    "3. Do not emit XML, JSON, markdown, or prose tool envelopes for task tools."
)

_VISIBLE_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>([\s\S]*?)</think>", re.IGNORECASE)


def _resolve_timeout_seconds(
    config: Dict[str, Any],
    default_value: int = 60,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> int:
    if isinstance(metadata, Mapping):
        metadata_timeout = _as_int(metadata.get("timeout_seconds"), 0)
        if metadata_timeout > 0:
            return metadata_timeout

    provider_timeout = _as_int(config.get("timeout_seconds"), 0)
    if provider_timeout > 0:
        return provider_timeout

    timeouts = config.get("timeouts")
    if isinstance(timeouts, dict):
        req_timeout = _as_int(timeouts.get("request_timeout_sec"), 0)
        if req_timeout > 0:
            return req_timeout

    return default_value


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, dict):
        for key in ("text", "output_text", "content", "value"):
            value = content.get(key)
            extracted = _extract_message_text(value)
            if extracted:
                return extracted
        return ""

    if isinstance(content, list):
        chunks = []
        for item in content:
            extracted = _extract_message_text(item)
            if extracted:
                chunks.append(extracted)
        return "\n".join(part.strip() for part in chunks if part.strip())

    return ""


def _sanitize_visible_assistant_text(text: str) -> str:
    candidate = str(text or "")
    if not candidate.strip():
        return ""
    sanitized = _VISIBLE_THINK_BLOCK_RE.sub("\n", candidate)
    sanitized = re.sub(r"[ \t]+\n", "\n", sanitized)
    sanitized = re.sub(r"\n[ \t]+", "\n", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    return sanitized.strip()


def _build_thinking_block(
    *,
    content: str,
    signature: str | None = None,
    redacted: bool = False,
) -> dict[str, Any] | None:
    body = str(content or "").strip()
    normalized_signature = str(signature or "").strip() or None
    if not body and not normalized_signature:
        return None
    payload: dict[str, Any] = {
        "type": "thinking",
        "content": body,
        "redacted": bool(redacted),
    }
    if normalized_signature is not None:
        payload["signature"] = normalized_signature
    return payload


def _extract_inline_think_blocks(raw_text: Any) -> list[dict[str, Any]]:
    text = _extract_message_text(raw_text)
    if not text:
        return []

    blocks: list[dict[str, Any]] = []
    for match in _VISIBLE_THINK_BLOCK_RE.finditer(text):
        block = _build_thinking_block(content=match.group(1))
        if block is not None:
            blocks.append(block)
    return blocks


def _dedupe_thinking_blocks(
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, bool]] = set()
    for item in blocks:
        if not isinstance(item, Mapping):
            continue
        key = (
            str(item.get("content", "") or "").strip(),
            str(item.get("signature", "") or "").strip(),
            bool(item.get("redacted", False)),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(item))
    return deduped


def _extract_openai_like_thinking_blocks(message_payload: Any) -> list[dict[str, Any]]:
    if not isinstance(message_payload, Mapping):
        return []
    reasoning_text = _extract_message_text(message_payload.get("reasoning"))
    blocks: list[dict[str, Any]] = []
    block = _build_thinking_block(content=reasoning_text)
    if block:
        blocks.append(block)
    blocks.extend(_extract_inline_think_blocks(message_payload.get("content")))
    return _dedupe_thinking_blocks(blocks)


def _extract_ollama_thinking_blocks(message_payload: Any) -> list[dict[str, Any]]:
    if not isinstance(message_payload, Mapping):
        return []
    thinking_text = _extract_message_text(message_payload.get("thinking"))
    block = _build_thinking_block(content=thinking_text)
    return [block] if block else []


def _extract_anthropic_thinking_blocks(response_payload: Any) -> list[dict[str, Any]]:
    if not isinstance(response_payload, Mapping):
        return []
    raw_content = response_payload.get("content")
    if not isinstance(raw_content, list):
        return []

    blocks: list[dict[str, Any]] = []
    for item in raw_content:
        if not isinstance(item, Mapping):
            continue
        block_type = str(item.get("type", "") or "").strip().lower()
        if block_type not in {"thinking", "redacted_thinking"}:
            continue
        content = _extract_message_text(
            item.get("thinking") or item.get("text") or item.get("content")
        )
        block = _build_thinking_block(
            content=content,
            signature=str(item.get("signature", "") or "").strip() or None,
            redacted=(
                block_type == "redacted_thinking"
                or bool(item.get("redacted", False))
                or (
                    not str(content or "").strip()
                    and bool(str(item.get("signature", "") or "").strip())
                )
            ),
        )
        if block is not None:
            blocks.append(block)
    return blocks


def _extract_openai_like_primary_text(
    *,
    response_payload: Dict[str, Any],
    first_choice: Dict[str, Any],
    message_payload: Dict[str, Any],
) -> tuple[str, str, str]:
    candidates: list[tuple[str, Any]] = [
        ("message.content", message_payload.get("content")),
        ("message.refusal", message_payload.get("refusal")),
        ("message.output_text", message_payload.get("output_text")),
        ("message.text", message_payload.get("text")),
        ("choice.text", first_choice.get("text")),
        ("choice.output_text", first_choice.get("output_text")),
        ("choice.content", first_choice.get("content")),
        ("response.output_text", response_payload.get("output_text")),
        ("response.text", response_payload.get("text")),
        ("response.response", response_payload.get("response")),
        ("response.content", response_payload.get("content")),
    ]
    for source, raw in candidates:
        extracted = _extract_message_text(raw)
        if extracted:
            return _sanitize_visible_assistant_text(extracted), extracted, source
    return "", "", "none"


def _collapse_system_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    system_chunks = [
        _extract_message_text(item.get("content", "")).strip()
        for item in messages
        if str(item.get("role", "")).strip().lower() == "system"
        and _extract_message_text(item.get("content", "")).strip()
    ]
    if len(system_chunks) <= 1:
        return messages

    non_system_messages = [
        dict(item)
        for item in messages
        if str(item.get("role", "")).strip().lower() != "system"
    ]
    return [
        {"role": "system", "content": "\n\n".join(system_chunks)},
        *non_system_messages,
    ]


def _ensure_openai_like_non_system_turn(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if any(str(item.get("role", "")).strip().lower() != "system" for item in messages):
        return messages
    return [*messages, {"role": "user", "content": "Continue."}]


def _resolve_local_image_path(raw_path: str) -> Path:
    token = str(raw_path or "").strip()
    if not token:
        raise LLMCtlError("INVALID_ARGUMENT", "Image path is empty")
    path = Path(token)
    if not path.is_absolute():
        root = resolve_home_root()
        path = (root / path).resolve(strict=False)
    else:
        path = path.resolve(strict=False)
    if not path.exists() or not path.is_file():
        raise LLMCtlError(
            "INVALID_ARGUMENT",
            f"Image path does not exist: {path}",
        )
    return path


def _image_part_bytes(part: ImageContentPart) -> tuple[str, str]:
    if part.source == "base64":
        mime = str(part.mime_type or "").strip().lower()
        data = str(part.data_base64 or "").strip()
        if not mime.startswith("image/") or not data:
            raise LLMCtlError(
                "INVALID_ARGUMENT",
                "Image base64 content requires image mime_type and data_base64",
            )
        return mime, data
    if part.source == "path":
        path = _resolve_local_image_path(str(part.path or ""))
        mime = str(part.mime_type or "").strip().lower()
        if not mime:
            mime = str(mimetypes.guess_type(str(path))[0] or "").strip().lower()
        if not mime.startswith("image/"):
            raise LLMCtlError(
                "INVALID_ARGUMENT",
                f"Unsupported image mime for attachment: {path}",
            )
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return mime, data
    raise LLMCtlError(
        "INVALID_ARGUMENT",
        "OpenAI-compatible image parts require source=path or source=base64",
    )


def _openai_like_content(
    message: Message,
    *,
    enable_vision_input: bool,
    supports_vision_input: bool,
) -> str | list[dict[str, Any]]:
    if not message.content_parts:
        return str(message.content or "").strip()

    parts: list[dict[str, Any]] = []
    for item in message.content_parts:
        if isinstance(item, TextContentPart):
            text = str(item.text or "").strip()
            if text:
                parts.append({"type": "text", "text": text})
            continue
        if isinstance(item, ImageContentPart):
            if not enable_vision_input:
                raise LLMCtlError(
                    "INVALID_ARGUMENT",
                    "Vision input is disabled for this provider configuration",
                )
            if not supports_vision_input:
                raise LLMCtlError(
                    "INVALID_ARGUMENT",
                    "This provider does not support image input on the current path",
                )
            if item.source == "url":
                url = str(item.url or "").strip()
                if not url:
                    raise LLMCtlError(
                        "INVALID_ARGUMENT",
                        "Image url source requires a non-empty url",
                    )
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": url,
                            "detail": str(item.detail_level or "auto"),
                        },
                    }
                )
                continue
            mime, data = _image_part_bytes(item)
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{data}",
                        "detail": str(item.detail_level or "auto"),
                    },
                }
            )
    if not parts:
        raise LLMCtlError(
            "INVALID_ARGUMENT",
            "Structured content did not produce any provider payload parts",
        )
    return parts


def _anthropic_content(
    message: Message,
    *,
    enable_vision_input: bool,
    supports_vision_input: bool,
) -> str | list[dict[str, Any]]:
    if not message.content_parts:
        return str(message.content or "").strip()

    parts: list[dict[str, Any]] = []
    for item in message.content_parts:
        if isinstance(item, TextContentPart):
            text = str(item.text or "").strip()
            if text:
                parts.append({"type": "text", "text": text})
            continue
        if isinstance(item, ImageContentPart):
            if not enable_vision_input:
                raise LLMCtlError(
                    "INVALID_ARGUMENT",
                    "Vision input is disabled for this provider configuration",
                )
            if not supports_vision_input:
                raise LLMCtlError(
                    "INVALID_ARGUMENT",
                    "This provider does not support image input on the current path",
                )
            if item.source == "url":
                url = str(item.url or "").strip()
                if not url:
                    raise LLMCtlError(
                        "INVALID_ARGUMENT",
                        "Image url source requires a non-empty url",
                    )
                parts.append(
                    {
                        "type": "image",
                        "source": {"type": "url", "url": url},
                    }
                )
                continue
            mime, data = _image_part_bytes(item)
            parts.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": data,
                    },
                }
            )
    if not parts:
        raise LLMCtlError(
            "INVALID_ARGUMENT",
            "Structured content did not produce any provider payload parts",
        )
    return parts


def _messages_openai_like(
    request: LLMRequest,
    include_fallback_instruction: bool,
    *,
    collapse_system_messages: bool = False,
    tool_name_overrides: Mapping[str, str] | None = None,
    extra_system_instruction: str = "",
    enable_vision_input: bool = False,
    supports_vision_input: bool = False,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    schema_only = bool(request.tools) and is_schema_only_submit_output_tools(
        request.tools
    )
    fallback_instruction = ""
    if include_fallback_instruction and request.tools:
        fallback_instruction = build_fallback_tool_call_instruction(
            request.tools,
            schema_only=schema_only,
            canonical_to_external=tool_name_overrides,
        )

    for msg in request.messages:
        content = _openai_like_content(
            msg,
            enable_vision_input=enable_vision_input,
            supports_vision_input=supports_vision_input,
        )
        if isinstance(content, str) and not content:
            continue
        role = (
            msg.role if msg.role in {"system", "user", "assistant", "tool"} else "user"
        )
        if role == "tool":
            meta = dict(getattr(msg, "meta", {}) or {})
            tool_call_id = str(meta.get("tool_call_id", "") or "").strip()
            tool_name = str(meta.get("tool_name", "") or "").strip()
            tool_arguments = meta.get("tool_arguments", {})
            if tool_call_id and tool_name:
                external_tool_name = (
                    str(tool_name_overrides.get(tool_name, tool_name)).strip()
                    if tool_name_overrides
                    else tool_name
                )
                if isinstance(tool_arguments, dict):
                    serialized_arguments = json.dumps(tool_arguments, sort_keys=True)
                else:
                    serialized_arguments = "{}"
                messages.append(
                    {
                        "role": "assistant",
                        "content": "Tool call issued.",
                        "tool_calls": [
                            {
                                "id": tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": external_tool_name,
                                    "arguments": serialized_arguments,
                                },
                            }
                        ],
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "content": content,
                        "tool_call_id": tool_call_id,
                    }
                )
                continue
            messages.append(
                {
                    "role": "system",
                    "content": f"Tool result ({tool_name or 'unknown'}): {content}",
                }
            )
            continue
        messages.append({"role": role, "content": content})

    if fallback_instruction:
        if schema_only:
            insert_index = len(messages)
            for idx, item in enumerate(messages):
                if item.get("role") != "system":
                    insert_index = idx
                    break
            messages.insert(
                insert_index,
                {"role": "system", "content": fallback_instruction},
            )
        else:
            messages.insert(0, {"role": "system", "content": fallback_instruction})

    extra_instruction_parts = []
    if str(extra_system_instruction or "").strip():
        extra_instruction_parts.append(str(extra_system_instruction).strip())
    if schema_only and not include_fallback_instruction:
        # Native-only providers still need an explicit phase boundary; otherwise
        # schema-only control calls can be tempted into task-tool envelopes.
        extra_instruction_parts.append(_SCHEMA_ONLY_SUBMIT_OUTPUT_NATIVE_INSTRUCTION)
    if extra_instruction_parts:
        messages.insert(
            0,
            {"role": "system", "content": "\n\n".join(extra_instruction_parts)},
        )

    if not messages:
        messages.append({"role": "user", "content": "Continue."})

    if collapse_system_messages:
        messages = _collapse_system_messages(messages)

    return _ensure_openai_like_non_system_turn(messages)


def _messages_anthropic(
    request: LLMRequest,
    include_fallback_instruction: bool,
    *,
    tool_name_overrides: Mapping[str, str] | None = None,
    enable_prompt_cache: bool = False,
    cache_system_prompt: bool = True,
    enable_vision_input: bool = False,
    supports_vision_input: bool = False,
) -> tuple[str | list[dict[str, Any]], list[dict[str, Any]]]:
    system_chunks: list[str] = []
    system_blocks: list[dict[str, Any]] = []
    fallback_instruction = ""
    schema_only = False
    if include_fallback_instruction and request.tools:
        schema_only = is_schema_only_submit_output_tools(request.tools)
        fallback_instruction = build_fallback_tool_call_instruction(
            request.tools,
            schema_only=schema_only,
            canonical_to_external=tool_name_overrides,
        )

    chat_messages: list[dict[str, Any]] = []
    for msg in request.messages:
        content = _anthropic_content(
            msg,
            enable_vision_input=enable_vision_input,
            supports_vision_input=supports_vision_input,
        )
        if isinstance(content, str) and not content:
            continue

        if msg.role == "system":
            if enable_prompt_cache:
                if isinstance(content, str):
                    system_text_blocks = [{"type": "text", "text": content}]
                elif all(
                    isinstance(item, dict) and item.get("type") == "text"
                    for item in content
                ):
                    system_text_blocks = [dict(item) for item in content]
                else:
                    raise LLMCtlError(
                        "INVALID_ARGUMENT",
                        "Anthropic system prompts must remain text-only",
                    )
                for block in system_text_blocks:
                    if cache_system_prompt and isinstance(msg.cache_control, dict):
                        cache_control = dict(msg.cache_control)
                        if cache_control:
                            block["cache_control"] = cache_control
                    system_blocks.append(block)
            else:
                if isinstance(content, str):
                    system_chunks.append(content)
                elif all(
                    isinstance(item, dict) and item.get("type") == "text"
                    for item in content
                ):
                    rendered = [
                        str(item.get("text", "")).strip()
                        for item in content
                        if str(item.get("text", "")).strip()
                    ]
                    if rendered:
                        system_chunks.append("\n\n".join(rendered))
                else:
                    raise LLMCtlError(
                        "INVALID_ARGUMENT",
                        "Anthropic system prompts must remain text-only",
                    )
            continue

        role = msg.role if msg.role in {"user", "assistant"} else "user"
        chat_messages.append({"role": role, "content": content})

    if fallback_instruction:
        if enable_prompt_cache:
            block = {"type": "text", "text": fallback_instruction}
            if schema_only:
                system_blocks.append(block)
            else:
                system_blocks.insert(0, block)
        elif schema_only:
            system_chunks.append(fallback_instruction)
        else:
            system_chunks.insert(0, fallback_instruction)

    if not chat_messages:
        chat_messages.append({"role": "user", "content": ""})

    if enable_prompt_cache:
        return system_blocks, chat_messages
    return "\n\n".join(system_chunks).strip(), chat_messages


def _usage_from_openai_like(payload: Any) -> UsageInfo:
    if not isinstance(payload, dict):
        return UsageInfo()

    prompt_tokens = payload.get("prompt_tokens")
    completion_tokens = payload.get("completion_tokens")
    total_tokens = payload.get("total_tokens")
    total_source: str | None = None
    if isinstance(total_tokens, int) and not isinstance(total_tokens, bool):
        total_source = "provider"
    elif isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
        total_tokens = prompt_tokens + completion_tokens
        total_source = "derived"

    cached_tokens: int | None = None
    details = payload.get("prompt_tokens_details")
    if isinstance(details, dict):
        raw = details.get("cached_tokens")
        if isinstance(raw, int):
            cached_tokens = raw

    return UsageInfo(
        input_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
        output_tokens=completion_tokens if isinstance(completion_tokens, int) else None,
        total_tokens=total_tokens if isinstance(total_tokens, int) else None,
        total_source=total_source,
        cached_tokens=cached_tokens,
    )


def _usage_from_ollama(payload: Dict[str, Any]) -> UsageInfo:
    prompt_eval_count = payload.get("prompt_eval_count")
    eval_count = payload.get("eval_count")
    total: int | None = None
    if isinstance(prompt_eval_count, int) and isinstance(eval_count, int):
        total = prompt_eval_count + eval_count

    return UsageInfo(
        input_tokens=prompt_eval_count if isinstance(prompt_eval_count, int) else None,
        output_tokens=eval_count if isinstance(eval_count, int) else None,
        total_tokens=total,
        total_source="derived" if total is not None else None,
    )


def _usage_from_anthropic(payload: Any) -> UsageInfo:
    if not isinstance(payload, dict):
        return UsageInfo()

    input_tokens = payload.get("input_tokens")
    output_tokens = payload.get("output_tokens")
    total: int | None = None
    if isinstance(input_tokens, int) and isinstance(output_tokens, int):
        total = input_tokens + output_tokens

    raw_cache_read = payload.get("cache_read_input_tokens")
    cached_tokens = raw_cache_read if isinstance(raw_cache_read, int) else None
    raw_cache_creation = payload.get("cache_creation_input_tokens")
    cache_creation_tokens = (
        raw_cache_creation if isinstance(raw_cache_creation, int) else None
    )

    return UsageInfo(
        input_tokens=input_tokens if isinstance(input_tokens, int) else None,
        output_tokens=output_tokens if isinstance(output_tokens, int) else None,
        total_tokens=total,
        total_source="derived" if total is not None else None,
        cached_tokens=cached_tokens,
        cache_creation_tokens=cache_creation_tokens,
    )


def _http_json_post(
    *,
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout_seconds: int,
    provider_name: str,
    trace_metadata: Dict[str, Any] | None = None,
    env: Mapping[str, object] | None = None,
) -> Dict[str, Any]:
    return http_json_post(
        url=url,
        payload=payload,
        headers=headers,
        timeout_seconds=timeout_seconds,
        provider_name=provider_name,
        trace_metadata=trace_metadata,
        env=env,
    )


def _http_json_get(
    *,
    url: str,
    headers: Dict[str, str],
    timeout_seconds: int,
    provider_name: str,
    trace_metadata: Dict[str, Any] | None = None,
    env: Mapping[str, object] | None = None,
) -> Dict[str, Any]:
    return http_json_get(
        url=url,
        headers=headers,
        timeout_seconds=timeout_seconds,
        provider_name=provider_name,
        trace_metadata=trace_metadata,
        env=env,
    )


def _resolve_api_key(
    config: Dict[str, Any], provider_name: str, required: bool = True
) -> str:
    api_key = str(config.get("api_key") or "").strip()
    if api_key:
        return api_key
    if required:
        raise LLMCtlError(
            "AUTH_ERROR",
            f"{provider_name} provider selected but API key is missing",
            {"provider": provider_name},
        )
    return ""


def _resolve_model(
    request: LLMRequest, config: Dict[str, Any], default_model: str
) -> str:
    model = str(request.model or config.get("model") or default_model).strip()
    if not model:
        raise LLMCtlError("INVALID_ARGUMENT", "Model is required")
    return model


def _resolve_tool_names(request: LLMRequest) -> List[str]:
    return [tool.name for tool in (request.tools or []) if str(tool.name).strip()]


def _decode_nested_json_object(raw_value: Any) -> dict[str, Any] | None:
    if isinstance(raw_value, dict):
        return dict(raw_value)
    if not isinstance(raw_value, str):
        return None
    token = raw_value.strip()
    if not token.startswith("{"):
        return None
    try:
        parsed = json.loads(token)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return dict(parsed)
    return None


def _decode_json_like(value: Any) -> Any:
    if isinstance(value, str):
        token = value.strip()
        if token.startswith("{") or token.startswith("["):
            try:
                decoded = json.loads(token)
            except json.JSONDecodeError:
                return value
            return _decode_json_like(decoded)
        return value
    if isinstance(value, dict):
        return {key: _decode_json_like(raw) for key, raw in value.items()}
    if isinstance(value, list):
        return [_decode_json_like(item) for item in value]
    return value


_DECISION_ALLOWED_KEYS = {
    "mode",
    "confidence",
    "reason_code",
    "sub_intents",
    "rationale",
    "act_profile",
    "execution_target",
    "question",
    "answer",
}


def _sanitize_decision_like_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    args = _decode_json_like(dict(arguments or {}))
    if not isinstance(args, dict):
        return dict(arguments or {})
    if not ("mode" in args and "reason_code" in args and "confidence" in args):
        return dict(args)

    cleaned: dict[str, Any] = {
        key: args[key] for key in _DECISION_ALLOWED_KEYS if key in args
    }
    if "sub_intents" not in cleaned:
        cleaned["sub_intents"] = []
    if "rationale" not in cleaned:
        cleaned["rationale"] = ""
    decoded_cleaned = _decode_json_like(cleaned)
    if isinstance(decoded_cleaned, dict):
        return decoded_cleaned
    return cleaned


def _normalize_submit_output_arguments(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if str(tool_name or "").strip() != "submit_output":
        return dict(arguments or {})
    args = dict(arguments or {})
    for key in ("decision", "Decision", "output", "result", "payload", "inputs"):
        candidate = _decode_nested_json_object(args.get(key))
        if candidate is not None:
            nested = _decode_nested_json_object(candidate.get("decision"))
            if nested is not None:
                return _sanitize_decision_like_arguments(nested)
            return _sanitize_decision_like_arguments(candidate)
    if len(args) == 1:
        only_value = next(iter(args.values()))
        candidate = _decode_nested_json_object(only_value)
        if candidate is not None:
            nested = _decode_nested_json_object(candidate.get("decision"))
            if nested is not None:
                return _sanitize_decision_like_arguments(nested)
            return _sanitize_decision_like_arguments(candidate)
    return _sanitize_decision_like_arguments(args)


def _coerce_tool_calls(raw_calls: list[Any]) -> list[ToolCall]:
    normalized: list[ToolCall] = []
    for call in raw_calls or []:
        if isinstance(call, ToolCall):
            normalized_args = _normalize_submit_output_arguments(
                str(call.name or "").strip(),
                dict(call.arguments or {}),
            )
            normalized.append(call.model_copy(update={"arguments": normalized_args}))
            continue
        if isinstance(call, dict):
            parsed_call = ToolCall.model_validate(call)
            normalized_args = _normalize_submit_output_arguments(
                str(parsed_call.name or "").strip(),
                dict(parsed_call.arguments or {}),
            )
            normalized.append(
                parsed_call.model_copy(update={"arguments": normalized_args})
            )
            continue
        name = str(getattr(call, "name", "") or "").strip()
        if not name:
            continue
        arguments = _normalize_submit_output_arguments(
            name,
            dict(getattr(call, "arguments", {}) or {}),
        )
        call_id = getattr(call, "id", None)
        normalized.append(
            ToolCall(
                id=str(call_id) if call_id else None,
                name=name,
                arguments=arguments,
                status=LLM_TOOL_CALL_STATUS_PARSED,
            )
        )
    return normalized


def _last_user_text(messages: List[Message]) -> str:
    for msg in reversed(messages):
        if msg.role == "user":
            return str(msg.content or "")
    return ""


def _list_models_from_config(config: Dict[str, Any]) -> List[str]:
    models = config.get("models")
    if isinstance(models, list):
        return [str(item) for item in models if str(item).strip()]
    model = str(config.get("model") or "").strip()
    return [model] if model else []
