"""Runtime profile resolution and override helpers."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any, Mapping

from openminion.base.config.base import ConfigError
from openminion.base.config.runtime.capability import (
    ModeRuntimeResolution,
    PluginRuntimeResolution,
    ProviderResolution,
    merge_tool_runtime_overrides,
    resolve_mode_runtime_policy,
    resolve_plugin_runtime_policy,
    resolve_provider_runtime_policy,
)
from openminion.base.config.runtime.reasoning import (
    REASONING_PROFILE_MINIMAL,
    RuntimeReasoningConfigResolution,
    resolve_runtime_reasoning_config,
)
from .profile_overrides import (
    PERMISSION_MODE_BYPASS,
    PERMISSION_MODE_CYCLE,
    PERMISSION_MODE_DEFAULT,
    PERMISSION_MODE_READONLY,
    PERMISSION_MODE_VALUES,
    RunProfileOverrides,
    _provider_config_field_name,
    combine_run_profile_overrides,
    next_permission_mode,
    run_profile_overrides_from_mapping,
)
from ..core import (
    AgentProfileConfig,
    OpenMinionConfig,
    _merge_trailer_guidance_variant,
    resolve_agent_config,
)


def resolve_runtime_profile(
    config: OpenMinionConfig,
    *,
    agent_id: str | None = None,
    overrides: RunProfileOverrides | None = None,
) -> AgentProfileConfig:
    """Return the effective :class:`AgentProfileConfig` for *agent_id*."""

    selected = resolve_agent_config(config, agent_id)
    effective_overrides = overrides or RunProfileOverrides()
    provider_resolution = resolve_provider_runtime_policy(
        system_policy=config.runtime.provider_policy,
        agent_policy=selected.provider_policy,
        code_default_provider="echo",
        legacy_agent_provider=str(selected.provider or ""),
        invocation_provider=effective_overrides.provider,
    )
    mode_resolution = resolve_mode_runtime_policy(
        system_modes=config.runtime.modes,
        agent_modes=selected.modes,
    )
    thinking_resolution = resolve_runtime_reasoning_config(
        code_default_profile=REASONING_PROFILE_MINIMAL,
        system_profile=_thinking_policy_profile(config.runtime.thinking_policy),
        agent_profile=_selected_agent_thinking_profile(selected),
        invocation_requested_profile=effective_overrides.thinking or None,
        provider_name=provider_resolution.selected_provider,
        model_name=effective_overrides.model,
    )
    merged_tools = merge_tool_runtime_overrides(
        system_tools=config.runtime.tools,
        agent_tools=selected.tools,
    )
    return replace(
        selected,
        provider=provider_resolution.selected_provider,
        system_prompt=effective_overrides.system_prompt or selected.system_prompt,
        thinking=thinking_resolution.reasoning_profile,
        modes=mode_resolution.effective_modes,
        tools=merged_tools,
    )


def build_runtime_config(
    config: OpenMinionConfig,
    *,
    agent_id: str | None = None,
    overrides: RunProfileOverrides | None = None,
) -> OpenMinionConfig:
    effective_overrides = overrides or RunProfileOverrides()
    selected_profile = resolve_agent_config(config, agent_id)
    selected_agent_id = str(agent_id or "").strip()
    effective_profile = resolve_runtime_profile(
        config,
        agent_id=agent_id,
        overrides=effective_overrides,
    )
    plugin_resolution = resolve_plugin_runtime_policy(
        compatibility_enabled_plugins=list(config.enabled_plugins),
        system_policy=config.runtime.plugins,
    )
    merged_tss_value, merged_tss_has = _pick_flag(
        agent_has=effective_profile.has_tool_schema_shortlisting_enabled,
        agent_value=effective_profile.tool_schema_shortlisting_enabled,
        runtime_has=config.runtime.has_tool_schema_shortlisting_enabled,
        runtime_value=config.runtime.tool_schema_shortlisting_enabled,
    )
    merged_bwa_value, merged_bwa_has = _pick_flag(
        agent_has=effective_profile.has_allow_background_write_authorization,
        agent_value=effective_profile.allow_background_write_authorization,
        runtime_has=config.runtime.has_allow_background_write_authorization,
        runtime_value=config.runtime.allow_background_write_authorization,
    )
    merged_variant, merged_variant_has = _merge_trailer_guidance_variant(
        agent_variant=effective_profile.trailer_guidance_variant,
        runtime_variant=config.runtime.trailer_guidance_variant,
        agent_set=effective_profile.has_trailer_guidance_variant,
        runtime_set=config.runtime.has_trailer_guidance_variant,
    )
    new_agents = dict(config.agents)
    effective_id = selected_agent_id or str(effective_profile.name or "").strip()
    if effective_id and effective_id in new_agents:
        new_agents[effective_id] = effective_profile
    effective_config = replace(
        config,
        default_agent=(
            selected_agent_id
            if selected_agent_id and selected_agent_id in new_agents
            else config.default_agent
        ),
        agents=new_agents,
        runtime=replace(
            config.runtime,
            tool_schema_shortlisting_enabled=merged_tss_value,
            has_tool_schema_shortlisting_enabled=merged_tss_has,
            allow_background_write_authorization=merged_bwa_value,
            has_allow_background_write_authorization=merged_bwa_has,
            trailer_guidance_variant=(
                dict(merged_variant or {}) if merged_variant_has else None
            ),
            has_trailer_guidance_variant=merged_variant_has,
            tools=effective_profile.tools,
        ),
        enabled_plugins=list(plugin_resolution.effective_enabled),
    )

    provider_config_overrides = _select_profile_provider_config_overrides(
        selected_profile,
        effective_profile,
        effective_overrides,
    )
    effective_model_override = (
        effective_overrides.model
        or str(provider_config_overrides.get("model", "") or "").strip()
    )
    if not provider_config_overrides and not effective_model_override:
        return effective_config

    provider_field = _provider_config_field_name(effective_profile.provider)
    if not provider_field:
        raise ConfigError(
            "Runtime model overrides are not supported for the echo provider."
        )
    provider_config = getattr(config.providers, provider_field, None)
    if provider_config is None:
        raise ConfigError(
            f"Runtime model override is not supported for provider {effective_profile.provider!r}."
        )
    provider_patch = dict(provider_config_overrides)
    if effective_model_override:
        provider_patch["model"] = effective_model_override
    updated_provider = _apply_provider_config_patch(
        provider_name=effective_profile.provider,
        provider_config=provider_config,
        patch=provider_patch,
    )
    updated_providers = replace(config.providers, **{provider_field: updated_provider})
    return replace(effective_config, providers=updated_providers)


def build_capability_runtime_diagnostics(
    config: OpenMinionConfig,
    *,
    agent_id: str | None = None,
    overrides: RunProfileOverrides | None = None,
) -> dict[str, Any]:
    selected = resolve_agent_config(config, agent_id)
    effective_overrides = overrides or RunProfileOverrides()
    provider_resolution = resolve_provider_runtime_policy(
        system_policy=config.runtime.provider_policy,
        agent_policy=selected.provider_policy,
        code_default_provider="echo",
        legacy_agent_provider=str(selected.provider or ""),
        invocation_provider=effective_overrides.provider,
    )
    mode_resolution = resolve_mode_runtime_policy(
        system_modes=config.runtime.modes,
        agent_modes=selected.modes,
    )
    plugin_resolution = resolve_plugin_runtime_policy(
        compatibility_enabled_plugins=list(config.enabled_plugins),
        system_policy=config.runtime.plugins,
    )
    thinking_resolution = resolve_runtime_reasoning_config(
        code_default_profile=REASONING_PROFILE_MINIMAL,
        system_profile=_thinking_policy_profile(config.runtime.thinking_policy),
        agent_profile=_selected_agent_thinking_profile(selected),
        invocation_requested_profile=effective_overrides.thinking or None,
        provider_name=provider_resolution.selected_provider,
        model_name=effective_overrides.model,
    )
    from openminion.base.config.runtime.tools import tool_runtime_config_to_dict

    merged_tools = merge_tool_runtime_overrides(
        system_tools=config.runtime.tools,
        agent_tools=selected.tools,
    )
    return {
        "provider": _provider_resolution_to_dict(provider_resolution),
        "thinking": _thinking_resolution_to_dict(thinking_resolution),
        "modes": _mode_resolution_to_dict(mode_resolution),
        "plugins": _plugin_resolution_to_dict(plugin_resolution),
        "brain": _brain_diagnostic_payload(selected, config.runtime),
        "tools": tool_runtime_config_to_dict(merged_tools),
    }


def _brain_diagnostic_payload(
    selected: AgentProfileConfig,
    runtime: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field_name in (
        "tool_schema_shortlisting_enabled",
        "allow_background_write_authorization",
    ):
        value, present = _pick_flag(
            agent_has=getattr(selected, f"has_{field_name}"),
            agent_value=getattr(selected, field_name),
            runtime_has=getattr(runtime, f"has_{field_name}"),
            runtime_value=getattr(runtime, field_name),
        )
        if present:
            payload[field_name] = bool(value)
    merged, present = _merge_trailer_guidance_variant(
        agent_variant=selected.trailer_guidance_variant,
        runtime_variant=runtime.trailer_guidance_variant,
        agent_set=selected.has_trailer_guidance_variant,
        runtime_set=runtime.has_trailer_guidance_variant,
    )
    if present:
        payload["trailer_guidance_variant"] = merged
    return payload


def _pick_flag(
    *,
    agent_has: bool,
    agent_value: Any,
    runtime_has: bool,
    runtime_value: Any,
) -> tuple[Any, bool]:
    if agent_has:
        return agent_value, True
    if runtime_has:
        return runtime_value, True
    return None, False


def _select_profile_provider_config_overrides(
    selected_profile: AgentProfileConfig,
    effective_profile: AgentProfileConfig,
    effective_overrides: RunProfileOverrides,
) -> dict[str, Any]:
    if effective_overrides.provider:
        return {}
    selected_provider = str(selected_profile.provider or "").strip().lower()
    effective_provider = str(effective_profile.provider or "").strip().lower()
    if not selected_provider or selected_provider != effective_provider:
        return {}
    return dict(getattr(selected_profile, "provider_config_overrides", {}) or {})


def _apply_provider_config_patch(
    *,
    provider_name: str,
    provider_config: Any,
    patch: Mapping[str, Any],
) -> Any:
    if not patch:
        return provider_config
    allowed_fields = set(asdict(provider_config))
    unknown_fields = sorted(set(patch) - allowed_fields)
    if unknown_fields:
        valid = ", ".join(sorted(allowed_fields))
        raise ConfigError(
            f"Unsupported provider_config_overrides for provider {provider_name!r}: "
            f"{unknown_fields!r}. Valid fields: {valid}."
        )
    return replace(provider_config, **dict(patch))


def _thinking_policy_profile(policy: Any | None) -> str | None:
    return str(getattr(policy, "reasoning_profile", "") or "").strip() or None


def _selected_agent_thinking_profile(selected: AgentProfileConfig) -> str | None:
    legacy_profile = str(selected.thinking or "").strip() or None
    return _thinking_policy_profile(selected.thinking_policy) or legacy_profile


def _provider_resolution_to_dict(resolution: ProviderResolution) -> dict[str, Any]:
    return {
        "selected": resolution.selected_provider,
        "source": resolution.source,
        "effective_enabled": list(resolution.effective_enabled),
        "provider_order": list(resolution.provider_order),
        "system_default_provider": resolution.system_default_provider,
        "agent_default_provider": resolution.agent_default_provider,
        "invocation_requested_provider": resolution.invocation_requested_provider,
    }


def _thinking_resolution_to_dict(
    resolution: RuntimeReasoningConfigResolution,
) -> dict[str, Any]:
    return {
        "code_default_profile": REASONING_PROFILE_MINIMAL,
        "system_profile": resolution.system_profile,
        "agent_profile": resolution.agent_profile,
        "invocation_requested_profile": resolution.requested_profile,
        "effective": resolution.diagnostics_payload(),
    }


def _mode_resolution_to_dict(resolution: ModeRuntimeResolution) -> dict[str, Any]:
    return {
        "effective": {
            name: {"enabled": bool(config.enabled)}
            for name, config in sorted(resolution.effective_modes.items())
        },
        "blocked_reasons": dict(resolution.blocked_reasons),
    }


def _plugin_resolution_to_dict(resolution: PluginRuntimeResolution) -> dict[str, Any]:
    return {
        "effective_enabled": list(resolution.effective_enabled),
        "blocked": list(resolution.blocked),
        "source": resolution.source,
    }


__all__ = [
    "PERMISSION_MODE_BYPASS",
    "PERMISSION_MODE_CYCLE",
    "PERMISSION_MODE_DEFAULT",
    "PERMISSION_MODE_READONLY",
    "PERMISSION_MODE_VALUES",
    "RunProfileOverrides",
    "build_capability_runtime_diagnostics",
    "build_runtime_config",
    "combine_run_profile_overrides",
    "next_permission_mode",
    "resolve_runtime_profile",
    "run_profile_overrides_from_mapping",
]
