from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from openminion.modules.llm.config import resolve_provider_identity_translation
from openminion.modules.llm.providers.behavior.resolver import resolve_behavior_profile
from openminion.modules.llm.providers.adapters import OpenAIProvider
from openminion.modules.llm.setup_catalog import get_setup_preset
from openminion.modules.llm.schemas import LLMRequest


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        return None


def _complete_with_payload(
    *, model: str, base_url: str
) -> tuple[str | None, str, str, dict[str, object]]:
    provider = OpenAIProvider()
    request = LLMRequest.model_validate(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": "Primary system context."},
                {"role": "user", "content": "hi"},
            ],
            "tools": [
                {
                    "name": "submit_output",
                    "description": "return structured output",
                    "input_schema": {"type": "object"},
                }
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": "submit_output"},
            },
        }
    )
    payload = {
        "model": model,
        "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    captured: dict[str, object] = {}

    def _fake_urlopen(http_request, timeout=None):
        del timeout
        captured["url"] = http_request.full_url
        captured["body"] = json.loads(http_request.data.decode("utf-8"))
        return _FakeHTTPResponse(payload)

    with patch(
        "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
        side_effect=_fake_urlopen,
    ):
        response = provider.complete(
            request,
            {
                "api_key": "test-key",
                "base_url": base_url,
                "tool_call_strategy": "hybrid",
            },
        )

    assert response.ok
    body = captured.get("body")
    assert isinstance(body, dict)
    messages = body.get("messages")
    assert isinstance(messages, list)
    rendered = "\n".join(
        str(item.get("content", "") or "")
        for item in messages
        if isinstance(item, dict)
    )
    profile = (
        dict(response.telemetry or {})
        .get("normalization", {})
        .get("request_compat_profile")
    )
    captured_url = str(captured.get("url") or "")
    return profile, rendered, captured_url, body


@pytest.mark.parametrize(
    ("model", "base_url", "expected_profile", "expected_label"),
    [
        (
            "MiniMax-M2.7",
            "https://api.minimax.io/v1",
            "minimax_openai_compat",
            "Native tool-calling contract:",
        ),
        (
            "MiniMax-M2.5",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "minimax_openai_compat",
            "Native tool-calling contract:",
        ),
        (
            "MiniMax-M2.7",
            "https://api.openai.com/v1",
            "openai_default",
            "Tool-calling contract:",
        ),
        (
            "gpt-4.1-mini",
            "https://api.minimax.io/v1",
            "openai_default",
            "Tool-calling contract:",
        ),
    ],
)
def test_openai_request_compat_characterization(
    model: str,
    base_url: str,
    expected_profile: str,
    expected_label: str,
) -> None:
    profile, rendered, _url, _body = _complete_with_payload(
        model=model, base_url=base_url
    )

    assert profile == expected_profile
    assert expected_label in rendered
    assert (
        "Native tool-calling contract:" not in rendered
        if expected_label == "Tool-calling contract:"
        else "Tool-calling contract:" not in rendered
    )


@pytest.mark.parametrize(
    (
        "preset_id",
        "model",
        "expected_vendor",
        "expected_model_family",
        "expected_url",
    ),
    [
        (
            "minimax",
            "MiniMax-M3",
            "minimax",
            "minimax",
            "https://api.minimax.io/v1/chat/completions",
        ),
        (
            "kimi",
            "kimi-k3",
            "kimi",
            "kimi",
            "https://api.moonshot.ai/v1/chat/completions",
        ),
        (
            "zai",
            "glm-5.2",
            "zai",
            "glm",
            "https://api.z.ai/api/paas/v4/chat/completions",
        ),
        (
            "zai-coding",
            "glm-5.2",
            "zai-coding",
            "glm",
            "https://api.z.ai/api/coding/paas/v4/chat/completions",
        ),
        (
            "deepseek",
            "deepseek-v4-flash",
            "deepseek",
            "deepseek",
            "https://api.deepseek.com/chat/completions",
        ),
        (
            "qwen-dashscope",
            "qwen-plus",
            "dashscope",
            "qwen",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        ),
        (
            "gemini",
            "gemini-3.6-flash",
            "gemini",
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        ),
        (
            "xai",
            "grok-4.5",
            "xai",
            "grok",
            "https://api.x.ai/v1/chat/completions",
        ),
        (
            "mistral",
            "mistral-large-latest",
            "mistral",
            "mistral",
            "https://api.mistral.ai/v1/chat/completions",
        ),
        (
            "together",
            "MiniMaxAI/MiniMax-M3",
            "together",
            "minimax",
            "https://api.together.ai/v1/chat/completions",
        ),
    ],
)
def test_frontier_openai_compatible_presets_resolve_identity_and_endpoint(
    preset_id: str,
    model: str,
    expected_vendor: str,
    expected_model_family: str,
    expected_url: str,
) -> None:
    preset = get_setup_preset(preset_id)
    identity = resolve_provider_identity_translation(
        preset.runtime_adapter,
        model=model,
        base_url=preset.default_base_url,
    )

    profile = resolve_behavior_profile(
        provider=preset.runtime_adapter,
        model=model,
        base_url=preset.default_base_url,
        provider_identity=identity,
    )
    request_compat, _rendered, url, body = _complete_with_payload(
        model=model,
        base_url=preset.default_base_url,
    )

    assert identity["service_vendor"] == expected_vendor
    assert identity["model_family"] == expected_model_family
    assert profile.provider_identity is not None
    assert profile.provider_identity.service_vendor == expected_vendor
    assert profile.provider_identity.model_family == expected_model_family
    assert url == expected_url
    assert body["model"] == model
    assert "tools" in body
    assert body["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_output"},
    }
    if preset_id == "minimax":
        assert request_compat == "minimax_openai_compat"
    else:
        assert request_compat == "openai_default"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "https://example-workspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    ],
)
def test_dashscope_openai_compatible_endpoint_variants_resolve_structurally(
    base_url: str,
) -> None:
    identity = resolve_provider_identity_translation(
        "openai",
        model="qwen-plus",
        base_url=base_url,
    )

    assert identity["service_vendor"] == "dashscope"
    assert identity["model_family"] == "qwen"
