import time
from typing import Any

from openminion.modules.llm.reasoning import (
    normalize_optional_reasoning_profile,
)

from ...contracts.adapter import (
    ProviderAdapterResult,
    adapter_result_to_llm_response,
)
from ...constants import LLM_TOOL_CALL_STRATEGY_FALLBACK
from ...errors import LLMCtlError
from ...interfaces import LLM_RESPONSE_INTERFACE_VERSION
from ...schemas import LLMRequest, LLMResponse, Message
from ..behavior import resolve_behavior_profile
from ..contract import PROVIDER_INTERFACE_VERSION
from ..message_payloads import (
    _extract_message_text,
    _extract_ollama_thinking_blocks,
    _sanitize_visible_assistant_text,
    _coerce_tool_calls,
    _http_json_post,
    _list_models_from_config,
    _messages_openai_like,
    _resolve_api_key,
    _resolve_model,
    _resolve_timeout_seconds,
    _resolve_tool_names,
    _usage_from_ollama,
)
from ..tool_calling import (
    build_openai_tools_payload,
    is_schema_only_submit_output_tools,
    resolve_tool_call_source_precedence,
    supports_fallback_tool_calling,
    supports_native_tool_calling,
    ToolCallFallbackSource,
)

_OLLAMA_STRUCTURED_OUTPUT_INSTRUCTION = (
    "Return only a valid JSON object that matches the requested schema. "
    "Do not add prose, markdown, or tool-call wrapper text."
)


def _schema_property_names(schema: dict[str, Any]) -> list[str]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    names: list[str] = []
    for key in properties:
        token = str(key).strip()
        if token:
            names.append(token)
    return names


def _schema_required_names(schema: dict[str, Any]) -> list[str]:
    required = schema.get("required")
    if not isinstance(required, list):
        return []
    names: list[str] = []
    for item in required:
        token = str(item).strip()
        if token:
            names.append(token)
    return names


def _schema_enum_hints(schema: dict[str, Any]) -> list[str]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    hints: list[str] = []
    for raw_name, raw_spec in properties.items():
        name = str(raw_name).strip()
        if not name or not isinstance(raw_spec, dict):
            continue
        enum_values = raw_spec.get("enum")
        if not isinstance(enum_values, list) or not enum_values or len(enum_values) > 6:
            continue
        rendered = [str(item).strip() for item in enum_values if str(item).strip()]
        if rendered:
            hints.append(f"{name}={'|'.join(rendered)}")
    return hints


def _render_schema_type(spec: dict[str, Any]) -> str:
    direct = str(spec.get("type", "")).strip()
    if direct:
        return direct
    any_of = spec.get("anyOf")
    if not isinstance(any_of, list):
        return ""
    rendered: list[str] = []
    for item in any_of:
        if not isinstance(item, dict):
            continue
        token = str(item.get("type", "")).strip()
        if token:
            rendered.append(token)
    deduped: list[str] = []
    for token in rendered:
        if token not in deduped:
            deduped.append(token)
    return "|".join(deduped)


def _schema_type_hints(schema: dict[str, Any]) -> list[str]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    hints: list[str] = []
    for raw_name, raw_spec in properties.items():
        name = str(raw_name).strip()
        if not name or not isinstance(raw_spec, dict):
            continue
        rendered_type = _render_schema_type(raw_spec)
        if rendered_type:
            hints.append(f"{name}={rendered_type}")
    return hints


def _build_ollama_schema_instruction(
    schema_format: dict[str, Any] | str | None,
) -> str:
    if not isinstance(schema_format, dict):
        return _OLLAMA_STRUCTURED_OUTPUT_INSTRUCTION

    title = str(schema_format.get("title", "")).strip()
    allowed_keys = _schema_property_names(schema_format)
    required_keys = _schema_required_names(schema_format)
    enum_hints = _schema_enum_hints(schema_format)
    type_hints = _schema_type_hints(schema_format)

    parts = [_OLLAMA_STRUCTURED_OUTPUT_INSTRUCTION]
    if title:
        parts.append(f"Schema: {title}.")
    if allowed_keys:
        parts.append(f"Allowed keys: {', '.join(allowed_keys[:10])}.")
    if required_keys:
        parts.append(f"Required keys: {', '.join(required_keys[:10])}.")
    if schema_format.get("additionalProperties") is False:
        parts.append("Do not include keys outside the schema.")
    if type_hints:
        parts.append(f"Type hints: {'; '.join(type_hints[:6])}.")
    if enum_hints:
        parts.append(f"Enum fields: {'; '.join(enum_hints[:4])}.")
    if title.endswith("Judgment"):
        parts.append(
            "Return the judgment object only. Do not answer the user directly in prose."
        )
    elif title.endswith("Payload") or title.endswith("Classification"):
        parts.append(
            "Return only the structured object for this step. Do not add extra top-level fields."
        )
    return " ".join(parts)


def _insert_ollama_schema_instruction(
    messages: list[dict[str, str]],
    *,
    schema_format: dict[str, Any] | str | None,
) -> list[dict[str, str]]:
    instruction_message = {
        "role": "system",
        "content": _build_ollama_schema_instruction(schema_format),
    }
    inserted: list[dict[str, str]] = []
    inserted_instruction = False
    for item in messages:
        if not inserted_instruction and item.get("role") != "system":
            inserted.append(instruction_message)
            inserted_instruction = True
        inserted.append(dict(item))
    if not inserted_instruction:
        inserted.append(instruction_message)
    return inserted


def _resolve_ollama_schema_format(request: LLMRequest) -> dict[str, Any] | str | None:
    if not request.tools or not is_schema_only_submit_output_tools(request.tools):
        return None
    schema = request.tools[0].input_schema
    return dict(schema) if isinstance(schema, dict) else "json"


def _resolve_ollama_think(request: LLMRequest) -> bool | None:
    metadata = dict(request.metadata or {})
    raw_profile = (
        metadata.get("thinking_provider_effort")
        or metadata.get("thinking_reasoning_profile")
        or metadata.get("thinking")
    )
    normalized = normalize_optional_reasoning_profile(raw_profile)
    if normalized is None:
        return None
    return normalized == "detailed"


class OllamaProvider:
    name = "ollama"
    contract_version = LLM_RESPONSE_INTERFACE_VERSION
    provider_interface_version = PROVIDER_INTERFACE_VERSION
    default_base_url = "http://127.0.0.1:11434"

    def complete(self, request: LLMRequest, config: dict[str, Any]) -> LLMResponse:
        started = time.perf_counter()
        model = _resolve_model(request, config, "llama3.1")
        api_key = _resolve_api_key(config, self.name, required=False)
        base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
        behavior_profile = resolve_behavior_profile(
            provider=self.name,
            model=model,
            base_url=base_url,
            metadata=request.metadata,
            env=config.get("__env__") if isinstance(config, dict) else None,
        )
        tool_call_strategy = str(
            config.get("tool_call_strategy", LLM_TOOL_CALL_STRATEGY_FALLBACK)
        )
        schema_format = _resolve_ollama_schema_format(request)
        schema_only_submit_output = schema_format is not None
        include_fallback_instruction = (
            supports_fallback_tool_calling(tool_call_strategy)
            and not schema_only_submit_output
        )
        messages = _messages_openai_like(
            request,
            include_fallback_instruction=include_fallback_instruction,
            enable_vision_input=bool(config.get("enable_vision_input", False)),
            supports_vision_input=False,
        )
        if schema_only_submit_output:
            messages = _insert_ollama_schema_instruction(
                messages, schema_format=schema_format
            )

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {},
        }
        if schema_only_submit_output:
            payload["format"] = schema_format
        elif request.tools and supports_native_tool_calling(tool_call_strategy):
            payload["tools"] = build_openai_tools_payload(request.tools)
        think_value = _resolve_ollama_think(request)
        if think_value is not None:
            payload["think"] = think_value

        if request.temperature is not None:
            payload["options"]["temperature"] = request.temperature
        if request.top_p is not None:
            payload["options"]["top_p"] = request.top_p
        if request.max_output_tokens is not None:
            payload["options"]["num_predict"] = request.max_output_tokens
        if request.stop:
            payload["options"]["stop"] = request.stop

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        response_payload = _http_json_post(
            url=f"{base_url}/api/chat",
            payload=payload,
            headers=headers,
            timeout_seconds=_resolve_timeout_seconds(config, metadata=request.metadata),
            provider_name=self.name,
            trace_metadata=request.metadata,
            env=config.get("__env__") if isinstance(config, dict) else None,
        )

        message_payload = response_payload.get("message")
        raw_text = ""
        if isinstance(message_payload, dict):
            raw_text = _extract_message_text(message_payload.get("content"))
        if not raw_text:
            fallback_text = response_payload.get("response")
            if isinstance(fallback_text, str):
                raw_text = fallback_text.strip()
        text = _sanitize_visible_assistant_text(raw_text)

        allowed_tool_names = _resolve_tool_names(request)
        thinking_text = ""
        if isinstance(message_payload, dict):
            thinking_text = _extract_message_text(message_payload.get("thinking"))
        thinking_blocks = _extract_ollama_thinking_blocks(message_payload)
        tool_call_resolution = resolve_tool_call_source_precedence(
            message_payload=message_payload,
            fallback_sources=[
                ToolCallFallbackSource(source="message.content", text=raw_text),
                ToolCallFallbackSource(source="message.thinking", text=thinking_text),
            ],
            provider_name=self.name,
            model_name=str(response_payload.get("model") or model),
            allowed_tool_names=allowed_tool_names if request.tools else None,
            fallback_enabled=bool(request.tools and schema_only_submit_output),
            parser_plugin_selection=behavior_profile.parser_plugin_selection,
            fallback_parser_policy=behavior_profile.fallback_parser_policy,
        )
        tool_calls = _coerce_tool_calls(tool_call_resolution.calls)

        if not text and not tool_calls:
            raise LLMCtlError(
                "EMPTY_PAYLOAD",
                f"{self.name} response did not include text or tool calls. "
                "If using Ollama, ensure the model is pulled: 'ollama pull {model}'",
                details={"retryable": True, "model": model, "base_url": base_url},
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        assistant_messages = [Message(role="assistant", content=text)] if text else []
        usage = _usage_from_ollama(response_payload)
        return adapter_result_to_llm_response(
            ProviderAdapterResult(
                provider=self.name,
                model=str(response_payload.get("model") or model),
                output_text=text,
                assistant_messages=assistant_messages,
                tool_calls=tool_calls,
                thinking=thinking_blocks,
                usage=usage,
                latency_ms=elapsed_ms,
                finish_reason=str(response_payload.get("done_reason", "")).strip(),
                provider_raw=response_payload,
                normalization_meta={
                    "adapter": "ollama",
                    "behavior_profile_id": behavior_profile.profile_id,
                    "schema_only_submit_output": schema_only_submit_output,
                    "tool_call_strategy": tool_call_strategy,
                    **tool_call_resolution.as_metadata(),
                },
            )
        )

    def list_models(self, config: dict[str, Any]) -> list[str]:
        return _list_models_from_config(config)

    def healthcheck(self, config: dict[str, Any]) -> dict[str, Any]:
        del config
        return {"ok": True, "provider": self.name}


def ollama_provider() -> OllamaProvider:
    return OllamaProvider()
