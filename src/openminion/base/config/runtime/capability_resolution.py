"""Provider, mode, plugin, and tool runtime policy resolution."""

from __future__ import annotations

from typing import Mapping

from openminion.base.config.base import ConfigError
from openminion.base.config.runtime.tools import (
    ToolFamilyRuntimeConfig,
    ToolRuntimeConfig,
    coerce_tool_runtime_config,
)

from .capability import (
    _MODE_BOOL_FIELDS,
    _MODE_INT_BOUNDS,
    ModeRuntimePolicyConfig,
    ModeRuntimeResolution,
    PluginRuntimePolicyConfig,
    PluginRuntimeResolution,
    ProviderResolution,
    ProviderRuntimePolicyConfig,
    _normalize_token,
)


def _policy_default(
    policy: ProviderRuntimePolicyConfig | None,
    enabled: list[str],
    has_enabled: bool,
) -> str:
    if policy is None:
        return ""
    return policy.default_provider or (enabled[0] if has_enabled and enabled else "")


def resolve_provider_runtime_policy(
    *,
    system_policy: ProviderRuntimePolicyConfig | None,
    agent_policy: ProviderRuntimePolicyConfig | None,
    code_default_provider: str,
    legacy_agent_provider: str = "",
    invocation_provider: str = "",
) -> ProviderResolution:
    code_default = _normalize_token(code_default_provider) or "echo"
    legacy_agent = _normalize_token(legacy_agent_provider)
    invocation = _normalize_token(invocation_provider)
    system_enabled = list(system_policy.enabled) if system_policy else []
    agent_enabled = list(agent_policy.enabled) if agent_policy else []
    system_has_enabled = bool(system_policy and system_policy.has_enabled)
    agent_has_enabled = bool(agent_policy and agent_policy.has_enabled)
    if system_has_enabled and agent_has_enabled:
        extra = [item for item in agent_enabled if item not in system_enabled]
        if extra:
            raise ConfigError(f"Agent providers exceed system providers: {extra!r}.")
    enabled = agent_enabled if agent_has_enabled else system_enabled
    system_default = _policy_default(system_policy, system_enabled, system_has_enabled)
    agent_default = _policy_default(agent_policy, agent_enabled, agent_has_enabled)
    for label, candidate in (
        ("agent.provider", legacy_agent),
        ("agent runtime override", agent_default),
        ("invocation override", invocation),
    ):
        if candidate and enabled and candidate not in enabled:
            raise ConfigError(
                f"{label} requested provider {candidate!r}, but it is blocked by "
                f"the effective provider allowlist {enabled!r}."
            )
    provider_order = _resolve_effective_provider_order(
        system_policy=system_policy,
        agent_policy=agent_policy,
        effective_enabled=enabled,
    )
    candidates = (
        ("system_runtime", system_default),
        ("agent_runtime", legacy_agent),
        ("agent_runtime", agent_default),
        ("invocation_override", invocation),
    )
    fallback = (
        (enabled[0], "system_runtime" if system_has_enabled else "agent_runtime")
        if enabled
        else (code_default, "capability_definition")
    )
    selected, source = next(
        ((provider, layer) for layer, provider in reversed(candidates) if provider),
        fallback,
    )
    if enabled and selected not in enabled:
        selected = enabled[0]
        source = "system_runtime" if system_has_enabled else "agent_runtime"
    return ProviderResolution(
        selected_provider=selected,
        source=source,
        effective_enabled=tuple(enabled),
        provider_order=tuple(provider_order),
        system_default_provider=system_default,
        agent_default_provider=agent_default or legacy_agent,
        invocation_requested_provider=invocation,
    )


def _resolve_effective_provider_order(
    *,
    system_policy: ProviderRuntimePolicyConfig | None,
    agent_policy: ProviderRuntimePolicyConfig | None,
    effective_enabled: list[str],
) -> list[str]:
    allowed = set(effective_enabled)
    if agent_policy is not None and agent_policy.has_provider_order:
        extra = [
            item
            for item in agent_policy.provider_order
            if allowed and item not in allowed
        ]
        if system_policy is not None and extra:
            raise ConfigError(
                "Agent runtime override providers.provider_order cannot exceed the "
                f"effective provider allowlist: {extra!r}."
            )
        ordered = list(agent_policy.provider_order)
    elif system_policy is not None and system_policy.has_provider_order:
        ordered = list(system_policy.provider_order)
    else:
        ordered = list(effective_enabled)
    return [item for item in ordered if not allowed or item in allowed]


def merge_tool_runtime_overrides(
    *,
    system_tools: ToolRuntimeConfig | None,
    agent_tools: ToolRuntimeConfig | None,
) -> ToolRuntimeConfig:
    system = coerce_tool_runtime_config(system_tools)
    agent = coerce_tool_runtime_config(agent_tools)
    return ToolRuntimeConfig(
        **{
            name: _merge_tool_family_runtime_overrides(
                family_name=name,
                system_family=getattr(system, name),
                agent_family=getattr(agent, name),
            )
            for name in ("search", "fetch", "browser", "weather")
        }
    )


def _merge_tool_family_runtime_overrides(
    *,
    family_name: str,
    system_family: ToolFamilyRuntimeConfig | None,
    agent_family: ToolFamilyRuntimeConfig | None,
) -> ToolFamilyRuntimeConfig | None:
    if system_family is None or agent_family is None:
        return agent_family or system_family
    system_enabled = list(system_family.enabled_providers)
    agent_enabled = list(agent_family.enabled_providers)
    if system_enabled and agent_enabled:
        extra = [item for item in agent_enabled if item not in system_enabled]
        if extra:
            raise ConfigError(
                f"agent runtime override tools.{family_name}.enabled_providers cannot "
                f"exceed runtime.tools.{family_name}.enabled_providers: {extra!r}."
            )
    enabled = agent_enabled or system_enabled
    default = agent_family.default_provider or system_family.default_provider
    if enabled and default and default not in enabled:
        raise ConfigError(
            f"agent runtime override tools.{family_name}.default_provider={default!r} "
            f"is blocked by the effective enabled_providers {enabled!r}."
        )
    order = list(agent_family.provider_order or system_family.provider_order)
    extra = [item for item in order if enabled and item not in enabled]
    if extra:
        raise ConfigError(
            f"agent runtime override tools.{family_name}.provider_order cannot exceed "
            f"the effective enabled_providers: {extra!r}."
        )
    fallback = (
        agent_family.allow_fallback
        if agent_family.allow_fallback is not None
        else system_family.allow_fallback
    )
    if system_family.allow_fallback is False and agent_family.allow_fallback is True:
        raise ConfigError(
            f"agent runtime override tools.{family_name}.allow_fallback=true cannot "
            f"override runtime.tools.{family_name}.allow_fallback=false."
        )
    return ToolFamilyRuntimeConfig(enabled, default, order, fallback)


def _normalized_modes(
    values: Mapping[str, ModeRuntimePolicyConfig] | None,
) -> dict[str, ModeRuntimePolicyConfig]:
    return {
        str(name).strip().lower(): config
        for name, config in (values or {}).items()
        if str(name).strip()
    }


def resolve_mode_runtime_policy(
    *,
    system_modes: Mapping[str, ModeRuntimePolicyConfig] | None,
    agent_modes: Mapping[str, ModeRuntimePolicyConfig] | None,
) -> ModeRuntimeResolution:
    system = _normalized_modes(system_modes)
    agent = _normalized_modes(agent_modes)
    fields_to_merge = (*_MODE_BOOL_FIELDS, *_MODE_INT_BOUNDS)
    effective: dict[str, ModeRuntimePolicyConfig] = {}
    blocked: dict[str, str] = {}
    for name in sorted(set(system) | set(agent)):
        system_entry = system.get(name)
        agent_entry = agent.get(name)
        if system_entry is not None and not system_entry.enabled:
            effective[name] = ModeRuntimePolicyConfig(
                enabled=False,
                **{field: getattr(system_entry, field) for field in fields_to_merge},
            )
            if agent_entry is not None and agent_entry.enabled:
                blocked[name] = (
                    f"agent runtime override requested mode {name!r}, but "
                    f"system.runtime.modes.{name}.enabled=false."
                )
            continue
        owner = agent_entry or system_entry
        if owner is None:
            continue
        effective[name] = ModeRuntimePolicyConfig(
            enabled=owner.enabled,
            **{
                field: (
                    getattr(agent_entry, field)
                    if agent_entry is not None
                    and getattr(agent_entry, field) is not None
                    else getattr(system_entry, field)
                    if system_entry is not None
                    else None
                )
                for field in fields_to_merge
            },
        )
    return ModeRuntimeResolution(effective, blocked)


def resolve_plugin_runtime_policy(
    *,
    compatibility_enabled_plugins: list[str] | tuple[str, ...],
    system_policy: PluginRuntimePolicyConfig | None,
) -> PluginRuntimeResolution:
    compat = tuple(filter(None, map(_normalize_token, compatibility_enabled_plugins)))
    if system_policy is None:
        return PluginRuntimeResolution(compat, (), "legacy_enabled_plugins")
    blocked = tuple(filter(None, map(_normalize_token, system_policy.blocked)))
    candidates = tuple(system_policy.enabled) if system_policy.has_enabled else compat
    enabled = tuple(item for item in candidates if item not in set(blocked))
    source = "system_runtime" if system_policy.has_enabled else "legacy_enabled_plugins"
    return PluginRuntimeResolution(enabled, blocked, source)


__all__ = "merge_tool_runtime_overrides resolve_mode_runtime_policy resolve_plugin_runtime_policy resolve_provider_runtime_policy".split()
