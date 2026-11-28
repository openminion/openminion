from __future__ import annotations

from typing import Any

import pytest

from openminion.base.config import (
    AgentProfileConfig,
    ConfigError,
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


@pytest.mark.parametrize(
    "policy",
    [
        {"mode": "native"},
        {
            "mode": "separate",
            "connection_id": "anthropic",
            "model": "claude-sonnet-5",
            "purpose": "text",
        },
    ],
)
def test_image_policy_round_trip_does_not_change_chat_route(policy: dict) -> None:
    payload = _config().to_dict()
    payload["agents"]["coder"]["image_input"] = policy
    restored = OpenMinionConfig.from_dict(payload)
    assert restored.agents["coder"].to_dict()["image_input"] == policy
    assert restored.agents["coder"].provider == "anthropic"
    assert restored.agents["coder"].provider_config_overrides == {
        "model": "claude-sonnet-5"
    }


@pytest.mark.parametrize(
    "policy",
    [
        {"mode": "automatic"},
        {"mode": "native", "purpose": "describe"},
        {"mode": "native", "connection_id": "anthropic"},
        {"mode": "separate"},
        {"mode": "separate", "connection_id": "missing", "model": "claude-sonnet-5"},
        {"mode": "separate", "connection_id": "anthropic", "model": "missing"},
        {"mode": "native", "api_key": "not-a-secret"},
    ],
)
def test_invalid_image_policy_is_rejected(policy: dict) -> None:
    payload = _config().to_dict()
    payload["agents"]["coder"]["image_input"] = policy
    with pytest.raises((ValueError, ConfigError)):
        OpenMinionConfig.from_dict(payload)


def test_image_reader_connection_cannot_be_removed_from_saved_config() -> None:
    payload = _config().to_dict()
    profile = payload["agents"]["coder"]
    profile["image_input"] = {
        "mode": "separate",
        "connection_id": "minimax",
        "model": "MiniMax-M2.7",
    }
    restored = OpenMinionConfig.from_dict(payload).to_dict()
    del restored["agents"]["coder"]["model_connections"]["minimax"]
    with pytest.raises(ConfigError, match="image reader"):
        OpenMinionConfig.from_dict(restored)


@pytest.mark.parametrize(
    "capabilities",
    [
        {"missing": "supported"},
        {"claude-sonnet-5": True},
        {"claude-sonnet-5": "maybe"},
        [],
    ],
)
def test_image_capabilities_require_exact_models_and_explicit_states(
    capabilities: object,
) -> None:
    payload = _config().to_dict()
    payload["agents"]["coder"]["model_connections"]["anthropic"]["image_input"] = (
        capabilities
    )
    with pytest.raises((ValueError, ConfigError)):
        OpenMinionConfig.from_dict(payload)


def test_adding_model_preserves_capabilities_only_for_unchanged_route() -> None:
    from openminion.modules.llm.model_connections import add_model_connection

    profile = _config().agents["coder"]
    profile.model_connections["anthropic"]["image_input"] = {
        "claude-sonnet-5": "supported"
    }
    add_model_connection(
        profile,
        connection_id="anthropic",
        display_name="Anthropic",
        provider="anthropic",
        model="other",
        provider_patch={},
        default=False,
    )
    assert profile.model_connections["anthropic"]["image_input"] == {
        "claude-sonnet-5": "supported"
    }
    add_model_connection(
        profile,
        connection_id="anthropic",
        display_name="Other endpoint",
        provider="anthropic",
        model="other",
        provider_patch={"base_url": "https://example.invalid"},
        default=False,
    )
    assert "image_input" not in profile.model_connections["anthropic"]


def test_separate_reader_defaults_to_describe():
    payload = _config().to_dict()
    payload["agents"]["coder"]["image_input"] = {
        "mode": "separate",
        "connection_id": "anthropic",
        "model": "claude-sonnet-5",
    }
    profile = OpenMinionConfig.from_dict(payload).agents["coder"]
    assert profile.image_input.purpose == "describe"


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
