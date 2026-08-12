import json
import time
from typing import Any, Dict, Iterator, List

from ...contracts.adapter import (
    ProviderAdapterResult,
    adapter_result_to_llm_response,
)
from ...errors import LLMCtlError
from ...interfaces import LLM_RESPONSE_INTERFACE_VERSION
from ...schemas import (
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    Message,
    ResponseError,
    ToolCall,
    ToolCallStatus,
)
from ...streaming import response_stream_events, stream_error_event
from .payloads import (
    _extract_anthropic_thinking_blocks,
    _messages_anthropic,
    _usage_from_anthropic,
    normalize_anthropic_tool_choice,
)
from ..message_payloads import (
    _as_int,
    _extract_message_text,
    _list_models_from_config,
    _resolve_api_key,
    _resolve_model,
    _resolve_timeout_seconds,
    _http_json_post,
)
from ..transport.sse import iter_sse_post_lines
from ..behavior import resolve_behavior_profile
from ..contract import PROVIDER_INTERFACE_VERSION
from ..tool_calling import (
    build_openai_tools_payload,
    build_tool_schema_name_map,
    detect_raw_envelope,
    detect_raw_tool_markup,
    remap_provider_tool_call_name,
    supports_native_tool_calling,
    sanitize_envelope_leak,
    supports_fallback_tool_calling,
)


def _headers(
    config: Dict[str, Any], api_key: str, *, stream: bool = False
) -> dict[str, str]:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": str(config.get("api_version") or "2023-06-01"),
        "content-type": "application/json",
    }
    if stream:
        headers["accept"] = "text/event-stream"
    return headers


class AnthropicProvider:
    name = "anthropic"
    contract_version = LLM_RESPONSE_INTERFACE_VERSION
    provider_interface_version = PROVIDER_INTERFACE_VERSION
    default_base_url = "https://api.anthropic.com/v1"

    @staticmethod
    def _prompt_cache_config(config: Dict[str, Any]) -> tuple[bool, bool]:
        raw = config.get("prompt_cache")
        if isinstance(raw, dict):
            return bool(raw.get("enabled", False)), bool(
                raw.get("cache_system_prompt", True)
            )
        return False, True

    def _response_from_payload(
        self,
        *,
        request: LLMRequest,
        model: str,
        response_payload: Dict[str, Any],
        started: float,
        behavior_profile_id: str,
        tool_call_strategy: str,
        prompt_cache_enabled: bool,
        external_to_canonical: dict[str, str] | None = None,
    ) -> LLMResponse:
        text = _extract_message_text(response_payload.get("content"))
        thinking_blocks = _extract_anthropic_thinking_blocks(response_payload)
        tool_calls = self._tool_calls_from_payload(
            response_payload,
            external_to_canonical=external_to_canonical,
        )
        if (
            request.tools
            and text
            and (detect_raw_envelope(text) or detect_raw_tool_markup(text))
        ):
            text = sanitize_envelope_leak(text)
        if not text and not tool_calls:
            raise LLMCtlError(
                "EMPTY_PAYLOAD",
                f"{self.name} response did not include text or tool calls",
                details={"retryable": True},
            )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        raw_tool_uses = [
            dict(item)
            for item in response_payload.get("content", [])
            if isinstance(item, dict) and item.get("type") == "tool_use"
        ]
        meta = {"anthropic_tool_uses": raw_tool_uses} if raw_tool_uses else {}
        assistant_messages = [Message(role="assistant", content=text, meta=meta)]
        normalization_meta = {
            "adapter": "anthropic",
            "behavior_profile_id": behavior_profile_id,
            "tool_call_strategy": tool_call_strategy,
            "prompt_cache_enabled": prompt_cache_enabled,
        }
        if request.tools and text.startswith("[system: UNEXECUTABLE_TOOL_ENVELOPE]"):
            normalization_meta["envelope_sanitized"] = True
        return adapter_result_to_llm_response(
            ProviderAdapterResult(
                provider=self.name,
                model=str(response_payload.get("model") or model),
                output_text=text,
                assistant_messages=assistant_messages,
                tool_calls=tool_calls,
                thinking=thinking_blocks,
                usage=_usage_from_anthropic(response_payload.get("usage")),
                latency_ms=elapsed_ms,
                finish_reason=str(response_payload.get("stop_reason", "")).strip(),
                provider_raw=response_payload,
                normalization_meta=normalization_meta,
            )
        )

    @staticmethod
    def _tool_calls_from_payload(
        response_payload: Dict[str, Any],
        *,
        external_to_canonical: dict[str, str] | None,
    ) -> list[ToolCall]:
        raw_content = response_payload.get("content")
        if not isinstance(raw_content, list):
            return []
        calls: list[ToolCall] = []
        for item in raw_content:
            if not isinstance(item, dict) or item.get("type") != "tool_use":
                continue
            raw_input = item.get("input", {})
            is_malformed = not isinstance(raw_input, dict)
            status: ToolCallStatus = "error" if is_malformed else "parsed"
            calls.append(
                ToolCall(
                    id=str(item.get("id") or "").strip() or None,
                    name=remap_provider_tool_call_name(
                        str(item.get("name") or "").strip(),
                        external_to_canonical=external_to_canonical,
                    ),
                    arguments=raw_input if isinstance(raw_input, dict) else {},
                    status=status,
                    error="Anthropic tool_use input must be an object"
                    if is_malformed
                    else None,
                )
            )
        return calls

    @staticmethod
    def _add_tools_to_payload(
        payload: Dict[str, Any],
        *,
        request: LLMRequest,
        tool_call_strategy: str,
        canonical_to_external: dict[str, str] | None,
    ) -> None:
        if not request.tools or not supports_native_tool_calling(tool_call_strategy):
            return
        payload["tools"] = [
            dict(
                name=function_payload.get("name", ""),
                description=function_payload.get("description", ""),
                input_schema=function_payload.get("parameters", {}),
            )
            for item in build_openai_tools_payload(
                request.tools,
                canonical_to_external=canonical_to_external,
            )
            if isinstance(function_payload := item.get("function"), dict)
        ]
        payload["tool_choice"] = normalize_anthropic_tool_choice(
            request.tool_choice,
            canonical_to_external=canonical_to_external,
        )

    @staticmethod
    def _reject_unsupported_sampling(request: LLMRequest, model: str) -> None:
        sampling_overrides = [
            name
            for name, value in (
                ("temperature", request.temperature),
                ("top_p", request.top_p),
            )
            if value is not None
        ]
        if model == "claude-sonnet-5" and sampling_overrides:
            raise LLMCtlError(
                "INVALID_ARGUMENT",
                "Claude Sonnet 5 does not accept sampling overrides; omit "
                + ", ".join(sampling_overrides),
                details=dict(
                    model=model, parameters=sampling_overrides, retryable=False
                ),
            )

    def complete(self, request: LLMRequest, config: Dict[str, Any]) -> LLMResponse:
        started = time.perf_counter()
        model = _resolve_model(request, config, "claude-sonnet-5")
        self._reject_unsupported_sampling(request, model)
        api_key = _resolve_api_key(config, self.name, required=True)
        base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
        behavior_profile = resolve_behavior_profile(
            provider=self.name,
            model=model,
            base_url=base_url,
            metadata=request.metadata,
            env=config.get("__env__"),
        )

        tool_call_strategy = str(config.get("tool_call_strategy", "off"))
        prompt_cache_enabled, cache_system_prompt = self._prompt_cache_config(config)
        tool_name_map = (
            build_tool_schema_name_map(
                request.tools,
                provider_name=self.name,
                model_name=model,
            )
            if request.tools
            else None
        )
        canonical_to_external = (
            tool_name_map.canonical_to_external if tool_name_map else None
        )
        external_to_canonical = (
            tool_name_map.external_to_canonical if tool_name_map else None
        )
        system_prompt, messages = _messages_anthropic(
            request,
            include_fallback_instruction=supports_fallback_tool_calling(
                tool_call_strategy
            ),
            enable_prompt_cache=prompt_cache_enabled,
            cache_system_prompt=cache_system_prompt,
            enable_vision_input=bool(config.get("enable_vision_input", False)),
            supports_vision_input=True,
            tool_name_overrides=canonical_to_external,
        )

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": _as_int(
                config.get("max_tokens"), request.max_output_tokens or 1024
            ),
        }
        if system_prompt:
            payload["system"] = system_prompt
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop:
            payload["stop_sequences"] = request.stop
        self._add_tools_to_payload(
            payload,
            request=request,
            tool_call_strategy=tool_call_strategy,
            canonical_to_external=canonical_to_external,
        )

        response_payload = _http_json_post(
            url=f"{base_url}/messages",
            payload=payload,
            headers=_headers(config, api_key),
            timeout_seconds=_resolve_timeout_seconds(config, metadata=request.metadata),
            provider_name=self.name,
            trace_metadata=request.metadata,
            env=config.get("__env__"),
        )

        return self._response_from_payload(
            request=request,
            model=model,
            response_payload=response_payload,
            started=started,
            behavior_profile_id=behavior_profile.profile_id,
            tool_call_strategy=tool_call_strategy,
            prompt_cache_enabled=prompt_cache_enabled,
            external_to_canonical=external_to_canonical,
        )

    def stream(
        self, request: LLMRequest, config: Dict[str, Any]
    ) -> Iterator[LLMStreamEvent]:
        if request.tools:
            yield from self._complete_as_stream(request, config)
            return

        try:
            model = _resolve_model(request, config, "claude-sonnet-5")
            api_key = _resolve_api_key(config, self.name, required=True)
            base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
            system_prompt, messages = _messages_anthropic(request, False)
            payload: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": _as_int(
                    config.get("max_tokens"), request.max_output_tokens or 1024
                ),
                "stream": True,
            }
            if system_prompt:
                payload["system"] = system_prompt
            if request.stop:
                payload["stop_sequences"] = request.stop
            stream_event_type = "message"
            for line in iter_sse_post_lines(
                url=f"{base_url}/messages",
                payload=payload,
                headers=_headers(config, api_key, stream=True),
                timeout_seconds=_resolve_timeout_seconds(
                    config, metadata=request.metadata
                ),
                provider_name=self.name,
                trace_metadata=request.metadata,
                env=config.get("__env__"),
            ):
                if line.startswith("event:"):
                    stream_event_type = line[len("event:") :].strip() or "message"
                    continue
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:") :].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError as exc:
                    yield LLMStreamEvent(
                        type="error",
                        error=ResponseError(
                            code="PROVIDER_ERROR",
                            message="anthropic stream malformed event payload",
                            details=dict(
                                stream_event_type=stream_event_type, error=str(exc)
                            ),
                        ),
                    )
                    break
                if stream_event_type == "content_block_delta":
                    delta = chunk.get("delta") if isinstance(chunk, dict) else {}
                    text = delta.get("text") if isinstance(delta, dict) else None
                    if text:
                        yield LLMStreamEvent(type="delta", delta_text=str(text))
                elif stream_event_type == "message_stop":
                    break
        except LLMCtlError as exc:
            yield stream_error_event(exc, message_prefix="anthropic stream error: ")
        yield LLMStreamEvent(type="done")

    def _complete_as_stream(
        self,
        request: LLMRequest,
        config: Dict[str, Any],
    ) -> Iterator[LLMStreamEvent]:
        try:
            response = self.complete(request, config)
        except LLMCtlError as exc:
            yield stream_error_event(exc)
            yield LLMStreamEvent(type="done")
            return
        yield from response_stream_events(response)
        yield LLMStreamEvent(type="done")

    def list_models(self, config: Dict[str, Any]) -> List[str]:
        return _list_models_from_config(config)

    def healthcheck(self, config: Dict[str, Any]) -> Dict[str, Any]:
        del config
        return {"ok": True, "provider": self.name}


class ClaudeProvider(AnthropicProvider):
    name = "claude"


def anthropic_provider() -> AnthropicProvider:
    return AnthropicProvider()


def claude_provider() -> ClaudeProvider:
    return ClaudeProvider()
