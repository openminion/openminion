from __future__ import annotations

import logging
from typing import Any, Dict, Iterator
from unittest.mock import patch

import pytest

from openminion.base.constants import OPENMINION_PROVIDER_INTERFACE_STRICT_ENV
from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.interfaces import LLM_RESPONSE_INTERFACE_VERSION
from openminion.modules.llm.providers.contract import (
    PROVIDER_INTERFACE_VERSION,
    ensure_provider,
)
from openminion.modules.llm.providers.plugins import ProviderRegistry
from openminion.modules.llm.providers.contracts import (
    ProviderError,
    validate_provider_response_shape,
)
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
from openminion.modules.llm.schemas import (
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    UsageInfo,
)


@pytest.mark.parametrize(
    "factory",
    [
        echo_provider,
        openai_provider,
        openrouter_provider,
        anthropic_provider,
        claude_provider,
        ollama_provider,
        groq_provider,
        cerebras_provider,
        cortensor_provider,
    ],
)
def test_adapter_providers_satisfy_interface(factory) -> None:
    provider = factory()
    ensure_provider(provider, component_name=f"provider:{provider.name}")


class _MissingListModelsProvider:
    name = "missing_list_models"
    contract_version = LLM_RESPONSE_INTERFACE_VERSION
    provider_interface_version = PROVIDER_INTERFACE_VERSION

    def complete(self, request: LLMRequest, config: Dict[str, Any]) -> LLMResponse:
        del request, config
        return LLMResponse(
            ok=True,
            provider=self.name,
            model="stub",
            output_text="ok",
            assistant_messages=[],
            tool_calls=[],
            usage=UsageInfo(input_tokens=1, output_tokens=1, total_tokens=2),
            latency_ms=0,
            provider_raw={},
        )

    def healthcheck(self, config: Dict[str, Any]) -> Dict[str, Any]:
        del config
        return {"ok": True}


def test_registry_rejects_missing_method() -> None:
    registry = ProviderRegistry()
    with pytest.raises(LLMCtlError):
        registry.add(_MissingListModelsProvider())


# provider_interface_version validation


_BUILTIN_FACTORIES = [
    echo_provider,
    openai_provider,
    openrouter_provider,
    anthropic_provider,
    claude_provider,
    ollama_provider,
    groq_provider,
    cerebras_provider,
    cortensor_provider,
]


@pytest.mark.parametrize("factory", _BUILTIN_FACTORIES)
def test_builtin_providers_declare_interface_version(factory) -> None:
    provider = factory()
    assert provider.provider_interface_version == PROVIDER_INTERFACE_VERSION


def test_builtin_providers_register_without_warnings_in_default_mode(
    monkeypatch, caplog
) -> None:
    monkeypatch.delenv(OPENMINION_PROVIDER_INTERFACE_STRICT_ENV, raising=False)
    registry = ProviderRegistry()
    with caplog.at_level(logging.WARNING):
        for factory in _BUILTIN_FACTORIES:
            try:
                registry.add(factory())
            except ValueError as exc:
                # echo + openai have shared bases; "already registered" is
                # only triggered if a concrete provider name collides, which
                # would itself be a different bug — surface it.
                if "already registered" not in str(exc):
                    raise

    interface_warnings = [
        record
        for record in caplog.records
        if "provider_interface_version" in record.getMessage()
    ]
    assert interface_warnings == [], (
        f"unexpected provider_interface_version warnings: {interface_warnings}"
    )


class _NoInterfaceVersionProvider:
    name = "no_iface_version"
    contract_version = LLM_RESPONSE_INTERFACE_VERSION

    def complete(self, request: LLMRequest, config: Dict[str, Any]) -> LLMResponse:
        del request, config
        return LLMResponse(
            ok=True,
            provider=self.name,
            model="stub",
            output_text="ok",
            assistant_messages=[],
            tool_calls=[],
            usage=UsageInfo(),
            latency_ms=0,
            provider_raw={},
        )

    def list_models(self, config: Dict[str, Any]) -> list:
        del config
        return ["stub"]

    def healthcheck(self, config: Dict[str, Any]) -> Dict[str, Any]:
        del config
        return {"ok": True}


def test_missing_interface_version_warns_in_default_mode(monkeypatch, caplog) -> None:
    monkeypatch.delenv(OPENMINION_PROVIDER_INTERFACE_STRICT_ENV, raising=False)
    with caplog.at_level(logging.WARNING):
        ensure_provider(_NoInterfaceVersionProvider())
    matched = [
        record
        for record in caplog.records
        if "provider_interface_version" in record.getMessage()
    ]
    assert matched, "expected a provider_interface_version warning"


def test_missing_interface_version_raises_in_strict_mode(monkeypatch) -> None:
    monkeypatch.setenv(OPENMINION_PROVIDER_INTERFACE_STRICT_ENV, "1")
    with pytest.raises(LLMCtlError) as ctx:
        ensure_provider(_NoInterfaceVersionProvider())
    assert ctx.value.code == "PROVIDER_CONTRACT_VIOLATION"
    assert "no_iface_version" in str(ctx.value)


# adapter return-shape compatibility tests


def _stub_openai_like_response(model: str = "openai/gpt-4.1-mini") -> Dict[str, Any]:
    return {
        "id": "stub",
        "model": model,
        "choices": [
            {
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
    }


def _stub_anthropic_response() -> Dict[str, Any]:
    return {
        "id": "stub",
        "model": "claude-3-5-sonnet-latest",
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _stub_ollama_response() -> Dict[str, Any]:
    return {
        "model": "llama3.1",
        "message": {"role": "assistant", "content": "ok"},
        "done": True,
        "prompt_eval_count": 1,
        "eval_count": 1,
    }


def _stub_request() -> LLMRequest:
    return LLMRequest.model_validate({"messages": [{"role": "user", "content": "hi"}]})


# Adapters call ``helpers._http_json_post`` (which delegates to
# ``transport.http.http_json_post``); patching the helpers entry point keeps
# the test focused on adapter behavior.
_HTTP_TARGET = "openminion.modules.llm.providers.message_payloads.http_json_post"


@pytest.mark.parametrize(
    ("factory", "stub_response", "config"),
    [
        (
            openai_provider,
            _stub_openai_like_response("gpt-4.1-mini"),
            {"api_key": "sk-test", "model": "gpt-4.1-mini"},
        ),
        (
            openrouter_provider,
            _stub_openai_like_response("openai/gpt-4.1-mini"),
            {"api_key": "or-test", "model": "openai/gpt-4.1-mini"},
        ),
        (
            anthropic_provider,
            _stub_anthropic_response(),
            {"api_key": "ant-test", "model": "claude-3-5-sonnet-latest"},
        ),
        (
            ollama_provider,
            _stub_ollama_response(),
            {"model": "llama3.1"},
        ),
    ],
)
def test_adapter_complete_returns_llmresponse_compatible(
    factory, stub_response, config
) -> None:
    provider = factory()
    with patch(_HTTP_TARGET, return_value=stub_response):
        result = provider.complete(_stub_request(), config)
    validate_provider_response_shape("complete", result)
    assert result.provider == provider.name
    assert isinstance(result.model, str) and result.model


@pytest.mark.parametrize(
    "factory",
    [
        openai_provider,
        openrouter_provider,
        anthropic_provider,
        ollama_provider,
        cortensor_provider,
    ],
)
def test_adapter_list_models_returns_list_str(factory) -> None:
    provider = factory()
    # Use a config that lists the expected models so list_models doesn't
    # need to issue HTTP calls.
    config = {"models": ["model-a", "model-b"]}
    result = provider.list_models(config)
    validate_provider_response_shape("list_models", result)
    assert "model-a" in result


@pytest.mark.parametrize(
    "factory",
    [
        openai_provider,
        openrouter_provider,
        anthropic_provider,
        ollama_provider,
        cortensor_provider,
    ],
)
def test_adapter_healthcheck_returns_dict(factory) -> None:
    provider = factory()
    result = provider.healthcheck({})
    validate_provider_response_shape("healthcheck", result)
    assert result.get("provider") == provider.name


def test_validate_provider_response_shape_rejects_wrong_list_models_type() -> None:
    with pytest.raises(ProviderError):
        validate_provider_response_shape("list_models", {"not": "a list"})


def test_validate_provider_response_shape_rejects_wrong_healthcheck_type() -> None:
    with pytest.raises(ProviderError):
        validate_provider_response_shape("healthcheck", "ok")


def test_validate_provider_response_shape_rejects_wrong_complete_shape() -> None:
    class _BadResponse:
        # missing provider/model attributes
        output_text = "x"

    with pytest.raises(ProviderError):
        validate_provider_response_shape("complete", _BadResponse())


class _WrongShapeProvider:
    name = "wrong_shape"
    contract_version = LLM_RESPONSE_INTERFACE_VERSION
    provider_interface_version = PROVIDER_INTERFACE_VERSION

    def complete(self, request: LLMRequest, config: Dict[str, Any]) -> LLMResponse:
        del request, config
        return LLMResponse(
            ok=True,
            provider=self.name,
            model="stub",
            output_text="ok",
            assistant_messages=[],
            tool_calls=[],
            usage=UsageInfo(),
            latency_ms=0,
            provider_raw={},
        )

    def stream(
        self, request: LLMRequest, config: Dict[str, Any]
    ) -> Iterator[LLMStreamEvent]:
        del request, config
        if False:
            yield  # pragma: no cover

    def list_models(self, config: Dict[str, Any]):  # type: ignore[override]
        del config
        return {"unexpected": "dict"}

    def healthcheck(self, config: Dict[str, Any]) -> Dict[str, Any]:
        del config
        return {"ok": True}


def test_contract_validator_rejects_wrong_shape_provider() -> None:
    provider = _WrongShapeProvider()
    with pytest.raises(ProviderError):
        validate_provider_response_shape("list_models", provider.list_models({}))
