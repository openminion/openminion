from __future__ import annotations

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
