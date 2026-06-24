from __future__ import annotations

import pytest

from openminion.modules.llm.providers.base import ProviderError
from openminion.modules.llm.providers.factory import SUPPORTED_PROVIDERS
from openminion.modules.llm.providers.bridge import LLMCTLBridgeProvider
from openminion.modules.llm.providers.registry import ProviderRegistry


def _logger():
    import logging

    return logging.getLogger("openminion.tests.provider-registry")


def test_all_expected_providers_in_set() -> None:
    expected = {
        "echo",
        "openai",
        "anthropic",
        "claude",
        "openrouter",
        "cerebras",
        "groq",
        "ollama",
        "cortensor",
    }
    assert SUPPORTED_PROVIDERS == expected


def test_supported_providers_is_frozen() -> None:
    with pytest.raises((AttributeError, TypeError)):
        SUPPORTED_PROVIDERS.add("newprovider")  # type: ignore[union-attr]


def test_register_and_build() -> None:
    registry = ProviderRegistry()
    bridge = LLMCTLBridgeProvider(
        provider_name="echo", model="echo", provider_config={"model": "echo"}
    )
    registry.register("echo", lambda config, logger: bridge)
    result = registry.build("echo", None, logger=_logger())  # type: ignore[arg-type]
    assert result is bridge


def test_unknown_provider_raises() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ProviderError):
        registry.build("missing", None, logger=_logger())  # type: ignore[arg-type]


def test_empty_name_raises() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ProviderError):
        registry.register("", lambda config, logger: None)  # type: ignore[return-value]


def test_names_returns_sorted_list() -> None:
    registry = ProviderRegistry()
    bridge = LLMCTLBridgeProvider(
        provider_name="echo", model="echo", provider_config={"model": "echo"}
    )
    registry.register("z_provider", lambda config, logger: bridge)
    registry.register("a_provider", lambda config, logger: bridge)
    names = registry.names()
    assert names == sorted(names)
