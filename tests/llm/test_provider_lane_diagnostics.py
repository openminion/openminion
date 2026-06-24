from __future__ import annotations

from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.providers.diagnostics import (
    ProviderLaneAccessState,
    classify_provider_lane_access,
    provider_endpoint_lane,
    provider_lane_descriptor_from_config,
)


def test_provider_endpoint_lane_resolves_current_dashscope_variants() -> None:
    assert (
        provider_endpoint_lane("https://coding-intl.dashscope.aliyuncs.com/v1")
        == "dashscope_coding_intl"
    )
    assert (
        provider_endpoint_lane("https://dashscope.aliyuncs.com/compatible-mode/v1")
        == "dashscope_compatible_mode"
    )


def test_provider_lane_descriptor_from_config_uses_provider_model_and_lane() -> None:
    descriptor = provider_lane_descriptor_from_config(
        provider_name="alibaba",
        provider_config={
            "model": "MiniMax-M2.5",
            "base_url": "https://coding-intl.dashscope.aliyuncs.com/v1",
        },
    )
    assert descriptor.provider_name == "alibaba"
    assert descriptor.model_name == "MiniMax-M2.5"
    assert descriptor.endpoint_lane == "dashscope_coding_intl"


def test_auth_error_is_classified_as_access_blocked() -> None:
    result = classify_provider_lane_access(
        provider_name="alibaba",
        model_name="glm-5",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        error=LLMCtlError(
            "AUTH_ERROR",
            "openai auth failed: model not found or not entitled on this lane",
        ),
    )
    assert result.access_state == ProviderLaneAccessState.ACCESS_BLOCKED
    assert result.reason_code in {"auth_error", "entitlement_or_permission"}


def test_embedded_runtime_auth_error_text_is_classified_as_access_blocked() -> None:
    result = classify_provider_lane_access(
        provider_name="alibaba",
        model_name="glm-5",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        response_text=(
            "State machine error: AUTH_ERROR: openai auth failed: "
            '{"error":{"message":"Incorrect API key provided","code":"invalid_api_key"}}'
        ),
    )
    assert result.access_state == ProviderLaneAccessState.ACCESS_BLOCKED
    assert result.reason_code in {"auth_error", "entitlement_or_permission"}


def test_quota_failure_is_classified_before_runtime_support() -> None:
    result = classify_provider_lane_access(
        provider_name="openrouter",
        model_name="openai/gpt-5.4",
        base_url="https://openrouter.ai/api/v1",
        error=LLMCtlError(
            "PROVIDER_ERROR",
            (
                "openrouter request failed with HTTP 402: "
                '{"error":{"message":"This request requires more credits","code":402}}'
            ),
        ),
    )
    assert result.access_state == ProviderLaneAccessState.QUOTA_BLOCKED
    assert result.reason_code == "quota_or_rate_limit"


def test_transport_failure_is_classified_before_runtime_support() -> None:
    result = classify_provider_lane_access(
        provider_name="openrouter",
        model_name="openai/gpt-5.4",
        base_url="https://openrouter.ai/api/v1",
        error=LLMCtlError(
            "PROVIDER_ERROR",
            "openrouter request failed: [Errno 49] Can't assign requested address",
        ),
    )
    assert result.access_state == ProviderLaneAccessState.TRANSPORT_BLOCKED
    assert result.reason_code == "transport_error"


def test_response_envelope_failure_keeps_access_ready_but_not_runtime_certified() -> (
    None
):
    result = classify_provider_lane_access(
        provider_name="openrouter",
        model_name="minimax/minimax-m2.7",
        base_url="https://openrouter.ai/api/v1",
        error=LLMCtlError(
            "PROVIDER_ERROR",
            "openrouter response missing choices",
        ),
    )
    assert result.access_state == ProviderLaneAccessState.ACCESS_READY
    assert result.reason_code == "response_envelope_error"


def test_no_error_classifies_as_access_ready() -> None:
    result = classify_provider_lane_access(
        provider_name="alibaba",
        model_name="kimi-k2.5",
        base_url="https://coding-intl.dashscope.aliyuncs.com/v1",
        error=None,
    )
    assert result.access_state == ProviderLaneAccessState.ACCESS_READY
    assert result.reason_code == "probe_passed"


def test_plain_success_response_text_does_not_override_access_ready() -> None:
    result = classify_provider_lane_access(
        provider_name="alibaba",
        model_name="MiniMax-M2.5",
        base_url="https://coding-intl.dashscope.aliyuncs.com/v1",
        response_text="San Francisco, United States: 10.1C, cloudy.",
    )
    assert result.access_state == ProviderLaneAccessState.ACCESS_READY
    assert result.reason_code == "probe_passed"
