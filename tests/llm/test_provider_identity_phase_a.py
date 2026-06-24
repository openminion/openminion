from __future__ import annotations

from typing import Any
from unittest.mock import patch

from openminion.modules.llm.providers.openai.adapter import OpenAIProvider
from openminion.modules.llm.schemas import LLMRequest


def _basic_response_payload() -> dict[str, Any]:
    return {
        "model": "MiniMax-M2.7",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": "ok",
                },
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }


def _request() -> LLMRequest:
    return LLMRequest.model_validate(
        {
            "model": "MiniMax-M2.7",
            "messages": [{"role": "user", "content": "hi"}],
        }
    )


def test_phase_a_emits_partial_identity_and_heuristic_shadow_without_behavior_change() -> (
    None
):
    provider = OpenAIProvider()

    with patch(
        "openminion.modules.llm.providers.openai.adapter._http_json_post",
        return_value=_basic_response_payload(),
    ):
        response = provider.complete(
            _request(),
            {
                "api_key": "test-key",
                "base_url": "https://api.minimax.io/v1",
                "provider_identity": {"service_vendor": "minimax"},
            },
        )

    normalization = response.telemetry["normalization"]
    assert normalization["request_compat_profile"] == "minimax_openai_compat"
    assert normalization["provider_identity"] == {
        "transport_adapter": "openai_chat",
        "wire_protocol_family": "openai_chat_completions",
        "service_vendor": "minimax",
        "model_family": "minimax",
    }
    assert normalization["provider.identity.heuristic_shadow"] == {
        "transport_adapter": "openai_chat",
        "wire_protocol_family": "openai_chat_completions",
        "service_vendor": "minimax",
        "model_family": "minimax",
    }
    assert normalization["provider.identity.partial"] == {
        "inferred_fields": [
            "transport_adapter",
            "wire_protocol_family",
            "model_family",
        ]
    }
    assert "provider.identity.heuristic_overridden" not in normalization


def test_phase_a_explicit_identity_override_changes_request_compat_lane() -> None:
    provider = OpenAIProvider()

    with patch(
        "openminion.modules.llm.providers.openai.adapter._http_json_post",
        return_value=_basic_response_payload(),
    ):
        response = provider.complete(
            _request(),
            {
                "api_key": "test-key",
                "base_url": "https://api.minimax.io/v1",
                "provider_identity": {
                    "transport_adapter": "openai_chat",
                    "wire_protocol_family": "openai_chat_completions",
                    "service_vendor": "custom-proxy",
                    "model_family": "minimax",
                },
            },
        )

    normalization = response.telemetry["normalization"]
    assert normalization["request_compat_profile"] == "openai_default"
    assert normalization["provider_identity"]["service_vendor"] == "custom-proxy"
    assert normalization["provider.identity.heuristic_shadow"]["service_vendor"] == (
        "minimax"
    )
    assert normalization["provider.identity.heuristic_overridden"] == {
        "overridden_fields": ["service_vendor"]
    }
    assert "provider.identity.partial" not in normalization
