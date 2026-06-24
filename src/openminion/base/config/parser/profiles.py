"""Agent profile parsing helpers."""

from copy import deepcopy
from typing import Any

from openminion.base.config.base import ConfigError
from openminion.base.config.runtime.capability import (
    coerce_mode_runtime_policy_map,
    coerce_plugin_runtime_policy_config,
    coerce_provider_runtime_policy_config,
    coerce_thinking_runtime_policy_config,
)
from openminion.base.config.core import AgentProfileConfig
from openminion.base.config.parse import _as_bool
from openminion.base.config.skill_selection import (
    normalize_skill_catalog,
    normalize_skill_value,
)
from openminion.base.config.runtime.tools import coerce_tool_runtime_config
from openminion.base.config.mcp import coerce_mcp_exposure_config


def _parse_model_capability_overrides(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}

    parsed: dict[str, dict[str, Any]] = {}
    for raw_profile_id, raw_override in value.items():
        profile_id = str(raw_profile_id).strip()
        if not profile_id or not isinstance(raw_override, dict):
            continue
        parsed[profile_id] = deepcopy(raw_override)
    return parsed


def _parse_provider_config_overrides(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _parse_trailer_guidance_variant_map(
    raw: Any, *, field_path: str
) -> tuple[dict[str, str] | None, bool]:
    if raw is None:
        return None, False
    if isinstance(raw, dict):
        variant_map: dict[str, str] = {}
        for key, val in raw.items():
            lane = str(key or "").strip().lower()
            variant_name = str(val or "").strip()
            if lane and variant_name:
                variant_map[lane] = variant_name
        return variant_map, True
    raise ConfigError(
        f"{field_path} must be an object mapping lane names to variant names."
    )


def _parse_agent_profiles(value: Any) -> dict[str, AgentProfileConfig]:
    from .action import _build_action_policy_config

    if value is None:
        return {}
    if not isinstance(value, dict):
        return {}

    parsed: dict[str, AgentProfileConfig] = {}
    for agent_id, agent_config in value.items():
        if not isinstance(agent_config, dict):
            parsed[agent_id] = AgentProfileConfig()
            continue
        if "runtime_overrides" in agent_config:
            raise ConfigError(
                f"Nested 'runtime_overrides' under agents.{agent_id} is no longer supported. "
                f"Flatten to 'agents.{agent_id}.*'. "
                "See the config-shape migration guide."
            )

        kwargs: dict[str, Any] = dict(
            name=agent_config.get("name", agent_id),
            default_channel=agent_config.get("default_channel", ""),
            thinking=agent_config.get("thinking", ""),
            provider=agent_config.get("provider", ""),
            default_act_profile=agent_config.get("default_act_profile", ""),
            skill=normalize_skill_value(agent_config.get("skill")),
            skill_catalog=normalize_skill_catalog(agent_config.get("skill_catalog")),
            skill_explicit="skill" in agent_config,
            skill_catalog_explicit="skill_catalog" in agent_config,
            system_prompt=agent_config.get("system_prompt", ""),
            provider_config_overrides=_parse_provider_config_overrides(
                agent_config.get("provider_config_overrides")
            ),
            model_capability_overrides=_parse_model_capability_overrides(
                agent_config.get("model_capability_overrides")
            ),
            action_policy=(
                _build_action_policy_config(agent_config["action_policy"])
                if isinstance(agent_config.get("action_policy"), dict)
                else None
            ),
            thinking_policy=coerce_thinking_runtime_policy_config(
                agent_config.get("thinking_policy"),
                field_path=f"agents.{agent_id}.thinking_policy",
            ),
            provider_policy=coerce_provider_runtime_policy_config(
                agent_config.get("provider_policy"),
                field_path=f"agents.{agent_id}.provider_policy",
            ),
            modes=coerce_mode_runtime_policy_map(
                agent_config.get("modes"),
                field_path=f"agents.{agent_id}.modes",
            ),
            plugins=coerce_plugin_runtime_policy_config(
                agent_config.get("plugins"),
                field_path=f"agents.{agent_id}.plugins",
            ),
            tools=coerce_tool_runtime_config(agent_config.get("tools")),
            mcp_exposure=coerce_mcp_exposure_config(agent_config.get("mcp_exposure")),
        )
        if "tool_schema_shortlisting_enabled" in agent_config:
            kwargs["tool_schema_shortlisting_enabled"] = _as_bool(
                agent_config.get("tool_schema_shortlisting_enabled"), True
            )
            kwargs["has_tool_schema_shortlisting_enabled"] = True
        if "allow_background_write_authorization" in agent_config:
            kwargs["allow_background_write_authorization"] = _as_bool(
                agent_config.get("allow_background_write_authorization"), False
            )
            kwargs["has_allow_background_write_authorization"] = True
        if "trailer_guidance_variant" in agent_config:
            variant, has_variant = _parse_trailer_guidance_variant_map(
                agent_config.get("trailer_guidance_variant"),
                field_path=f"agents.{agent_id}.trailer_guidance_variant",
            )
            kwargs["trailer_guidance_variant"] = variant
            kwargs["has_trailer_guidance_variant"] = has_variant
        parsed[agent_id] = AgentProfileConfig(**kwargs)
    return parsed


__all__ = [
    "_parse_agent_profiles",
    "_parse_model_capability_overrides",
    "_parse_provider_config_overrides",
]
