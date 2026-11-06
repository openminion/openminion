from __future__ import annotations

from unittest.mock import patch

from openminion.modules.brain.runtime.reasoning import (
    ThinkingRequest,
    ThinkingResolutionInput,
    resolve_thinking,
)
from openminion.modules.llm.providers.adapters import (
    OllamaProvider,
    OpenAIProvider,
)
from openminion.modules.llm.schemas import LLMRequest, Message

# Payload keys that would indicate a provider effort was transmitted to the
# upstream provider. None of the direct OpenAI/Anthropic/Groq/Cerebras/
# OpenRouter adapters emit these; only ollama maps the effort to `think`.
_EFFORT_PAYLOAD_KEYS = (
    "thinking",
    "reasoning",
    "reasoning_effort",
    "think",
    "thinking_provider_effort",
)

_OPENAI_RESPONSE = {
    "model": "gpt-4.1-mini",
    "choices": [
        {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}

_OLLAMA_RESPONSE = {
    "model": "kimi-k2.5",
    "message": {"role": "assistant", "content": "hello"},
    "prompt_eval_count": 5,
    "eval_count": 2,
}


def _resolve(*, provider: str, route: str | None, profile: str = "detailed"):
    return resolve_thinking(
        request=ThinkingRequest(
            purpose="unit_test",
            requested_profile=profile,
            provider=provider,
            model="example-model",
            route=route,
        ),
        layers=ThinkingResolutionInput(code_default_profile="minimal"),
    )


def test_direct_openai_route_degrades_provider_effort() -> None:
    resolved = _resolve(provider="openai", route="direct")

    assert resolved.reasoning_profile == "detailed"
    assert resolved.supported is False
    assert resolved.provider_effort is None
    assert resolved.degraded_reason == "provider_effort_unsupported"


def test_bridge_openai_route_keeps_provider_effort_supported() -> None:
    resolved = _resolve(provider="openai", route="bridge")

    assert resolved.reasoning_profile == "detailed"
    assert resolved.supported is True
    assert resolved.provider_effort == "detailed"
    assert resolved.degraded_reason is None


def test_ollama_route_transmits_provider_effort() -> None:
    resolved = _resolve(provider="ollama", route="ollama")

    assert resolved.reasoning_profile == "detailed"
    assert resolved.supported is True
    assert resolved.provider_effort == "detailed"


def test_disabled_effort_is_supported_regardless_of_route() -> None:
    resolved = _resolve(provider="openai", route="direct", profile="off")

    assert resolved.reasoning_profile == "off"
    assert resolved.provider_effort is None
    assert resolved.supported is True
    assert resolved.degraded_reason is None


def test_no_route_falls_back_to_provider_capability() -> None:
    # The base-config/capability path resolves effort without a known execution
    # route; it must keep the original provider-allowlist behavior so reasoning
    # parity with base config holds (supported providers stay supported).
    resolved = _resolve(provider="openai", route=None)

    assert resolved.reasoning_profile == "detailed"
    assert resolved.supported is True
    assert resolved.provider_effort == "detailed"
    assert resolved.degraded_reason is None


def test_unknown_provider_on_bridge_still_degrades() -> None:
    resolved = _resolve(provider="unsupported-provider", route="bridge")

    assert resolved.reasoning_profile == "detailed"
    assert resolved.supported is False
    assert resolved.provider_effort is None
    assert resolved.degraded_reason == "provider_effort_unsupported"


def test_echo_is_not_claimed_as_transmitted_provider_effort() -> None:
    resolved = _resolve(provider="echo", route="bridge")

    assert resolved.reasoning_profile == "detailed"
    assert resolved.supported is False
    assert resolved.provider_effort is None
    assert resolved.degraded_reason == "provider_effort_unsupported"


def test_direct_openai_adapter_does_not_transmit_effort() -> None:
    provider = OpenAIProvider()
    captured: dict[str, object] = {}

    def fake_post(**kwargs):
        captured.update(kwargs)
        return dict(_OPENAI_RESPONSE)

    request = LLMRequest(
        provider="openai",
        model="gpt-4.1-mini",
        messages=[Message(role="user", content="hello")],
        metadata={"thinking_provider_effort": "detailed", "thinking": "detailed"},
    )

    with patch(
        "openminion.modules.llm.providers.openai.adapter._http_json_post",
        side_effect=fake_post,
    ):
        provider.complete(
            request,
            {"api_key": "test-key", "base_url": "https://api.openai.com/v1"},
        )

    payload = captured["payload"]
    assert isinstance(payload, dict)
    for key in _EFFORT_PAYLOAD_KEYS:
        assert key not in payload


def test_ollama_adapter_transmits_effort_as_think_flag() -> None:
    provider = OllamaProvider()
    captured: dict[str, object] = {}

    def fake_post(**kwargs):
        captured.update(kwargs)
        return dict(_OLLAMA_RESPONSE)

    request = LLMRequest(
        provider="ollama",
        model="kimi-k2.5",
        messages=[Message(role="user", content="hello")],
        metadata={
            "thinking_provider_effort": "detailed",
            "thinking_reasoning_profile": "detailed",
        },
    )

    with patch(
        "openminion.modules.llm.providers.ollama.adapter._http_json_post",
        side_effect=fake_post,
    ):
        provider.complete(
            request,
            {"base_url": "http://127.0.0.1:11434", "tool_call_strategy": "hybrid"},
        )

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["think"] is True
