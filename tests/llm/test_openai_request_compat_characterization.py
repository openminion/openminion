from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from openminion.modules.llm.providers.adapters import OpenAIProvider
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


def _complete_with_payload(*, model: str, base_url: str) -> tuple[str | None, str]:
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
    return profile, rendered


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
    profile, rendered = _complete_with_payload(model=model, base_url=base_url)

    assert profile == expected_profile
    assert expected_label in rendered
    assert (
        "Native tool-calling contract:" not in rendered
        if expected_label == "Tool-calling contract:"
        else "Tool-calling contract:" not in rendered
    )
