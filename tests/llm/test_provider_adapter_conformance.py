from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pytest

from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.interfaces import LLM_RESPONSE_INTERFACE_VERSION
from openminion.modules.llm.providers.adapters import (
    anthropic_provider,
    cerebras_provider,
    claude_provider,
    cortensor_provider,
    echo_provider,
    groq_provider,
    ollama_provider,
    openai_provider,
    openrouter_provider,
)
from openminion.modules.llm.providers.contract import (
    PROVIDER_INTERFACE_VERSION,
    ensure_provider,
    local_provider,
    stub_provider,
)
from openminion.modules.llm.providers.plugins import ProviderRegistry
from openminion.modules.llm.schemas import LLMRequest


@dataclass(frozen=True)
class AdapterExpectation:
    name: str
    factory: Callable[[], object]
    native_stream: bool
    fixture_complete: bool = False
    thinking_fixture: bool = False
    tool_normalization: str = "unsupported"


ADAPTERS = (
    AdapterExpectation(
        "stub", stub_provider, native_stream=True, fixture_complete=True
    ),
    AdapterExpectation(
        "local", local_provider, native_stream=True, fixture_complete=True
    ),
    AdapterExpectation(
        "echo", echo_provider, native_stream=False, fixture_complete=True
    ),
    AdapterExpectation(
        "openai", openai_provider, native_stream=True, tool_normalization="native"
    ),
    AdapterExpectation(
        "openrouter",
        openrouter_provider,
        native_stream=True,
        tool_normalization="native",
    ),
    AdapterExpectation("anthropic", anthropic_provider, native_stream=True),
    AdapterExpectation("claude", claude_provider, native_stream=True),
    AdapterExpectation(
        "ollama", ollama_provider, native_stream=False, thinking_fixture=True
    ),
    AdapterExpectation(
        "groq", groq_provider, native_stream=True, tool_normalization="native"
    ),
    AdapterExpectation(
        "cerebras", cerebras_provider, native_stream=True, tool_normalization="native"
    ),
    AdapterExpectation("cortensor", cortensor_provider, native_stream=False),
)


@pytest.mark.parametrize("expectation", ADAPTERS, ids=lambda item: item.name)
def test_builtin_adapter_registration_contract(expectation: AdapterExpectation) -> None:
    provider = expectation.factory()
    ensure_provider(provider, component_name=f"provider:{expectation.name}")
    assert getattr(provider, "name") == expectation.name
    assert getattr(provider, "provider_interface_version") == PROVIDER_INTERFACE_VERSION


@pytest.mark.parametrize("expectation", ADAPTERS, ids=lambda item: item.name)
def test_builtin_adapter_native_stream_disposition(
    expectation: AdapterExpectation,
) -> None:
    provider = expectation.factory()
    assert callable(getattr(provider, "stream", None)) is expectation.native_stream


@pytest.mark.parametrize(
    "expectation",
    [item for item in ADAPTERS if item.fixture_complete],
    ids=lambda item: item.name,
)
def test_fixture_safe_adapters_normalize_text_and_usage(
    expectation: AdapterExpectation,
) -> None:
    provider = expectation.factory()
    response = provider.complete(
        LLMRequest(
            provider=expectation.name,
            model=f"{expectation.name}-model",
            messages=[{"role": "user", "content": "hello"}],
        ),
        {},
    )
    assert response.ok is True
    assert response.provider == expectation.name
    assert response.output_text
    assert response.assistant_messages
    assert response.usage.total_tokens is not None or expectation.name == "echo"


def test_malformed_provider_fails_predictably() -> None:
    class MissingCompleteProvider:
        name = "broken"
        contract_version = LLM_RESPONSE_INTERFACE_VERSION
        provider_interface_version = PROVIDER_INTERFACE_VERSION

        def list_models(self, config):
            return []

        def healthcheck(self, config):
            return {"ok": True}

    registry = ProviderRegistry()
    with pytest.raises(LLMCtlError) as exc:
        registry.add(MissingCompleteProvider())
    assert exc.value.code == "INVALID_ARGUMENT"
    assert exc.value.details["method"] == "complete"


def test_current_complete_only_native_stream_case_is_explicit() -> None:
    provider = echo_provider()
    assert callable(getattr(provider, "stream", None)) is False


def test_openai_compatible_service_extension_stays_inside_provider_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = openai_provider()
    captured: dict[str, object] = {}

    def fake_post(**kwargs):
        captured.update(kwargs)
        return {
            "model": "acme-model",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(
        "openminion.modules.llm.providers.openai.adapter._http_json_post",
        fake_post,
    )

    response = provider.complete(
        LLMRequest(
            provider="openai",
            model="acme-model",
            messages=[{"role": "user", "content": "hello"}],
        ),
        {
            "api_key": "test-key",
            "base_url": "https://api.acme.example/v1",
            "provider_identity": {
                "service_vendor": "acmecloud",
                "wire_protocol_family": "openai_chat_completions",
                "model_family": "gpt",
            },
        },
    )

    assert response.output_text == "ok"
    assert captured["url"] == "https://api.acme.example/v1/chat/completions"
    assert response.telemetry["normalization"]["provider_identity"][
        "service_vendor"
    ] == ("acmecloud")
