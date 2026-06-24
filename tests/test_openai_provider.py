from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from tests._csc_fixtures import _csc_install_default_agent

from openminion.modules.llm.providers.base import (
    LLMProvider,
    ProviderError,
    ProviderHistoryMessage,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderToolSpec,
)
from openminion.modules.llm.providers.factory import SUPPORTED_PROVIDERS, build_provider
from openminion.modules.llm.providers.bridge import LLMCTLBridgeProvider


def _logger():
    return logging.getLogger("openminion.tests.openai-bridge")


def _make_response(**kwargs) -> ProviderResponse:
    return ProviderResponse(
        text=kwargs.get("text", "Hello"),
        model=kwargs.get("model", "gpt-4.1-mini"),
        usage=kwargs.get(
            "usage", {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
        ),
        tool_calls=kwargs.get("tool_calls", []),
        finish_reason=kwargs.get("finish_reason", ""),
    )


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


def _bridge(model: str = "gpt-4.1-mini") -> LLMCTLBridgeProvider:
    with patch(
        "openminion.modules.llm.providers.bridge._import_openminion_llm"
    ) as mock_import:
        mock_import.return_value = (
            _fake_agent_profile_cls(),
            _fake_llmctl_cls(),
            _fake_tool_policy_cls(),
        )
        return LLMCTLBridgeProvider(
            provider_name="openai",
            model=model,
            provider_config={"api_key": "test-key", "model": model},
        )


def test_openai_in_supported_providers() -> None:
    assert "openai" in SUPPORTED_PROVIDERS


def test_bridge_is_llm_provider() -> None:
    assert isinstance(_bridge(), LLMProvider)


def test_bridge_name_attribute() -> None:
    assert _bridge().name == "openai"


def test_generate_returns_provider_response() -> None:
    bridge = _bridge()
    expected = _make_response(text="Hello from OpenAI")
    with patch.object(bridge, "generate", new=AsyncMock(return_value=expected)):
        result = asyncio.run(
            bridge.generate(
                ProviderRequest(user_message="hello", system_prompt="you are helpful")
            )
        )
    assert result.text == "Hello from OpenAI"
    assert result.usage["total_tokens"] == 18


def test_generate_raises_provider_error_on_failure() -> None:
    bridge = _bridge()
    with patch.object(
        bridge,
        "generate",
        new=AsyncMock(side_effect=ProviderError("connection refused")),
    ):
        with pytest.raises(ProviderError):
            asyncio.run(
                bridge.generate(ProviderRequest(user_message="hi", system_prompt=""))
            )


def test_history_messages_forwarded() -> None:
    bridge = _bridge()
    captured_req: list[ProviderRequest] = []

    async def _fake_generate(req: ProviderRequest) -> ProviderResponse:
        captured_req.append(req)
        return _make_response(text="ok")

    with patch.object(bridge, "generate", new=AsyncMock(side_effect=_fake_generate)):
        asyncio.run(
            bridge.generate(
                ProviderRequest(
                    user_message="latest",
                    system_prompt="you are helpful",
                    history=[
                        ProviderHistoryMessage(role="user", content="old user message"),
                        ProviderHistoryMessage(
                            role="assistant", content="old assistant reply"
                        ),
                    ],
                )
            )
        )
    assert len(captured_req) == 1
    assert len(captured_req[0].history) == 2


def test_tool_calls_in_response() -> None:
    bridge = _bridge()
    expected = _make_response(
        text="",
        tool_calls=[
            ProviderToolCall(name="search", id="c1", arguments={"query": "test"})
        ],
    )
    with patch.object(bridge, "generate", new=AsyncMock(return_value=expected)):
        result = asyncio.run(
            bridge.generate(
                ProviderRequest(
                    user_message="search for test",
                    system_prompt="",
                    tools=[ProviderToolSpec(name="search", description="Search")],
                )
            )
        )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "search"


def test_provider_error_construction() -> None:
    error = ProviderError("api_error: rate_limited")
    assert "rate_limited" in str(error)


def test_factory_raises_provider_error_without_bridge() -> None:
    from openminion.base.config import OpenMinionConfig

    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openai")
    with patch(
        "openminion.modules.llm.providers.factory.llmctl_bridge_available",
        return_value=False,
    ):
        with pytest.raises(ProviderError):
            build_provider(config, _logger())
