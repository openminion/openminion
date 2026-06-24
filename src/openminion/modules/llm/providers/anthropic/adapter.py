import time
from typing import Any, Dict, List

from ...contracts.adapter import (
    ProviderAdapterResult,
    adapter_result_to_llm_response,
)
from ...errors import LLMCtlError
from ...interfaces import LLM_RESPONSE_INTERFACE_VERSION
from ...schemas import LLMRequest, LLMResponse, Message
from ..message_payloads import (
    _as_int,
    _extract_anthropic_thinking_blocks,
    _extract_message_text,
    _coerce_tool_calls,
    _list_models_from_config,
    _messages_anthropic,
    _resolve_api_key,
    _resolve_model,
    _resolve_timeout_seconds,
    _usage_from_anthropic,
    _http_json_post,
)
from ..behavior import resolve_behavior_profile
from ..contract import PROVIDER_INTERFACE_VERSION
from ..tool_calling import (
    detect_raw_envelope,
    detect_raw_tool_markup,
    sanitize_envelope_leak,
    supports_fallback_tool_calling,
)


class AnthropicProvider:
    name = "anthropic"
    contract_version = LLM_RESPONSE_INTERFACE_VERSION
    provider_interface_version = PROVIDER_INTERFACE_VERSION
    default_base_url = "https://api.anthropic.com/v1"

    @staticmethod
    def _prompt_cache_config(config: Dict[str, Any]) -> tuple[bool, bool]:
        raw = config.get("prompt_cache")
        if not isinstance(raw, dict):
            return False, True
        enabled = bool(raw.get("enabled", False))
        cache_system_prompt = bool(raw.get("cache_system_prompt", True))
        return enabled, cache_system_prompt

    def complete(self, request: LLMRequest, config: Dict[str, Any]) -> LLMResponse:
        started = time.perf_counter()
        model = _resolve_model(request, config, "claude-3-5-sonnet-latest")
        api_key = _resolve_api_key(config, self.name, required=True)
        base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
        behavior_profile = resolve_behavior_profile(
            provider=self.name,
            model=model,
            base_url=base_url,
            metadata=request.metadata,
            env=config.get("__env__") if isinstance(config, dict) else None,
        )

        tool_call_strategy = str(config.get("tool_call_strategy", "off"))
        prompt_cache_enabled, cache_system_prompt = self._prompt_cache_config(config)
        system_prompt, messages = _messages_anthropic(
            request,
            include_fallback_instruction=supports_fallback_tool_calling(
                tool_call_strategy
            ),
            enable_prompt_cache=prompt_cache_enabled,
            cache_system_prompt=cache_system_prompt,
            enable_vision_input=bool(config.get("enable_vision_input", False)),
            supports_vision_input=True,
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

        response_payload = _http_json_post(
            url=f"{base_url}/messages",
            payload=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": str(config.get("api_version") or "2023-06-01"),
                "content-type": "application/json",
            },
            timeout_seconds=_resolve_timeout_seconds(config, metadata=request.metadata),
            provider_name=self.name,
            trace_metadata=request.metadata,
            env=config.get("__env__") if isinstance(config, dict) else None,
        )

        text = _extract_message_text(response_payload.get("content"))
        thinking_blocks = _extract_anthropic_thinking_blocks(response_payload)
        tool_calls = []
        if (
            request.tools
            and text
            and (detect_raw_envelope(text) or detect_raw_tool_markup(text))
        ):
            text = sanitize_envelope_leak(text)
        tool_calls = _coerce_tool_calls(tool_calls)

        if not text and not tool_calls:
            # Classify into explicit error codes; CER-04: Tool-call-only is valid
            raise LLMCtlError(
                "EMPTY_PAYLOAD",
                f"{self.name} response did not include text or tool calls",
                details={"retryable": True},
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        assistant_messages = [Message(role="assistant", content=text)] if text else []
        usage = _usage_from_anthropic(response_payload.get("usage"))

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
                finish_reason=str(response_payload.get("stop_reason", "")).strip(),
                provider_raw=response_payload,
                normalization_meta={
                    "adapter": "anthropic",
                    "behavior_profile_id": behavior_profile.profile_id,
                    "tool_call_strategy": tool_call_strategy,
                    "prompt_cache_enabled": prompt_cache_enabled,
                    **(
                        {"envelope_sanitized": True}
                        if request.tools
                        and text.startswith("[system: UNEXECUTABLE_TOOL_ENVELOPE]")
                        else {}
                    ),
                },
            )
        )

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
