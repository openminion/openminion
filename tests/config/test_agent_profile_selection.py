from __future__ import annotations

import pytest

from openminion.base.config import (
    ConfigError,
    OpenMinionConfig,
    RunProfileOverrides,
    build_runtime_config,
    resolve_runtime_profile,
    run_profile_overrides_from_mapping,
)
from openminion.base.config.core import resolve_default_agent_id


def test_run_profile_overrides_from_mapping_accepts_supported_fields() -> None:
    overrides = run_profile_overrides_from_mapping(
        {
            "override_provider": "anthropic",
            "override_model": "claude-3-5-haiku-latest",
            "override_system_prompt": "Stay concise.",
        }
    )

    assert overrides.provider == "anthropic"
    assert overrides.model == "claude-3-5-haiku-latest"
    assert overrides.system_prompt == "Stay concise."


def test_run_profile_overrides_from_mapping_accepts_permission_overrides() -> None:
    overrides = run_profile_overrides_from_mapping(
        {
            "permission_mode": "readonly",
            "permission_overrides": '{"file.write": "bypass", "exec.run": "ask"}',
        }
    )

    assert overrides.permission_mode == "readonly"
    assert overrides.permission_overrides == (
        ("exec.run", "ask"),
        ("file.write", "bypass"),
    )


def test_run_profile_overrides_from_mapping_rejects_unsupported_agent_rename() -> None:
    with pytest.raises(ConfigError) as exc:
        run_profile_overrides_from_mapping({"override_agent_name": "renamed-agent"})

    assert "override-agent-name" in str(exc.value)


def test_resolve_runtime_profile_applies_provider_and_prompt_after_selection() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "default_agent": "hello-agent",
            "agents": {
                "hello-agent": {"name": "hello-agent", "provider": "openrouter"},
                "planner-safe": {
                    "name": "planner-safe",
                    "provider": "openai",
                    "system_prompt": "Base planner prompt.",
                },
            },
        }
    )

    profile = resolve_runtime_profile(
        config,
        agent_id="planner-safe",
        overrides=RunProfileOverrides(
            provider="anthropic",
            system_prompt="Overridden planner prompt.",
        ),
    )

    assert profile.name == "planner-safe"
    assert profile.provider == "anthropic"
    assert profile.system_prompt == "Overridden planner prompt."


def _build_effective_profile(config, agent_id):
    effective = build_runtime_config(
        config,
        agent_id=agent_id,
        overrides=RunProfileOverrides(
            provider="anthropic",
            model="claude-3-5-haiku-latest",
            system_prompt="Use terse diagnostic prose.",
        ),
    )
    return effective, effective.agents[agent_id]


def test_build_runtime_config_applies_model_override_to_selected_provider() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "default_agent": "hello-agent",
            "agents": {
                "hello-agent": {"name": "hello-agent", "provider": "openrouter"},
                "planner-safe": {"name": "planner-safe", "provider": "openai"},
            },
        }
    )

    effective, effective_profile = _build_effective_profile(config, "planner-safe")

    assert effective_profile.name == "planner-safe"
    assert effective_profile.provider == "anthropic"
    assert effective_profile.system_prompt == "Use terse diagnostic prose."
    assert effective.providers.anthropic.model == "claude-3-5-haiku-latest"
    assert effective.providers.openai.model == config.providers.openai.model


def test_build_runtime_config_rebinds_runtime_default_agent_to_selected_profile() -> (
    None
):
    config = OpenMinionConfig.from_dict(
        {
            "default_agent": "minimax-m2-7",
            "agents": {
                "minimax-m2-7": {"name": "minimax-m2-7", "provider": "openai"},
                "minimax-m2-5": {"name": "minimax-m2-5", "provider": "openai"},
            },
        }
    )

    effective = build_runtime_config(config, agent_id="minimax-m2-5")

    assert resolve_default_agent_id(config) == "minimax-m2-7"
    assert resolve_default_agent_id(effective) == "minimax-m2-5"
    assert effective.default_agent == "minimax-m2-5"
    assert config.default_agent == "minimax-m2-7"


def test_build_runtime_config_applies_profile_provider_config_overrides() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "default_agent": "hello-agent",
            "agents": {
                "hello-agent": {
                    "name": "hello-agent",
                    "provider": "openrouter",
                    "provider_config_overrides": {
                        "model": "anthropic/claude-haiku-4.5",
                    },
                },
                "gpt-4o-mini": {
                    "name": "gpt-4o-mini",
                    "provider": "openrouter",
                    "provider_config_overrides": {
                        "model": "openai/gpt-4o-mini",
                        "tool_call_strategy": "hybrid",
                    },
                },
            },
        }
    )

    effective = build_runtime_config(config, agent_id="gpt-4o-mini")
    effective_profile = effective.agents["gpt-4o-mini"]

    assert effective_profile.name == "gpt-4o-mini"
    assert effective_profile.provider == "openrouter"
    assert effective.providers.openrouter.model == "openai/gpt-4o-mini"
    assert effective.providers.openrouter.tool_call_strategy == "hybrid"


def test_invocation_model_override_wins_over_profile_provider_config_override() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "default_agent": "hello-agent",
            "agents": {
                "hello-agent": {"name": "hello-agent", "provider": "openrouter"},
                "gpt-5": {
                    "name": "gpt-5",
                    "provider": "openrouter",
                    "provider_config_overrides": {
                        "model": "openai/gpt-5.4-mini",
                    },
                },
            },
        }
    )

    effective = build_runtime_config(
        config,
        agent_id="gpt-5",
        overrides=RunProfileOverrides(model="openai/gpt-5.4"),
    )

    assert effective.providers.openrouter.model == "openai/gpt-5.4"


def test_provider_override_bypasses_profile_provider_config_overrides() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "default_agent": "hello-agent",
            "agents": {
                "hello-agent": {"name": "hello-agent", "provider": "openrouter"},
                "gpt-4o-mini": {
                    "name": "gpt-4o-mini",
                    "provider": "openrouter",
                    "provider_config_overrides": {
                        "model": "openai/gpt-4o-mini",
                    },
                },
            },
        }
    )

    effective = build_runtime_config(
        config,
        agent_id="gpt-4o-mini",
        overrides=RunProfileOverrides(provider="anthropic"),
    )
    effective_profile = effective.agents["gpt-4o-mini"]

    assert effective_profile.provider == "anthropic"
    assert effective.providers.anthropic.model == config.providers.anthropic.model


def test_provider_config_overrides_reject_unknown_fields() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "default_agent": "hello-agent",
            "agents": {
                "hello-agent": {"name": "hello-agent", "provider": "openrouter"},
                "broken": {
                    "name": "broken",
                    "provider": "openrouter",
                    "provider_config_overrides": {
                        "not_a_real_field": "x",
                    },
                },
            },
        }
    )

    with pytest.raises(ConfigError, match="provider_config_overrides"):
        build_runtime_config(config, agent_id="broken")


def test_build_runtime_config_rejects_model_override_for_echo_provider() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "agents": {
                "hello-agent": {"name": "hello-agent", "provider": "echo"},
            },
        }
    )

    with pytest.raises(ConfigError) as exc:
        build_runtime_config(
            config,
            overrides=RunProfileOverrides(model="ignored-model"),
        )

    assert "echo provider" in str(exc.value)


def test_resolve_default_agent_id_returns_sole_entry() -> None:
    config = OpenMinionConfig.from_dict({"agents": {"alpha": {"provider": "echo"}}})
    assert resolve_default_agent_id(config) == "alpha"
