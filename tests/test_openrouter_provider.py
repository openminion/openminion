from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from openminion.modules.llm.providers.base import (
    LLMProvider,
    ProviderError,
    ProviderHistoryMessage,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderToolSpec,
)
from openminion.modules.llm.providers.factory import SUPPORTED_PROVIDERS
from openminion.modules.llm.providers.bridge import LLMCTLBridgeProvider


def _fake_agent_profile_cls():
    class FakeAgentProfile:
        def __init__(self, name, default_provider, default_model, tool_policy=None):
            pass

    return FakeAgentProfile


def _fake_tool_policy_cls():
    class FakeToolPolicy:
        def __init__(self, **kwargs):
            pass

    return FakeToolPolicy


def _fake_llmctl_cls():
    class FakeLLMCTL:
        @classmethod
        def from_config(cls, config):
            return cls()

        def client(self, profile):
            return object()

    return FakeLLMCTL


def _bridge(model: str = "openai/gpt-4.1-mini") -> LLMCTLBridgeProvider:
    with patch("openminion.modules.llm.providers.bridge._import_openminion_llm") as m:
        m.return_value = (
            _fake_agent_profile_cls(),
            _fake_llmctl_cls(),
            _fake_tool_policy_cls(),
        )
        return LLMCTLBridgeProvider(
            provider_name="openrouter",
            model=model,
            provider_config={"api_key": "test-key", "model": model},
        )


def _make_resp(**k) -> ProviderResponse:
    return ProviderResponse(
        text=k.get("text", "ok"),
        model=k.get("model", "openai/gpt-4.1-mini"),
        usage=k.get(
            "usage", {"prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 13}
        ),
        tool_calls=k.get("tool_calls", []),
    )


def test_openrouter_in_supported_providers() -> None:
    assert "openrouter" in SUPPORTED_PROVIDERS


def test_bridge_is_llm_provider() -> None:
    assert isinstance(_bridge(), LLMProvider)


def test_provider_name() -> None:
    assert _bridge().name == "openrouter"


def test_generate_returns_response() -> None:
    bridge = _bridge()
    with patch.object(
        bridge,
        "generate",
        new=AsyncMock(return_value=_make_resp(text="Hello from OpenRouter")),
    ):
        result = asyncio.run(
            bridge.generate(
                ProviderRequest(user_message="hello", system_prompt="helpful")
            )
        )
    assert result.text == "Hello from OpenRouter"
    assert result.usage["total_tokens"] == 13


def test_generate_raises_provider_error() -> None:
    bridge = _bridge()
    with patch.object(
        bridge, "generate", new=AsyncMock(side_effect=ProviderError("bad request"))
    ):
        try:
            asyncio.run(
                bridge.generate(ProviderRequest(user_message="hi", system_prompt=""))
            )
        except ProviderError:
            pass
        else:
            raise AssertionError("expected ProviderError")


def test_history_forwarded() -> None:
    bridge = _bridge()
    captured: list[ProviderRequest] = []

    async def _fake(req):
        captured.append(req)
        return _make_resp()

    with patch.object(bridge, "generate", new=AsyncMock(side_effect=_fake)):
        asyncio.run(
            bridge.generate(
                ProviderRequest(
                    user_message="latest",
                    system_prompt="helpful",
                    history=[
                        ProviderHistoryMessage(role="user", content="prior"),
                        ProviderHistoryMessage(role="assistant", content="response"),
                    ],
                )
            )
        )
    assert len(captured[0].history) == 2


def test_tool_calls_returned() -> None:
    bridge = _bridge()
    tc = ProviderToolCall(name="search_web", id="c1", arguments={"q": "openrouter"})
    with patch.object(
        bridge,
        "generate",
        new=AsyncMock(return_value=_make_resp(text="", tool_calls=[tc])),
    ):
        result = asyncio.run(
            bridge.generate(
                ProviderRequest(
                    user_message="search openrouter",
                    system_prompt="",
                    tools=[ProviderToolSpec(name="search_web", description="Search")],
                )
            )
        )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].arguments.get("q") == "openrouter"
