from __future__ import annotations

import pytest

from openminion.base.config import (
    AgentProfileConfig,
    ConfigError,
    OpenMinionConfig,
    RuntimeConfig,
    UnknownProfileError,
    resolve_agent_config,
    resolve_default_agent_id,
)
from openminion.base.config.core import ConfigValidationError


def test_single_agent_resolves_without_default_agent() -> None:
    config = OpenMinionConfig(
        agents={"lone": AgentProfileConfig(name="lone", provider="echo")}
    )
    assert resolve_default_agent_id(config) == "lone"


def test_empty_agents_catalog_raises_config_validation_error() -> None:
    config = OpenMinionConfig()
    assert not config.agents
    with pytest.raises(ConfigValidationError):
        resolve_default_agent_id(config)


def test_multi_agent_without_default_agent_raises_with_valid_ids() -> None:
    config = OpenMinionConfig(
        agents={
            "alpha": AgentProfileConfig(name="alpha", provider="echo"),
            "beta": AgentProfileConfig(name="beta", provider="echo"),
        }
    )
    with pytest.raises(UnknownProfileError) as excinfo:
        resolve_default_agent_id(config)
    message = str(excinfo.value)
    assert "'alpha'" in message
    assert "'beta'" in message


def test_default_agent_pointing_at_missing_id_raises() -> None:
    config = OpenMinionConfig(
        default_agent="ghost",
        agents={
            "alpha": AgentProfileConfig(name="alpha", provider="echo"),
            "beta": AgentProfileConfig(name="beta", provider="echo"),
        },
    )
    with pytest.raises(UnknownProfileError) as excinfo:
        resolve_default_agent_id(config)
    assert "ghost" in str(excinfo.value)


def test_default_agent_pointing_at_present_id_wins() -> None:
    config = OpenMinionConfig(
        default_agent="beta",
        agents={
            "alpha": AgentProfileConfig(name="alpha"),
            "beta": AgentProfileConfig(name="beta"),
        },
    )
    assert resolve_default_agent_id(config) == "beta"


def test_runtime_only_tss_propagates_to_resolved_profile() -> None:
    config = OpenMinionConfig(
        runtime=RuntimeConfig(
            tool_schema_shortlisting_enabled=True,
            has_tool_schema_shortlisting_enabled=True,
        ),
        agents={"agent-one": AgentProfileConfig(name="agent-one")},
    )
    profile = resolve_agent_config(config)
    assert profile.has_tool_schema_shortlisting_enabled is True
    assert profile.tool_schema_shortlisting_enabled is True


def test_agent_only_tss_does_not_leak_to_runtime() -> None:
    config = OpenMinionConfig(
        agents={
            "agent-one": AgentProfileConfig(
                name="agent-one",
                tool_schema_shortlisting_enabled=True,
                has_tool_schema_shortlisting_enabled=True,
            ),
        }
    )
    profile = resolve_agent_config(config)
    assert profile.tool_schema_shortlisting_enabled is True
    assert profile.has_tool_schema_shortlisting_enabled is True


def test_agent_value_wins_over_runtime_value() -> None:
    config = OpenMinionConfig(
        runtime=RuntimeConfig(
            tool_schema_shortlisting_enabled=False,
            has_tool_schema_shortlisting_enabled=True,
        ),
        agents={
            "agent-one": AgentProfileConfig(
                name="agent-one",
                tool_schema_shortlisting_enabled=True,
                has_tool_schema_shortlisting_enabled=True,
            ),
        },
    )
    profile = resolve_agent_config(config)
    assert profile.tool_schema_shortlisting_enabled is True


def test_trailer_guidance_variant_shallow_merges_per_key() -> None:
    config = OpenMinionConfig(
        runtime=RuntimeConfig(
            trailer_guidance_variant={
                "apd": "verbose_weak_model",
                "macc": "terse_strong_model",
            },
            has_trailer_guidance_variant=True,
        ),
        agents={
            "researcher": AgentProfileConfig(
                name="researcher",
                trailer_guidance_variant={"apd": "terse_strong_model"},
                has_trailer_guidance_variant=True,
            ),
        },
    )
    profile = resolve_agent_config(config)
    assert profile.has_trailer_guidance_variant is True
    assert profile.trailer_guidance_variant == {
        "apd": "terse_strong_model",
        "macc": "terse_strong_model",
    }


def test_parser_rejects_legacy_agent_block_with_migration_pointer() -> None:
    with pytest.raises(ConfigError) as excinfo:
        OpenMinionConfig.from_dict({"agent": {"name": "x", "provider": "echo"}})
    message = str(excinfo.value)
    assert "agent" in message
    assert "config-shape-migration-2026.md" in message


def test_parser_rejects_nested_runtime_overrides_under_agents_with_pointer() -> None:
    with pytest.raises(ConfigError) as excinfo:
        OpenMinionConfig.from_dict(
            {
                "agents": {
                    "x": {
                        "runtime_overrides": {"brain": {}},
                    }
                }
            }
        )
    message = str(excinfo.value)
    assert "runtime_overrides" in message
    assert "config-shape-migration-2026.md" in message


def test_parser_rejects_nested_runtime_brain_block_with_pointer() -> None:
    with pytest.raises(ConfigError) as excinfo:
        OpenMinionConfig.from_dict(
            {
                "agents": {"x": {"provider": "echo"}},
                "runtime": {"brain": {"tool_schema_shortlisting_enabled": True}},
            }
        )
    message = str(excinfo.value)
    assert "runtime.brain" in message
    assert "config-shape-migration-2026.md" in message


def test_parser_rejects_runtime_thinking_dict_with_pointer() -> None:
    with pytest.raises(ConfigError) as excinfo:
        OpenMinionConfig.from_dict(
            {
                "agents": {"x": {"provider": "echo"}},
                "runtime": {"thinking": {"reasoning_profile": "detailed"}},
            }
        )
    message = str(excinfo.value)
    assert "runtime.thinking" in message
    assert "config-shape-migration-2026.md" in message


def test_parser_rejects_runtime_providers_dict_with_pointer() -> None:
    with pytest.raises(ConfigError) as excinfo:
        OpenMinionConfig.from_dict(
            {
                "agents": {"x": {"provider": "echo"}},
                "runtime": {"providers": {"enabled": ["echo"]}},
            }
        )
    message = str(excinfo.value)
    assert "runtime.providers" in message
    assert "config-shape-migration-2026.md" in message
