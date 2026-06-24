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
)
from ...errors import LLMCtlError
from ...schemas import LLMRequest, LLMResponse, LLMStreamEvent, Message, ResponseError
from ..message_payloads import (
    _extract_message_text,
    _extract_openai_like_thinking_blocks,
    _extract_openai_like_primary_text,
    _coerce_tool_calls,
    _http_json_get,
    _http_json_post,
    _list_models_from_config,
    _messages_openai_like,
    _resolve_api_key,
    _resolve_model,
    _resolve_timeout_seconds,
    _resolve_tool_names,
    _usage_from_openai_like,
)
from ..openai.adapter import OpenAIProvider
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
from ..transport.sse import iter_sse_post_lines


class OpenRouterProvider(OpenAIProvider):
    name = "openrouter"
    default_base_url = "https://openrouter.ai/api/v1"

    def complete(self, request: LLMRequest, config: dict[str, Any]) -> LLMResponse:
        started = time.perf_counter()
        model = _resolve_model(request, config, "openai/gpt-4.1-mini")
        api_key = _resolve_api_key(config, self.name, required=True)
        base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
        behavior_profile = self._resolve_behavior_profile(
            model=model,
            base_url=base_url,
            metadata=request.metadata,
            env=config.get("__env__") if isinstance(config, dict) else None,
        )
        tool_call_strategy = str(
            config.get("tool_call_strategy", LLM_TOOL_CALL_STRATEGY_HYBRID)
        )
        collapse_system_messages = self._collapse_system_messages_for_model(model)
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
                include_fallback_instruction=supports_fallback_tool_calling(
                    tool_call_strategy
                ),
                collapse_system_messages=collapse_system_messages,
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
        resolved_max_tokens = self._resolve_max_tokens(request=request, config=config)
        if resolved_max_tokens is not None:
            payload["max_tokens"] = resolved_max_tokens
        if request.stop:
            payload["stop"] = request.stop

        if request.tools and supports_native_tool_calling(tool_call_strategy):
            payload["tools"] = build_openai_tools_payload(
                request.tools,
                canonical_to_external=(
                    tool_name_map.canonical_to_external if tool_name_map else None
                ),
            )
            payload["tool_choice"] = normalize_tool_choice(
                request.tool_choice,
                canonical_to_external=(
                    tool_name_map.canonical_to_external if tool_name_map else None
                ),
            )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        app_url = str(config.get("app_url") or "").strip()
        app_name = str(config.get("app_name") or "").strip()
        if app_url:
            headers["HTTP-Referer"] = app_url
        if app_name:
            headers["X-Title"] = app_name

        response_payload = _http_json_post(
            url=f"{base_url}/chat/completions",
            payload=payload,
            headers=headers,
            timeout_seconds=_resolve_timeout_seconds(config, metadata=request.metadata),
            provider_name=self.name,
            trace_metadata=request.metadata,
            env=config.get("__env__") if isinstance(config, dict) else None,
        )

        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMCtlError("PROVIDER_ERROR", f"{self.name} response missing choices")

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
                ToolCallFallbackSource(source="message.reasoning", text=reasoning_text),
            ],
            provider_name=self.name,
            model_name=str(response_payload.get("model") or model),
            allowed_tool_names=expanded_allowed_tool_names if request.tools else None,
            fallback_enabled=bool(
                request.tools
                and reasoning_text
                and supports_fallback_tool_calling(tool_call_strategy)
            ),
            parser_plugin_selection=behavior_profile.parser_plugin_selection,
            fallback_parser_policy=behavior_profile.fallback_parser_policy,
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

        if not text and not tool_calls:
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

        cost_usd: float | None = None
        usage_dict = response_payload.get("usage")
        if isinstance(usage_dict, dict):
            raw_cost = usage_dict.get("cost")
            if raw_cost is not None:
                try:
                    cost_usd = float(raw_cost)
                except (TypeError, ValueError):
                    pass

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
                cost_usd=cost_usd,
                finish_reason=str(first_choice.get("finish_reason", "")).strip(),
                provider_raw=response_payload,
                normalization_meta={
                    "adapter": "openrouter",
                    "behavior_profile_id": behavior_profile.profile_id,
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
        """ORIE-01: Stream SSE deltas from OpenRouter chat/completions endpoint."""
        model = _resolve_model(request, config, "openai/gpt-4.1-mini")
        try:
            api_key = _resolve_api_key(config, self.name, required=True)
        except LLMCtlError as exc:
            yield LLMStreamEvent(
                type="error", error=ResponseError(code="AUTH_ERROR", message=str(exc))
            )
            return

        base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")

        payload: dict[str, Any] = {
            "model": model,
            "messages": _messages_openai_like(
                request,
                include_fallback_instruction=False,
                collapse_system_messages=self._collapse_system_messages_for_model(
                    model
                ),
            ),
            "stream": True,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        resolved_max_tokens = self._resolve_max_tokens(request=request, config=config)
        if resolved_max_tokens is not None:
            payload["max_tokens"] = resolved_max_tokens
        if request.stop:
            payload["stop"] = request.stop

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        app_url = str(config.get("app_url") or "").strip()
        app_name = str(config.get("app_name") or "").strip()
        if app_url:
            headers["HTTP-Referer"] = app_url
        if app_name:
            headers["X-Title"] = app_name

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
                delta = (
                    choices[0].get("delta", {}) if isinstance(choices[0], dict) else {}
                )
                content = delta.get("content")
                if content:
                    yield LLMStreamEvent(type="delta", delta_text=str(content))
        except LLMCtlError as exc:
            yield LLMStreamEvent(
                type="error",
                error=ResponseError(
                    code=exc.code, message=f"openrouter stream error: {exc.message}"
                ),
            )
            return
        yield LLMStreamEvent(type="done")

    @staticmethod
    def _collapse_system_messages_for_model(model_name: str) -> bool:
        return "qwen" in str(model_name or "").strip().lower()

    @staticmethod
    def _resolve_max_tokens(request: LLMRequest, config: dict[str, Any]) -> int | None:
        if request.max_output_tokens is not None:
            try:
                value = int(request.max_output_tokens)
            except (TypeError, ValueError):
                return None
            return value if value > 0 else None
        raw = config.get("max_tokens")
        try:
            value = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def list_models(self, config: dict[str, Any]) -> list[str]:
        from_cfg = _list_models_from_config(config)
        if from_cfg:
            return from_cfg
        api_key = str(config.get("api_key") or "").strip()
        if not api_key:
            return []
        base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
        try:
            payload = _http_json_get(
                url=f"{base_url}/models",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout_seconds=_resolve_timeout_seconds(config),
                provider_name=self.name,
                env=config.get("__env__") if isinstance(config, dict) else None,
            )
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                return [
                    str(m.get("id", ""))
                    for m in data
                    if isinstance(m, dict) and m.get("id")
                ]
        except Exception:
            pass
        return []


def openrouter_provider() -> OpenRouterProvider:
    return OpenRouterProvider()
