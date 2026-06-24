from __future__ import annotations

import pytest

from openminion.base.config import (
    ConfigError,
    OpenMinionConfig,
    RunProfileOverrides,
    build_capability_runtime_diagnostics,
    build_runtime_config,
    resolve_runtime_profile,
)


def test_parser_accepts_canonical_system_runtime_and_agent_runtime_overrides() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "system": {
                "runtime": {
                    "provider_policy": {
                        "enabled": ["openrouter", "echo"],
                        "provider_order": ["openrouter", "echo"],
                    },
                    "modes": {
                        "coding": {"enabled": True},
                        "delegate": {"enabled": False},
                    },
                    "plugins": {
                        "enabled": ["validate"],
                        "blocked": ["experimental-pack"],
                    },
                    "tools": {
                        "browser": {
                            "enabled_providers": ["playwright"],
                            "default_provider": "playwright",
                            "provider_order": ["playwright"],
                        }
                    },
                },
                "providers": {
                    "openrouter": {"model": "anthropic/claude-haiku-4.5"},
                },
            },
            "agents": {
                "hello-agent": {
                    "name": "hello-agent",
                    "provider": "echo",
                    "provider_policy": {"default_provider": "openrouter"},
                    "modes": {"coding": {"enabled": True}},
                    "tools": {
                        "browser": {
                            "default_provider": "playwright",
                        }
                    },
                }
            },
        }
    )

    assert config.runtime.provider_policy is not None
    assert config.runtime.provider_policy.enabled == ["openrouter", "echo"]
    assert config.runtime.modes["delegate"].enabled is False
    assert config.runtime.plugins is not None
    assert config.runtime.plugins.blocked == ["experimental-pack"]

    profile = config.agents["hello-agent"]
    assert profile.provider_policy is not None
    assert profile.provider_policy.default_provider == "openrouter"
    assert profile.modes["coding"].enabled is True
    assert config.providers.openrouter.model == "anthropic/claude-haiku-4.5"

    payload = config.to_dict()
    assert payload["system"]["runtime"]["provider_policy"]["enabled"] == [
        "openrouter",
        "echo",
    ]
    assert (
        payload["agents"]["hello-agent"]["provider_policy"]["default_provider"]
        == "openrouter"
    )


def test_parser_round_trips_provider_config_overrides_for_agent_and_profile() -> None:
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
                    "provider": "openrouter",
                    "provider_config_overrides": {
                        "model": "openai/gpt-4o-mini",
                        "tool_call_strategy": "hybrid",
                    },
                },
            },
        }
    )

    payload = config.to_dict()
    assert (
        payload["agents"]["hello-agent"]["provider_config_overrides"]["model"]
        == "anthropic/claude-haiku-4.5"
    )
    assert (
        payload["agents"]["gpt-4o-mini"]["provider_config_overrides"]["model"]
        == "openai/gpt-4o-mini"
    )
    assert (
        payload["agents"]["gpt-4o-mini"]["provider_config_overrides"][
            "tool_call_strategy"
        ]
        == "hybrid"
    )


def test_system_provider_first_enabled_becomes_effective_default() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "system": {
                "runtime": {
                    "provider_policy": {
                        "enabled": ["openrouter", "echo"],
                    }
                }
            },
            "agents": {
                "hello-agent": {
                    "name": "hello-agent",
                    "provider": "",
                }
            },
        }
    )

    profile = resolve_runtime_profile(config)

    assert profile.provider == "openrouter"
    diagnostics = build_capability_runtime_diagnostics(config)
    assert diagnostics["provider"]["selected"] == "openrouter"
    assert diagnostics["provider"]["source"] == "system_runtime"


def test_request_override_cannot_resurrect_system_disabled_provider() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "system": {
                "runtime": {
                    "provider_policy": {
                        "enabled": ["echo"],
                        "default_provider": "echo",
                    }
                }
            },
            "agents": {"only-agent": {"provider": "echo"}},
        }
    )

    with pytest.raises(
        ConfigError, match="blocked by the effective provider allowlist"
    ):
        resolve_runtime_profile(
            config,
            overrides=RunProfileOverrides(provider="openrouter"),
        )


def test_agent_tool_runtime_override_may_narrow_but_not_resurrect() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "runtime": {
                "tools": {
                    "browser": {
                        "enabled_providers": ["playwright"],
                        "default_provider": "playwright",
                        "provider_order": ["playwright"],
                    }
                }
            },
            "agents": {
                "only-agent": {
                    "tools": {
                        "browser": {
                            "default_provider": "pinchtab",
                        }
                    }
                }
            },
        }
    )

    with pytest.raises(ConfigError, match="tools.browser.default_provider"):
        build_runtime_config(config)


def test_agent_tool_runtime_override_can_narrow_enabled_provider_set() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "runtime": {
                "tools": {
                    "search": {
                        "enabled_providers": ["brave", "tavily"],
                        "default_provider": "brave",
                        "provider_order": ["brave", "tavily"],
                    }
                }
            },
            "agents": {
                "only-agent": {
                    "tools": {
                        "search": {
                            "enabled_providers": ["tavily"],
                            "default_provider": "tavily",
                            "provider_order": ["tavily"],
                        }
                    }
                }
            },
        }
    )

    effective = build_runtime_config(config)

    search_cfg = effective.runtime.tools.search
    assert search_cfg is not None
    assert search_cfg.enabled_providers == ["tavily"]
    assert search_cfg.default_provider == "tavily"


def test_runtime_brain_tss_default_falls_back_to_code_default() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "agents": {
                "hello-agent": {
                    "name": "hello-agent",
                    "provider": "echo",
                }
            },
        }
    )

    effective = build_runtime_config(config)

    assert effective.runtime.tool_schema_shortlisting_enabled is None
    assert effective.runtime.has_tool_schema_shortlisting_enabled is False


def test_runtime_brain_tss_global_override_round_trips() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "runtime": {
                "tool_schema_shortlisting_enabled": False,
            },
            "agents": {
                "hello-agent": {
                    "name": "hello-agent",
                    "provider": "echo",
                }
            },
        }
    )

    effective = build_runtime_config(config)
    payload = config.to_dict()

    assert effective.runtime.tool_schema_shortlisting_enabled is False
    assert payload["runtime"]["tool_schema_shortlisting_enabled"] is False


def test_runtime_brain_tss_agent_override_can_enable_over_global_disabled() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "runtime": {
                "tool_schema_shortlisting_enabled": False,
            },
            "agents": {
                "minimax-m2-7": {
                    "tool_schema_shortlisting_enabled": True,
                }
            },
        }
    )

    effective = build_runtime_config(config, agent_id="minimax-m2-7")

    assert effective.runtime.tool_schema_shortlisting_enabled is True
    assert effective.agents["minimax-m2-7"].tool_schema_shortlisting_enabled is True


def test_runtime_brain_tss_agent_override_can_disable_over_global_enabled() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "runtime": {
                "tool_schema_shortlisting_enabled": True,
            },
            "agents": {
                "minimax-m2-7": {
                    "tool_schema_shortlisting_enabled": False,
                }
            },
        }
    )

    effective = build_runtime_config(config, agent_id="minimax-m2-7")

    assert effective.runtime.tool_schema_shortlisting_enabled is False
    assert effective.agents["minimax-m2-7"].tool_schema_shortlisting_enabled is False


def test_runtime_brain_background_write_authorization_agent_override() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "runtime": {
                "allow_background_write_authorization": False,
            },
            "agents": {
                "watch-agent": {
                    "allow_background_write_authorization": True,
                }
            },
        }
    )

    effective = build_runtime_config(config, agent_id="watch-agent")

    assert effective.runtime.allow_background_write_authorization is True
    assert effective.agents["watch-agent"].allow_background_write_authorization is True


def test_parser_accepts_runtime_thinking_policy_and_agent_override() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "system": {
                "runtime": {
                    "thinking_policy": {
                        "reasoning_profile": "detailed",
                    }
                }
            },
            "agents": {
                "hello-agent": {
                    "thinking": "minimal",
                    "thinking_policy": {
                        "reasoning_profile": "off",
                    },
                }
            },
        }
    )

    assert config.runtime.thinking_policy is not None
    assert config.runtime.thinking_policy.reasoning_profile == "detailed"
    profile = config.agents["hello-agent"]
    assert profile.thinking_policy is not None
    assert profile.thinking_policy.reasoning_profile == "off"

    payload = config.to_dict()
    assert (
        payload["system"]["runtime"]["thinking_policy"]["reasoning_profile"]
        == "detailed"
    )
    assert (
        payload["agents"]["hello-agent"]["thinking_policy"]["reasoning_profile"]
        == "off"
    )


def test_runtime_profile_thinking_request_override_is_reflected_in_diagnostics() -> (
    None
):
    config = OpenMinionConfig.from_dict(
        {
            "system": {
                "runtime": {
                    "thinking_policy": {
                        "reasoning_profile": "off",
                    }
                }
            },
            "agents": {
                "hello-agent": {
                    "thinking": "minimal",
                }
            },
        }
    )

    profile = resolve_runtime_profile(
        config,
        overrides=RunProfileOverrides(thinking="detailed"),
    )
    diagnostics = build_capability_runtime_diagnostics(
        config,
        overrides=RunProfileOverrides(thinking="detailed"),
    )

    assert profile.thinking == "detailed"
    assert diagnostics["thinking"]["system_profile"] == "off"
    assert diagnostics["thinking"]["agent_profile"] == "minimal"
    assert diagnostics["thinking"]["invocation_requested_profile"] == "detailed"
    assert diagnostics["thinking"]["effective"]["reasoning_profile"] == "detailed"
    assert diagnostics["thinking"]["effective"]["source_layer"] == "invocation_override"
