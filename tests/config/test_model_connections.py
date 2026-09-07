from __future__ import annotations

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
