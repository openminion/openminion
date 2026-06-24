from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

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


def _bridge(model: str = "claude-3-5-sonnet-latest") -> LLMCTLBridgeProvider:
    with patch(
        "openminion.modules.llm.providers.bridge._import_openminion_llm"
    ) as mock_import:
        mock_import.return_value = (
            _fake_agent_profile_cls(),
            _fake_llmctl_cls(),
            _fake_tool_policy_cls(),
        )
        return LLMCTLBridgeProvider(
            provider_name="anthropic",
            model=model,
            provider_config={"api_key": "test-key", "model": model},
        )


def _make_resp(**kwargs) -> ProviderResponse:
    return ProviderResponse(
        text=kwargs.get("text", "ok"),
        model=kwargs.get("model", "claude-3-5-sonnet-latest"),
        usage=kwargs.get("usage", {}),
        tool_calls=kwargs.get("tool_calls", []),
    )


def test_anthropic_in_supported_providers() -> None:
    assert "anthropic" in SUPPORTED_PROVIDERS


def test_claude_alias_in_supported_providers() -> None:
    assert "claude" in SUPPORTED_PROVIDERS


def test_bridge_is_llm_provider() -> None:
    assert isinstance(_bridge(), LLMProvider)


def test_bridge_name_attribute() -> None:
    assert _bridge().name == "anthropic"


def test_generate_returns_response() -> None:
    bridge = _bridge()
    with patch.object(
        bridge,
        "generate",
        new=AsyncMock(return_value=_make_resp(text="Hello from Anthropic")),
    ):
        result = asyncio.run(
            bridge.generate(
                ProviderRequest(user_message="hello", system_prompt="helpful")
            )
        )
    assert result.text == "Hello from Anthropic"


def test_generate_raises_provider_error() -> None:
    bridge = _bridge()
    with patch.object(
        bridge,
        "generate",
        new=AsyncMock(side_effect=ProviderError("rate_limited")),
    ):
        with pytest.raises(ProviderError):
            asyncio.run(
                bridge.generate(ProviderRequest(user_message="hi", system_prompt=""))
            )


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
                        ProviderHistoryMessage(role="user", content="prior user"),
                        ProviderHistoryMessage(
                            role="assistant", content="prior assistant"
                        ),
                    ],
                )
            )
        )
    assert len(captured[0].history) == 2


def test_tool_calls_returned() -> None:
    bridge = _bridge()
    tool_call = ProviderToolCall(name="calculator", id="c1", arguments={"expr": "2+2"})
    with patch.object(
        bridge,
        "generate",
        new=AsyncMock(return_value=_make_resp(text="", tool_calls=[tool_call])),
    ):
        result = asyncio.run(
            bridge.generate(
                ProviderRequest(
                    user_message="2+2",
                    system_prompt="",
                    tools=[ProviderToolSpec(name="calculator", description="eval")],
                )
            )
        )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "calculator"
