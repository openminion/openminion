from __future__ import annotations

from typing import Any

import pytest

from openminion.base.config import (
    AgentProfileConfig,
    OpenMinionConfig,
    RunProfileOverrides,
    build_runtime_config,
    run_profile_overrides_from_mapping,
)


def _config() -> OpenMinionConfig:
    return OpenMinionConfig(
        agents={
            "coder": AgentProfileConfig(
                name="coder",
                provider="anthropic",
                provider_config_overrides={"model": "claude-sonnet-5"},
                model_connections={
                    "anthropic": {
                        "provider": "anthropic",
                        "display_name": "Anthropic",
                        "models": ["claude-sonnet-5"],
                        "default": True,
                    },
                    "minimax": {
                        "provider": "openai",
                        "display_name": "MiniMax",
                        "models": ["MiniMax-M2.7", "MiniMax-M2.7-highspeed"],
                        "provider_config_overrides": {
                            "base_url": "https://api.minimax.io/v1",
                            "api_key_env": "MINIMAX_API_KEY",
                        },
                    },
                },
            )
        },
        default_agent="coder",
    )


def test_model_connections_round_trip_through_config_payload() -> None:
    config = _config()

    restored = OpenMinionConfig.from_dict(config.to_dict())
    profile = restored.agents["coder"]

    assert profile.model_connections["anthropic"]["default"] is True
    assert profile.model_connections["minimax"]["models"] == [
        "MiniMax-M2.7",
        "MiniMax-M2.7-highspeed",
    ]
    assert (
        profile.model_connections["minimax"]["provider_config_overrides"]["base_url"]
        == "https://api.minimax.io/v1"
    )


def test_runtime_model_connection_selects_adapter_route_and_model() -> None:
    runtime_config = build_runtime_config(
        _config(),
        agent_id="coder",
        overrides=RunProfileOverrides(
            provider="minimax",
            model="MiniMax-M2.7-highspeed",
        ),
    )

    assert runtime_config.agents["coder"].provider == "openai"
    assert runtime_config.providers.openai.model == "MiniMax-M2.7-highspeed"
    assert runtime_config.providers.openai.base_url == "https://api.minimax.io/v1"
    assert runtime_config.providers.openai.api_key_env == "MINIMAX_API_KEY"


def test_run_profile_mapping_uses_connection_as_provider_override() -> None:
    overrides = run_profile_overrides_from_mapping(
        {
            "override_provider": "minimax",
            "override_model": "MiniMax-M2.7",
        }
    )

    assert overrides.provider == "minimax"
    assert overrides.model == "MiniMax-M2.7"


def _ollama_config(
    provider_num_ctx: Any = None, connection_num_ctx: Any = None
) -> dict[str, Any]:
    ollama: dict[str, Any] = {"model": "qwen2.5:7b"}
    if provider_num_ctx is not None:
        ollama["num_ctx"] = provider_num_ctx
    connection: dict[str, Any] = {"provider": "ollama", "models": ["qwen2.5:7b"]}
    if connection_num_ctx is not None:
        connection["provider_config_overrides"] = {"num_ctx": connection_num_ctx}
    return {
        "default_agent": "local",
        "providers": {"ollama": ollama},
        "agents": {
            "local": {
                "name": "local",
                "provider": "ollama",
                "model_connections": {"local-ollama": connection},
            }
        },
    }


def _ollama_num_ctx(payload: dict[str, Any], *, connection: bool) -> int:
    runtime_config = build_runtime_config(
        OpenMinionConfig.from_dict(payload),
        agent_id="local",
        overrides=RunProfileOverrides(provider="local-ollama" if connection else ""),
    )
    return runtime_config.providers.ollama.num_ctx


def test_ollama_num_ctx_defaults_to_unset_and_round_trips() -> None:
    assert OpenMinionConfig.from_dict(_ollama_config()).providers.ollama.num_ctx == 0

    config = OpenMinionConfig.from_dict(_ollama_config(provider_num_ctx=8192))
    restored = OpenMinionConfig.from_dict(config.to_dict())

    assert restored.providers.ollama.num_ctx == 8192


def test_ollama_connection_num_ctx_overrides_provider_value() -> None:
    assert _ollama_num_ctx(_ollama_config(8192), connection=False) == 8192
    assert _ollama_num_ctx(_ollama_config(8192), connection=True) == 8192
    assert _ollama_num_ctx(_ollama_config(8192, 16384), connection=True) == 16384


@pytest.mark.parametrize("value", [True, "4096"])
def test_ollama_num_ctx_reaches_the_adapter_uncoerced(value: Any) -> None:
    from_file = _ollama_num_ctx(
        _ollama_config(provider_num_ctx=value), connection=False
    )
    from_connection = _ollama_num_ctx(
        _ollama_config(connection_num_ctx=value), connection=True
    )

    for num_ctx in (from_file, from_connection):
        assert type(num_ctx) is type(value)
        assert num_ctx == value
