from __future__ import annotations

import pytest

from openminion.base.config import (
    ConfigError,
    OpenMinionConfig,
    RunProfileOverrides,
    build_capability_runtime_diagnostics,
    resolve_runtime_profile,
)


def test_agent_runtime_provider_override_selects_system_allowed_provider() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "system": {
                "runtime": {
                    "provider_policy": {
                        "enabled": ["echo", "openrouter"],
                        "default_provider": "echo",
                        "provider_order": ["echo", "openrouter"],
                    }
                }
            },
            "agents": {
                "openminion": {
                    "name": "openminion",
                    "provider": "echo",
                    "provider_policy": {
                        "default_provider": "openrouter",
                    },
                },
            },
            "default_agent": "openminion",
        }
    )

    profile = resolve_runtime_profile(config)
    diagnostics = build_capability_runtime_diagnostics(config)

    assert profile.provider == "openrouter"
    assert diagnostics["provider"]["selected"] == "openrouter"
    assert diagnostics["provider"]["source"] == "agent_runtime"


def test_request_provider_override_is_blocked_when_system_disables_provider() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "system": {
                "runtime": {
                    "provider_policy": {
                        "enabled": ["echo"],
                    }
                }
            },
            "agents": {"openminion": {"name": "openminion", "provider": "echo"}},
            "default_agent": "openminion",
        }
    )

    with pytest.raises(ConfigError, match="invocation override requested provider"):
        resolve_runtime_profile(
            config,
            overrides=RunProfileOverrides(provider="openrouter"),
        )


def test_system_default_is_inherited_when_agent_has_no_provider_preference() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "system": {
                "runtime": {
                    "provider_policy": {
                        "enabled": ["openrouter", "echo"],
                        "default_provider": "openrouter",
                    }
                }
            },
            "agents": {"openminion": {"name": "openminion", "provider": ""}},
            "default_agent": "openminion",
        }
    )

    profile = resolve_runtime_profile(config)

    assert profile.provider == "openrouter"
