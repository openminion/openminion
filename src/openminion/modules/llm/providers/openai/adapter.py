import json
import time
from typing import Any, Iterator

from ...contracts.adapter import (
    ProviderAdapterResult,
    adapter_result_to_llm_response,
)
from ...constants import (
    LLM_TOOL_CALL_STATUS_PARSED,
    LLM_TOOL_CALL_STRATEGY_HYBRID,
    LLM_TOOL_CHOICE_AUTO,
)
from ...errors import LLMCtlError
from ...interfaces import LLM_RESPONSE_INTERFACE_VERSION
from ...schemas import LLMRequest, LLMResponse, LLMStreamEvent, Message, ResponseError
from ..transport.sse import iter_sse_post_lines
from ..contract import PROVIDER_INTERFACE_VERSION
from ..message_payloads import (
    _extract_message_text,
    _extract_openai_like_thinking_blocks,
    _extract_openai_like_primary_text,
    _coerce_tool_calls,
    _http_json_post,
    _list_models_from_config,
    _messages_openai_like,
    _resolve_api_key,
    _resolve_model,
    _resolve_timeout_seconds,
    _resolve_tool_names,
    _usage_from_openai_like,
)
from ..tool_choice import should_retry_with_auto_tool_choice
from ..tool_calling import (
    build_tool_schema_name_map,
    build_openai_tools_payload,
    normalize_tool_choice,
    remap_provider_tool_call_name,
    resolve_tool_call_source_precedence,
    supports_fallback_tool_calling,
    supports_native_tool_calling,
    ToolCallFallbackSource,
)
from ..behavior import resolve_behavior_profile
from .request_compatibility import (
    requires_auto_tool_choice_compat,
    resolve_openai_request_compat,
)


def _append_retry_system_instruction(
    messages: list[dict[str, Any]],
    instruction: str,
) -> list[dict[str, Any]]:
    note = str(instruction or "").strip()
    if not note:
        return list(messages)
    result = [dict(item) for item in messages]
    result.append({"role": "system", "content": note})
    return result


class OpenAIProvider:
    name = "openai"
    contract_version = LLM_RESPONSE_INTERFACE_VERSION
    provider_interface_version = PROVIDER_INTERFACE_VERSION
    default_base_url = "https://api.openai.com/v1"

    def _resolve_behavior_profile(
        self,
        *,
        model: str,
        base_url: str,
        provider_identity: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None,
        env: Any,
    ):
        return resolve_behavior_profile(
            provider=self.name,
            model=model,
            base_url=base_url,
            provider_identity=provider_identity,
            metadata=metadata,
            env=env,
        )

    def complete(self, request: LLMRequest, config: dict[str, Any]) -> LLMResponse:
        started = time.perf_counter()
        model = _resolve_model(request, config, "gpt-4.1-mini")
        api_key = _resolve_api_key(config, self.name, required=True)
        base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
        behavior_profile = self._resolve_behavior_profile(
            model=model,
            base_url=base_url,
            provider_identity=(
                config.get("provider_identity") if isinstance(config, dict) else None
            ),
            metadata=request.metadata,
            env=config.get("__env__") if isinstance(config, dict) else None,
        )
        request_compat = resolve_openai_request_compat(
            provider_identity=(
                behavior_profile.provider_identity.as_metadata()
                if behavior_profile.provider_identity is not None
                else None
            ),
            request_dialect=behavior_profile.request_dialect,
        )
        tool_call_strategy = str(
            config.get("tool_call_strategy", LLM_TOOL_CALL_STRATEGY_HYBRID)
        )
        tool_name_map = (
            build_tool_schema_name_map(
                request.tools,
                provider_name=self.name,
                model_name=model,
                capability=behavior_profile.tool_schema_capability,
            )
            if request.tools
            else None
        )

        payload: dict[str, Any] = {
            "model": model,
            "messages": _messages_openai_like(
                request,
                include_fallback_instruction=(
                    supports_fallback_tool_calling(tool_call_strategy)
                    and not request_compat.disable_fallback_instruction
                ),
                collapse_system_messages=request_compat.collapse_system_messages,
                extra_system_instruction=request_compat.native_tool_only_instruction,
                tool_name_overrides=(
                    tool_name_map.canonical_to_external if tool_name_map else None
                ),
                enable_vision_input=bool(config.get("enable_vision_input", False)),
                supports_vision_input=True,
            ),
        }

        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        if request.stop:
            payload["stop"] = request.stop

        if request.tools and supports_native_tool_calling(tool_call_strategy):
            payload["tools"] = build_openai_tools_payload(
                request.tools,
                canonical_to_external=(
                    tool_name_map.canonical_to_external if tool_name_map else None
                ),
            )
            normalized_tool_choice = normalize_tool_choice(
                request.tool_choice,
                canonical_to_external=(
                    tool_name_map.canonical_to_external if tool_name_map else None
                ),
            )
            payload["tool_choice"] = normalized_tool_choice

        retry_override_id = ""
        compat_retry_allowed = bool(request.tools) and requires_auto_tool_choice_compat(
            payload.get("tool_choice")
        )
        empty_payload_retry_used = False
        http_kwargs = {
            "url": f"{base_url}/chat/completions",
            "payload": payload,
            "headers": {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            "timeout_seconds": _resolve_timeout_seconds(
                config, metadata=request.metadata
            ),
            "provider_name": self.name,
            "trace_metadata": request.metadata,
            "env": config.get("__env__") if isinstance(config, dict) else None,
        }
        while True:
            try:
                response_payload = _http_json_post(**http_kwargs)
            except LLMCtlError as exc:
                if compat_retry_allowed and should_retry_with_auto_tool_choice(
                    exc, payload.get("tool_choice")
                ):
                    retry_override_id = "tool_choice_retry_to_auto"
                    payload["tool_choice"] = LLM_TOOL_CHOICE_AUTO
                    continue
                raise

            choices = response_payload.get("choices")
            if not isinstance(choices, list) or not choices:
                raise LLMCtlError(
                    "PROVIDER_ERROR", f"{self.name} response missing choices"
                )

            first_choice = choices[0] if isinstance(choices[0], dict) else None
            if not first_choice:
                raise LLMCtlError(
                    "PROVIDER_ERROR", f"{self.name} response has invalid choice payload"
                )

            message_payload = first_choice.get("message")
            if not isinstance(message_payload, dict):
                raise LLMCtlError(
                    "PROVIDER_ERROR", f"{self.name} response missing message payload"
                )

            allowed_tool_names = _resolve_tool_names(request)
            expanded_allowed_tool_names = (
                tool_name_map.expand_allowed_tool_names(allowed_tool_names)
                if tool_name_map is not None
                else allowed_tool_names
            )
            text, raw_text, text_source = _extract_openai_like_primary_text(
                response_payload=response_payload,
                first_choice=first_choice,
                message_payload=message_payload,
            )
            reasoning_text = _extract_message_text(message_payload.get("reasoning"))
            thinking_blocks = _extract_openai_like_thinking_blocks(message_payload)
            tool_call_resolution = resolve_tool_call_source_precedence(
                message_payload=message_payload,
                fallback_sources=[
                    ToolCallFallbackSource(source=text_source, text=raw_text),
                    ToolCallFallbackSource(
                        source="message.reasoning", text=reasoning_text
                    ),
                ],
                provider_name=self.name,
                model_name=str(response_payload.get("model") or model),
                allowed_tool_names=(
                    expanded_allowed_tool_names if request.tools else None
                ),
                fallback_enabled=bool(
                    request.tools
                    and request_compat.enable_structured_tool_envelope_parse
                ),
                parser_plugin_selection=behavior_profile.parser_plugin_selection,
                fallback_parser_policy=behavior_profile.fallback_parser_policy,
                fallback_mode="structured",
            )
            tool_calls = _coerce_tool_calls(
                [
                    {
                        "id": getattr(call, "id", None),
                        "name": remap_provider_tool_call_name(
                            getattr(call, "name", ""),
                            external_to_canonical=(
                                tool_name_map.external_to_canonical
                                if tool_name_map
                                else None
                            ),
                        ),
                        "arguments": dict(getattr(call, "arguments", {}) or {}),
                        "status": LLM_TOOL_CALL_STATUS_PARSED,
                    }
                    for call in tool_call_resolution.calls
                ]
            )
            if tool_calls and tool_call_resolution.selected_source != "native":
                text = ""
                raw_text = ""
                text_source = tool_call_resolution.selected_source

            if text or tool_calls:
                break

            if (
                request_compat.retry_empty_payload_once
                and not empty_payload_retry_used
                and request_compat.empty_payload_retry_instruction
            ):
                empty_payload_retry_used = True
                payload["messages"] = _append_retry_system_instruction(
                    payload.get("messages", []),
                    request_compat.empty_payload_retry_instruction,
                )
                continue

            # Classify into explicit error codes; CER-04: Tool-call-only is valid
            if not first_choice or not message_payload:
                raise LLMCtlError(
                    "MALFORMED_PAYLOAD",
                    f"{self.name} response has malformed or missing payload structure",
                    details={"retryable": False},
                )
            raise LLMCtlError(
                "EMPTY_PAYLOAD",
                f"{self.name} response did not include text or tool calls",
                details={
                    "retryable": True,
                    "finish_reason": str(first_choice.get("finish_reason", "")).strip(),
                    "choice_keys": sorted(str(k) for k in first_choice.keys()),
                    "message_keys": sorted(str(k) for k in message_payload.keys()),
                    "response_keys": sorted(str(k) for k in response_payload.keys()),
                },
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        assistant_messages = [Message(role="assistant", content=text)] if text else []
        usage = _usage_from_openai_like(response_payload.get("usage"))
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
                finish_reason=str(first_choice.get("finish_reason", "")).strip(),
                provider_raw=response_payload,
                normalization_meta={
                    "adapter": "openai",
                    "behavior_profile_id": behavior_profile.profile_id,
                    **(
                        {
                            "provider_identity": (
                                behavior_profile.provider_identity.as_metadata()
                            )
                        }
                        if behavior_profile.provider_identity is not None
                        else {}
                    ),
                    **(
                        {
                            "provider.identity.heuristic_shadow": (
                                behavior_profile.heuristic_provider_identity.as_metadata()
                            )
                        }
                        if behavior_profile.heuristic_provider_identity is not None
                        else {}
                    ),
                    **(
                        {
                            "provider.identity.partial": {
                                "inferred_fields": list(
                                    behavior_profile.provider_identity_inferred_fields
                                )
                            }
                        }
                        if behavior_profile.provider_identity_inferred_fields
                        else {}
                    ),
                    **(
                        {
                            "provider.identity.heuristic_overridden": {
                                "overridden_fields": list(
                                    behavior_profile.provider_identity_overridden_fields
                                )
                            }
                        }
                        if behavior_profile.provider_identity_overridden_fields
                        else {}
                    ),
                    "request_compat_profile": request_compat.profile_id,
                    "request_dialect": behavior_profile.request_dialect,
                    **(
                        {"provider_retry_override": retry_override_id}
                        if retry_override_id
                        else {}
                    ),
                    **(
                        {"empty_payload_retry_used": True}
                        if empty_payload_retry_used
                        else {}
                    ),
                    "tool_call_strategy": tool_call_strategy,
                    "tool_choice_policy": behavior_profile.tool_choice_policy,
                    "text_source": text_source,
                    "tool_schema_capability": (
                        tool_name_map.capability.id if tool_name_map else "identity"
                    ),
                    "tool_schema_external_name_map": (
                        dict(tool_name_map.canonical_to_external)
                        if tool_name_map and tool_name_map.active
                        else {}
                    ),
                    **tool_call_resolution.as_metadata(),
                },
            )
        )

    def stream(
        self, request: LLMRequest, config: dict[str, Any]
    ) -> Iterator[LLMStreamEvent]:
        model = _resolve_model(request, config, "gpt-4.1-mini")
        try:
            api_key = _resolve_api_key(config, self.name, required=True)
        except LLMCtlError as exc:
            yield LLMStreamEvent(
                type="error",
                error=ResponseError(code="AUTH_ERROR", message=str(exc)),
            )
            yield LLMStreamEvent(type="done")
            return

        base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
        behavior_profile = self._resolve_behavior_profile(
            model=model,
            base_url=base_url,
            provider_identity=(
                config.get("provider_identity") if isinstance(config, dict) else None
            ),
            metadata=request.metadata,
            env=config.get("__env__") if isinstance(config, dict) else None,
        )
        request_compat = resolve_openai_request_compat(
            provider_identity=(
                behavior_profile.provider_identity.as_metadata()
                if behavior_profile.provider_identity is not None
                else None
            ),
            request_dialect=behavior_profile.request_dialect,
        )

        payload: dict[str, Any] = {
            "model": model,
            "messages": _messages_openai_like(
                request,
                include_fallback_instruction=False,
                collapse_system_messages=request_compat.collapse_system_messages,
                extra_system_instruction=request_compat.native_tool_only_instruction,
                tool_name_overrides=None,
            ),
            "stream": True,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            try:
                max_tokens = int(request.max_output_tokens)
                if max_tokens > 0:
                    payload["max_tokens"] = max_tokens
            except (TypeError, ValueError):
                pass
        if request.stop:
            payload["stop"] = request.stop

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        timeout_seconds = _resolve_timeout_seconds(config, metadata=request.metadata)

        try:
            for line in iter_sse_post_lines(
                url=f"{base_url}/chat/completions",
                payload=payload,
                headers=headers,
                timeout_seconds=timeout_seconds,
                provider_name=self.name,
                trace_metadata=request.metadata,
            ):
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:") :].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                first = choices[0] if isinstance(choices[0], dict) else {}
                delta = first.get("delta", {}) if isinstance(first, dict) else {}
                content = delta.get("content") if isinstance(delta, dict) else None
                if content:
                    yield LLMStreamEvent(type="delta", delta_text=str(content))
        except LLMCtlError as exc:
            yield LLMStreamEvent(
                type="error",
                error=ResponseError(
                    code=exc.code,
                    message=f"openai stream error: {exc.message}",
                ),
            )
        yield LLMStreamEvent(type="done")

    def list_models(self, config: dict[str, Any]) -> list[str]:
        return _list_models_from_config(config)

    def healthcheck(self, config: dict[str, Any]) -> dict[str, Any]:
        del config
        return {"ok": True, "provider": self.name}


def openai_provider() -> OpenAIProvider:
    return OpenAIProvider()
