"""Anthropic Messages payload builders and response parsers."""

from typing import Any, Mapping

from ...errors import LLMCtlError
from ...schemas import ImageContentPart, LLMRequest, Message, TextContentPart, UsageInfo
from ..message_payloads import (
    _build_thinking_block,
    _dedupe_thinking_blocks,
    _extract_message_text,
    _image_part_bytes,
)
from ..tool_calling import (
    build_fallback_tool_call_instruction,
    is_schema_only_submit_output_tools,
)


def _invalid(message: str) -> None:
    raise LLMCtlError("INVALID_ARGUMENT", message)


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


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
    return _dedupe_thinking_blocks(blocks)


def _anthropic_image_content(
    item: ImageContentPart, *, enable_vision_input: bool, supports_vision_input: bool
) -> dict[str, Any]:
    if not enable_vision_input:
        _invalid("Vision input is disabled for this provider configuration")
    if not supports_vision_input:
        _invalid("This provider does not support image input on the current path")
    if item.source == "url":
        url = str(item.url or "").strip()
        if not url:
            _invalid("Image url source requires a non-empty url")
        return {"type": "image", "source": {"type": "url", "url": url}}
    mime, data = _image_part_bytes(item)
    return {"type": "image", "source": dict(type="base64", media_type=mime, data=data)}


def _anthropic_content(
    message: Message, *, enable_vision_input: bool, supports_vision_input: bool
) -> str | list[dict[str, Any]]:
    if not message.content_parts:
        return message.content.strip()

    parts: list[dict[str, Any]] = []
    for item in message.content_parts:
        if isinstance(item, TextContentPart):
            text = item.text.strip()
            if text:
                parts.append({"type": "text", "text": text})
            continue
        if isinstance(item, ImageContentPart):
            parts.append(
                _anthropic_image_content(
                    item,
                    enable_vision_input=enable_vision_input,
                    supports_vision_input=supports_vision_input,
                )
            )
    if not parts:
        _invalid("Structured content did not produce any provider payload parts")
    return parts


def _anthropic_tool_use_blocks(meta: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_blocks = meta.get("anthropic_tool_uses") or []
    return (
        [dict(raw) for raw in raw_blocks if isinstance(raw, Mapping)]
        if isinstance(raw_blocks, list)
        else []
    )


def _append_anthropic_tool_result(
    chat_messages: list[dict[str, Any]],
    message: Message,
    content: str | list[dict[str, Any]],
) -> bool:
    meta = dict(message.meta)
    tool_use_id = str(meta.get("tool_call_id", "") or "").strip()
    if not tool_use_id:
        return False
    block: dict[str, Any] = dict(
        type="tool_result", tool_use_id=tool_use_id, content=content
    )
    if bool(meta.get("is_error", False)):
        block["is_error"] = True
    if (
        chat_messages
        and chat_messages[-1].get("role") == "user"
        and isinstance(previous_content := chat_messages[-1].get("content"), list)
    ):
        previous_content.append(block)
        return True
    chat_messages.append({"role": "user", "content": [block]})
    return True


def _anthropic_system_text_blocks(
    content: str | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if all(isinstance(item, dict) and item.get("type") == "text" for item in content):
        return [dict(item) for item in content]
    _invalid("Anthropic system prompts must remain text-only")


def _append_anthropic_system_message(
    *,
    content: str | list[dict[str, Any]],
    message: Message,
    enable_prompt_cache: bool,
    cache_system_prompt: bool,
    system_chunks: list[str],
    system_blocks: list[dict[str, Any]],
) -> None:
    text_blocks = _anthropic_system_text_blocks(content)
    if enable_prompt_cache:
        for block in text_blocks:
            if cache_system_prompt and message.cache_control:
                block["cache_control"] = dict(message.cache_control)
            system_blocks.append(block)
        return
    rendered = [
        str(item.get("text", "")).strip()
        for item in text_blocks
        if str(item.get("text", "")).strip()
    ]
    if rendered:
        system_chunks.append("\n\n".join(rendered))


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
        if msg.role == "assistant":
            tool_use_blocks = _anthropic_tool_use_blocks(msg.meta)
            if tool_use_blocks:
                content_blocks = (
                    [{"type": "text", "text": content}]
                    if isinstance(content, str) and content
                    else []
                )
                chat_messages.append(
                    {
                        "role": "assistant",
                        "content": [*content_blocks, *tool_use_blocks],
                    }
                )
                continue
        if msg.role == "tool":
            if _append_anthropic_tool_result(chat_messages, msg, content):
                continue
        if isinstance(content, str) and not content:
            continue

        if msg.role == "system":
            _append_anthropic_system_message(
                content=content,
                message=msg,
                enable_prompt_cache=enable_prompt_cache,
                cache_system_prompt=cache_system_prompt,
                system_chunks=system_chunks,
                system_blocks=system_blocks,
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


def normalize_anthropic_tool_choice(
    raw_value: Any,
    *,
    canonical_to_external: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if isinstance(raw_value, str):
        if (normalized := raw_value.strip().lower()) == "required":
            return {"type": "any"}
        return {"type": normalized if normalized in {"auto", "none"} else "auto"}
    if isinstance(raw_value, Mapping):
        name = str(raw_value.get("name", "") or "").strip()
        if not name and isinstance(
            function_payload := raw_value.get("function"), Mapping
        ):
            name = str(function_payload.get("name", "") or "").strip()
        if name:
            external_name = str((canonical_to_external or {}).get(name, name)).strip()
            return {
                "type": "tool",
                "name": external_name,
            }
    return {"type": "auto"}


def _usage_from_anthropic(payload: Any) -> UsageInfo:
    if not isinstance(payload, dict):
        return UsageInfo()

    input_tokens = _int_or_none(payload.get("input_tokens"))
    output_tokens = _int_or_none(payload.get("output_tokens"))
    total = (
        input_tokens + output_tokens
        if input_tokens is not None and output_tokens is not None
        else None
    )

    return UsageInfo(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total,
        total_source="derived" if total is not None else None,
        cached_tokens=_int_or_none(payload.get("cache_read_input_tokens")),
        cache_creation_tokens=_int_or_none(payload.get("cache_creation_input_tokens")),
    )
