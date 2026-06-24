"""Provider config parsing helpers."""

from __future__ import annotations

from typing import Any

from openminion.base.config.parse import _as_bool, _as_float, _as_int, _as_int_list
from openminion.base.config.providers import (
    AnthropicProviderConfig,
    CerebrasProviderConfig,
    CortensorProviderConfig,
    GroqProviderConfig,
    OllamaProviderConfig,
    OpenAIProviderConfig,
    OpenRouterProviderConfig,
    ProvidersConfig,
)

_KIND_STR = "str"
_KIND_INT = "int"
_KIND_FLOAT = "float"
_KIND_BOOL = "bool"
_KIND_INT_LIST = "int_list"

_PROVIDER_SPECS: dict[str, tuple[type[Any], dict[str, tuple[str, Any]]]] = {
    "openai": (
        OpenAIProviderConfig,
        {
            "model": (_KIND_STR, "gpt-4.1-mini"),
            "api_key": (_KIND_STR, ""),
            "api_key_env": (_KIND_STR, "OPENAI_API_KEY"),
            "base_url": (_KIND_STR, "https://api.openai.com/v1"),
            "timeout_seconds": (_KIND_INT, 60),
            "temperature": (_KIND_FLOAT, 0.2),
            "tool_call_strategy": (_KIND_STR, "hybrid"),
        },
    ),
    "anthropic": (
        AnthropicProviderConfig,
        {
            "model": (_KIND_STR, "claude-3-5-sonnet-latest"),
            "api_key": (_KIND_STR, ""),
            "api_key_env": (_KIND_STR, "ANTHROPIC_API_KEY"),
            "base_url": (_KIND_STR, "https://api.anthropic.com/v1"),
            "timeout_seconds": (_KIND_INT, 60),
            "temperature": (_KIND_FLOAT, 0.2),
            "max_tokens": (_KIND_INT, 1024),
            "api_version": (_KIND_STR, "2023-06-01"),
        },
    ),
    "openrouter": (
        OpenRouterProviderConfig,
        {
            "model": (_KIND_STR, "openai/gpt-4.1-mini"),
            "api_key": (_KIND_STR, ""),
            "api_key_env": (_KIND_STR, "OPENROUTER_API_KEY"),
            "base_url": (_KIND_STR, "https://openrouter.ai/api/v1"),
            "timeout_seconds": (_KIND_INT, 60),
            "temperature": (_KIND_FLOAT, 0.2),
            "app_name": (_KIND_STR, "openminion"),
            "app_url": (_KIND_STR, ""),
            "tool_call_strategy": (_KIND_STR, "hybrid"),
            "max_tokens": (_KIND_INT, 2048),
        },
    ),
    "cerebras": (
        CerebrasProviderConfig,
        {
            "model": (_KIND_STR, "gpt-oss-120b"),
            "api_key": (_KIND_STR, ""),
            "api_key_env": (_KIND_STR, "CEREBRAS_API_KEY"),
            "base_url": (_KIND_STR, "https://api.cerebras.ai/v1"),
            "timeout_seconds": (_KIND_INT, 120),
            "temperature": (_KIND_FLOAT, 0.2),
            "tool_call_strategy": (_KIND_STR, "hybrid"),
        },
    ),
    "groq": (
        GroqProviderConfig,
        {
            "model": (_KIND_STR, "llama-3.3-70b-versatile"),
            "api_key": (_KIND_STR, ""),
            "api_key_env": (_KIND_STR, "GROQ_API_KEY"),
            "base_url": (_KIND_STR, "https://api.groq.com/openai/v1"),
            "timeout_seconds": (_KIND_INT, 120),
            "temperature": (_KIND_FLOAT, 0.2),
            "tool_call_strategy": (_KIND_STR, "hybrid"),
        },
    ),
    "ollama": (
        OllamaProviderConfig,
        {
            "model": (_KIND_STR, "llama3.1"),
            "base_url": (_KIND_STR, "http://127.0.0.1:11434"),
            "timeout_seconds": (_KIND_INT, 60),
            "temperature": (_KIND_FLOAT, 0.2),
            "api_key": (_KIND_STR, ""),
            "api_key_env": (_KIND_STR, "OLLAMA_API_KEY"),
            "tool_call_strategy": (_KIND_STR, "fallback"),
        },
    ),
    "cortensor": (
        CortensorProviderConfig,
        {
            "model": (_KIND_STR, "gpt-oss-20b"),
            "base_url": (
                _KIND_STR,
                "http://127.0.0.1:8080/api/v2/completions",
            ),
            "timeout_seconds": (_KIND_INT, 420),
            "transport_timeout_buffer_seconds": (_KIND_INT, 30),
            "result_wait_attempts": (_KIND_INT, 3),
            "result_wait_interval_seconds": (_KIND_FLOAT, 2.0),
            "precommit_timeout_seconds": (_KIND_INT, 300),
            "max_tokens": (_KIND_INT, 4096),
            "temperature": (_KIND_FLOAT, 0.2),
            "top_p": (_KIND_FLOAT, 1.0),
            "top_k": (_KIND_INT, 0),
            "presence_penalty": (_KIND_FLOAT, 0.0),
            "frequency_penalty": (_KIND_FLOAT, 0.0),
            "node_type": (_KIND_INT, 0),
            "api_key": (_KIND_STR, ""),
            "api_key_env": (_KIND_STR, "CORTENSOR_API_KEY"),
            "tool_call_strategy": (_KIND_STR, "hybrid"),
            "api_mode": (_KIND_STR, "auto"),
            "session_id": (_KIND_INT, 1),
            "session_ids": (_KIND_INT_LIST, []),
            "session_pool": (_KIND_STR, "auto"),
            "dedicated_session_ids": (_KIND_INT_LIST, []),
            "ephemeral_session_ids": (_KIND_INT_LIST, []),
            "session_parallel_requests": (_KIND_INT, 1),
            "session_retry_rounds": (_KIND_INT, 1),
            "prompt_type": (_KIND_INT, 1),
            "stream": (_KIND_BOOL, False),
            "privacy_level": (_KIND_STR, "high"),
        },
    ),
}


def _coerce_value(raw_value: Any, kind: str, default: Any) -> Any:
    if kind == _KIND_STR:
        return str(raw_value if raw_value is not None else default)
    if kind == _KIND_INT:
        return _as_int(raw_value, default)
    if kind == _KIND_FLOAT:
        return _as_float(raw_value, default)
    if kind == _KIND_BOOL:
        return _as_bool(raw_value, default)
    if kind == _KIND_INT_LIST:
        return _as_int_list(raw_value)
    raise ValueError(f"unknown provider field kind: {kind}")


def _extract_provider_payloads(
    effective_providers_payload: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    extracted: dict[str, dict[str, Any]] = {}
    for name in _PROVIDER_SPECS:
        raw_payload = effective_providers_payload.get(name)
        extracted[name] = raw_payload if isinstance(raw_payload, dict) else {}
    return extracted


def _build_providers_config(
    provider_payloads: dict[str, dict[str, Any]],
) -> ProvidersConfig:
    configs: dict[str, Any] = {}
    for name, (provider_cls, field_specs) in _PROVIDER_SPECS.items():
        payload = provider_payloads.get(name, {})
        kwargs = {
            field_name: _coerce_value(payload.get(field_name), kind, default)
            for field_name, (kind, default) in field_specs.items()
        }
        configs[name] = provider_cls(**kwargs)
    return ProvidersConfig(**configs)


def _providers_config_to_payload(config: ProvidersConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for name, (_, field_specs) in _PROVIDER_SPECS.items():
        provider_config = getattr(config, name)
        provider_payload: dict[str, Any] = {}
        for field_name, (kind, _) in field_specs.items():
            value = getattr(provider_config, field_name)
            if kind == _KIND_INT_LIST:
                provider_payload[field_name] = list(value)
            elif kind == _KIND_BOOL:
                provider_payload[field_name] = bool(value)
            elif kind == _KIND_FLOAT:
                provider_payload[field_name] = float(value)
            else:
                provider_payload[field_name] = value
        payload[name] = provider_payload
    return payload
