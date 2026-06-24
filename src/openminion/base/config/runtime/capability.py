"""Runtime capability policy config and resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, cast

from openminion.modules.llm.reasoning import (
    normalize_optional_reasoning_profile,
)
from openminion.base.config.base import ConfigError
from openminion.base.config.parse import _as_bool
from openminion.base.config.runtime.tools import (
    ToolFamilyRuntimeConfig,
    ToolRuntimeConfig,
    coerce_tool_runtime_config,
)


def _normalize_tokens(
    raw_value: object,
    *,
    field_path: str,
    allow_empty: bool = False,
) -> list[str]:
    if raw_value is None:
        return []
    if not isinstance(raw_value, list):
        raise ConfigError(f"{field_path} must be an array of string ids.")
    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_value):
        token = str(item or "").strip().lower()
        if not token:
            raise ConfigError(f"{field_path}[{index}] must be a non-empty string id.")
        if token in seen:
            raise ConfigError(f"{field_path} must not contain duplicates: {token!r}.")
        seen.add(token)
        normalized.append(token)
    if not allow_empty and raw_value == []:
        raise ConfigError(f"{field_path} must contain at least one item when provided.")
    return normalized


def _normalize_token(raw_value: object) -> str:
    return str(raw_value or "").strip().lower()


@dataclass
class ProviderRuntimePolicyConfig:
    enabled: list[str] = field(default_factory=list)
    default_provider: str = ""
    provider_order: list[str] = field(default_factory=list)
    has_enabled: bool = field(default=False, repr=False)
    has_default_provider: bool = field(default=False, repr=False)
    has_provider_order: bool = field(default=False, repr=False)


@dataclass
class ModeRuntimePolicyConfig:
    enabled: bool = True
    parallel_enabled: bool | None = None
    parallel_writes_enabled: bool | None = None
    max_parallel_workers: int | None = None
    checkpoint_interval: int | None = None
    max_resume_count: int | None = None
    max_depth: int | None = None
    priority_hint: int | None = None
    max_commands_per_turn: int | None = None
    max_adaptive_iterations: int | None = None
    max_adaptive_tool_calls_per_loop: int | None = None
    max_adaptive_llm_calls_per_loop: int | None = None
    adaptive_include_reflect: bool | None = None
    max_subtasks: int | None = None
    max_decompose_depth: int | None = None
    max_research_iterations: int | None = None
    max_self_corrections: int | None = None
    tool_schema_shortlisting_enabled: bool | None = None


def _optional_mode_int(
    value: object,
    *,
    field_path: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_path} must be an integer.") from exc
    if minimum is not None and parsed < minimum:
        raise ConfigError(f"{field_path} must be >= {minimum}.")
    if maximum is not None and parsed > maximum:
        raise ConfigError(f"{field_path} must be <= {maximum}.")
    return int(parsed)


@dataclass
class ThinkingRuntimePolicyConfig:
    reasoning_profile: str = ""
    has_reasoning_profile: bool = field(default=False, repr=False)


@dataclass
class PluginRuntimePolicyConfig:
    enabled: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    has_enabled: bool = field(default=False, repr=False)
    has_blocked: bool = field(default=False, repr=False)


@dataclass(frozen=True)
class ProviderResolution:
    selected_provider: str
    source: str
    effective_enabled: tuple[str, ...]
    provider_order: tuple[str, ...]
    system_default_provider: str = ""
    agent_default_provider: str = ""
    invocation_requested_provider: str = ""


@dataclass(frozen=True)
class ModeRuntimeResolution:
    effective_modes: dict[str, ModeRuntimePolicyConfig]
    blocked_reasons: dict[str, str]


@dataclass(frozen=True)
class PluginRuntimeResolution:
    effective_enabled: tuple[str, ...]
    blocked: tuple[str, ...]
    source: str


def coerce_thinking_runtime_policy_config(
    value: object,
    *,
    field_path: str,
) -> ThinkingRuntimePolicyConfig | None:
    if value is None:
        return None
    if isinstance(value, ThinkingRuntimePolicyConfig):
        return value
    if isinstance(value, str):
        normalized = normalize_optional_reasoning_profile(value) or ""
        return ThinkingRuntimePolicyConfig(
            reasoning_profile=normalized,
            has_reasoning_profile=bool(str(value or "").strip()),
        )
    if not isinstance(value, Mapping):
        raise ConfigError(f"{field_path} must be an object.")

    raw_reasoning = value.get("reasoning_profile")
    raw_legacy = value.get("thinking")
    if raw_reasoning is not None and raw_legacy is not None:
        normalized_reasoning = normalize_optional_reasoning_profile(raw_reasoning)
        normalized_legacy = normalize_optional_reasoning_profile(raw_legacy)
        if normalized_reasoning != normalized_legacy:
            raise ConfigError(
                f"{field_path}.reasoning_profile and {field_path}.thinking must agree when both are provided."
            )
    raw_value = raw_reasoning if raw_reasoning is not None else raw_legacy
    normalized = normalize_optional_reasoning_profile(raw_value) or ""
    return ThinkingRuntimePolicyConfig(
        reasoning_profile=normalized,
        has_reasoning_profile=("reasoning_profile" in value or "thinking" in value),
    )


def thinking_runtime_policy_to_dict(
    policy: ThinkingRuntimePolicyConfig | None,
) -> dict[str, Any]:
    if policy is None:
        return {}
    if not policy.has_reasoning_profile and not policy.reasoning_profile:
        return {}
    return {"reasoning_profile": policy.reasoning_profile}


def coerce_provider_runtime_policy_config(
    value: object,
    *,
    field_path: str,
) -> ProviderRuntimePolicyConfig | None:
    if value is None:
        return None
    if isinstance(value, ProviderRuntimePolicyConfig):
        return value
    if not isinstance(value, Mapping):
        raise ConfigError(f"{field_path} must be an object.")
    has_enabled = "enabled" in value
    has_default = "default_provider" in value
    has_order = "provider_order" in value
    enabled = _normalize_tokens(
        value.get("enabled"),
        field_path=f"{field_path}.enabled",
    )
    provider_order = _normalize_tokens(
        value.get("provider_order"),
        field_path=f"{field_path}.provider_order",
    )
    default_provider = _normalize_token(value.get("default_provider"))
    if default_provider and has_enabled and default_provider not in enabled:
        raise ConfigError(
            f"{field_path}.default_provider must be listed in {field_path}.enabled."
        )
    if default_provider and provider_order and default_provider not in provider_order:
        raise ConfigError(
            f"{field_path}.default_provider must be listed in {field_path}.provider_order."
        )
    if has_enabled and provider_order:
        extra = [item for item in provider_order if item not in enabled]
        if extra:
            raise ConfigError(
                f"{field_path}.provider_order must be a subset of {field_path}.enabled: {extra!r}."
            )
    return ProviderRuntimePolicyConfig(
        enabled=enabled,
        default_provider=default_provider,
        provider_order=provider_order,
        has_enabled=has_enabled,
        has_default_provider=has_default,
        has_provider_order=has_order,
    )


def provider_runtime_policy_to_dict(
    policy: ProviderRuntimePolicyConfig | None,
) -> dict[str, Any]:
    if policy is None:
        return {}
    payload: dict[str, Any] = {}
    if policy.has_enabled or policy.enabled:
        payload["enabled"] = list(policy.enabled)
    if policy.has_default_provider or policy.default_provider:
        payload["default_provider"] = policy.default_provider
    if policy.has_provider_order or policy.provider_order:
        payload["provider_order"] = list(policy.provider_order)
    return payload


def coerce_mode_runtime_policy_map(
    value: object,
    *,
    field_path: str,
) -> dict[str, ModeRuntimePolicyConfig]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"{field_path} must be an object keyed by mode id.")
    normalized: dict[str, ModeRuntimePolicyConfig] = {}
    for raw_name, raw_item in value.items():
        mode_name = str(raw_name or "").strip().lower()
        if not mode_name:
            raise ConfigError(f"{field_path} contains an empty mode id.")
        if isinstance(raw_item, ModeRuntimePolicyConfig):
            normalized[mode_name] = raw_item
            continue
        if not isinstance(raw_item, Mapping):
            raise ConfigError(f"{field_path}.{mode_name} must be an object.")
        normalized[mode_name] = ModeRuntimePolicyConfig(
            enabled=_as_bool(raw_item.get("enabled"), True),
            parallel_enabled=(
                _as_bool(raw_item.get("parallel_enabled"), False)
                if "parallel_enabled" in raw_item
                else None
            ),
            parallel_writes_enabled=(
                _as_bool(raw_item.get("parallel_writes_enabled"), False)
                if "parallel_writes_enabled" in raw_item
                else None
            ),
            max_parallel_workers=_optional_mode_int(
                raw_item.get("max_parallel_workers"),
                field_path=f"{field_path}.{mode_name}.max_parallel_workers",
                minimum=1,
                maximum=10,
            ),
            checkpoint_interval=_optional_mode_int(
                raw_item.get("checkpoint_interval"),
                field_path=f"{field_path}.{mode_name}.checkpoint_interval",
                minimum=1,
            ),
            max_resume_count=_optional_mode_int(
                raw_item.get("max_resume_count"),
                field_path=f"{field_path}.{mode_name}.max_resume_count",
                minimum=0,
            ),
            max_depth=_optional_mode_int(
                raw_item.get("max_depth"),
                field_path=f"{field_path}.{mode_name}.max_depth",
                minimum=0,
            ),
            priority_hint=_optional_mode_int(
                raw_item.get("priority_hint"),
                field_path=f"{field_path}.{mode_name}.priority_hint",
            ),
            max_commands_per_turn=_optional_mode_int(
                raw_item.get("max_commands_per_turn"),
                field_path=f"{field_path}.{mode_name}.max_commands_per_turn",
                minimum=1,
            ),
            max_adaptive_iterations=_optional_mode_int(
                raw_item.get("max_adaptive_iterations"),
                field_path=f"{field_path}.{mode_name}.max_adaptive_iterations",
                minimum=1,
                maximum=100,
            ),
            max_adaptive_tool_calls_per_loop=_optional_mode_int(
                raw_item.get("max_adaptive_tool_calls_per_loop"),
                field_path=f"{field_path}.{mode_name}.max_adaptive_tool_calls_per_loop",
                minimum=1,
                maximum=100,
            ),
            max_adaptive_llm_calls_per_loop=_optional_mode_int(
                raw_item.get("max_adaptive_llm_calls_per_loop"),
                field_path=f"{field_path}.{mode_name}.max_adaptive_llm_calls_per_loop",
                minimum=1,
                maximum=100,
            ),
            adaptive_include_reflect=(
                _as_bool(raw_item.get("adaptive_include_reflect"), False)
                if "adaptive_include_reflect" in raw_item
                else None
            ),
            max_subtasks=_optional_mode_int(
                raw_item.get("max_subtasks"),
                field_path=f"{field_path}.{mode_name}.max_subtasks",
                minimum=2,
                maximum=20,
            ),
            max_decompose_depth=_optional_mode_int(
                raw_item.get("max_decompose_depth"),
                field_path=f"{field_path}.{mode_name}.max_decompose_depth",
                minimum=1,
                maximum=5,
            ),
            max_research_iterations=_optional_mode_int(
                raw_item.get("max_research_iterations"),
                field_path=f"{field_path}.{mode_name}.max_research_iterations",
                minimum=1,
                maximum=20,
            ),
            max_self_corrections=_optional_mode_int(
                raw_item.get("max_self_corrections"),
                field_path=f"{field_path}.{mode_name}.max_self_corrections",
                minimum=1,
                maximum=20,
            ),
            tool_schema_shortlisting_enabled=(
                _as_bool(raw_item.get("tool_schema_shortlisting_enabled"), False)
                if "tool_schema_shortlisting_enabled" in raw_item
                else None
            ),
        )
    return normalized


def mode_runtime_policy_to_dict(
    policy: Mapping[str, ModeRuntimePolicyConfig] | None,
) -> dict[str, Any]:
    if not policy:
        return {}
    payload: dict[str, Any] = {}
    for name, config in sorted(policy.items(), key=lambda item: item[0]):
        entry: dict[str, Any] = {"enabled": bool(config.enabled)}
        for field_name in (
            "parallel_enabled",
            "parallel_writes_enabled",
            "max_parallel_workers",
            "checkpoint_interval",
            "max_resume_count",
            "max_depth",
            "priority_hint",
            "max_commands_per_turn",
            "max_adaptive_iterations",
            "max_adaptive_tool_calls_per_loop",
            "max_adaptive_llm_calls_per_loop",
            "adaptive_include_reflect",
            "max_subtasks",
            "max_decompose_depth",
            "max_research_iterations",
            "max_self_corrections",
            "tool_schema_shortlisting_enabled",
        ):
            value = getattr(config, field_name)
            if value is not None:
                entry[field_name] = value
        payload[str(name)] = entry
    return payload


def coerce_plugin_runtime_policy_config(
    value: object,
    *,
    field_path: str,
) -> PluginRuntimePolicyConfig | None:
    if value is None:
        return None
    if isinstance(value, PluginRuntimePolicyConfig):
        return value
    if not isinstance(value, Mapping):
        raise ConfigError(f"{field_path} must be an object.")
    has_enabled = "enabled" in value
    has_blocked = "blocked" in value
    enabled = _normalize_tokens(
        value.get("enabled"),
        field_path=f"{field_path}.enabled",
        allow_empty=True,
    )
    blocked = _normalize_tokens(
        value.get("blocked"),
        field_path=f"{field_path}.blocked",
        allow_empty=True,
    )
    return PluginRuntimePolicyConfig(
        enabled=enabled,
        blocked=blocked,
        has_enabled=has_enabled,
        has_blocked=has_blocked,
    )


def plugin_runtime_policy_to_dict(
    policy: PluginRuntimePolicyConfig | None,
) -> dict[str, Any]:
    if policy is None:
        return {}
    payload: dict[str, Any] = {}
    if policy.has_enabled or policy.enabled:
        payload["enabled"] = list(policy.enabled)
    if policy.has_blocked or policy.blocked:
        payload["blocked"] = list(policy.blocked)
    return payload


def resolve_provider_runtime_policy(
    *,
    system_policy: ProviderRuntimePolicyConfig | None,
    agent_policy: ProviderRuntimePolicyConfig | None,
    code_default_provider: str,
    legacy_agent_provider: str = "",
    invocation_provider: str = "",
) -> ProviderResolution:
    resolved_code_default = _normalize_token(code_default_provider) or "echo"
    resolved_legacy_agent = _normalize_token(legacy_agent_provider)
    resolved_invocation = _normalize_token(invocation_provider)
    system_enabled = list(system_policy.enabled) if system_policy else []
    agent_enabled = list(agent_policy.enabled) if agent_policy else []
    system_has_enabled = bool(system_policy and system_policy.has_enabled)
    agent_has_enabled = bool(agent_policy and agent_policy.has_enabled)

    if system_has_enabled and agent_has_enabled:
        extra = [item for item in agent_enabled if item not in system_enabled]
        if extra:
            raise ConfigError(
                "Agent runtime override providers.enabled cannot exceed "
                f"system.runtime.providers.enabled: {extra!r}."
            )

    effective_enabled = (
        agent_enabled
        if agent_has_enabled
        else system_enabled
        if system_has_enabled
        else []
    )

    system_default = ""
    if system_policy is not None:
        system_default = system_policy.default_provider or (
            system_enabled[0] if system_has_enabled and system_enabled else ""
        )
    agent_default = ""
    if agent_policy is not None:
        agent_default = agent_policy.default_provider or (
            agent_enabled[0] if agent_has_enabled and agent_enabled else ""
        )

    for label, candidate in (
        ("agent.provider", resolved_legacy_agent),
        ("agent runtime override", agent_default),
        ("invocation override", resolved_invocation),
    ):
        if candidate and effective_enabled and candidate not in effective_enabled:
            raise ConfigError(
                f"{label} requested provider {candidate!r}, but it is blocked by the effective provider allowlist {effective_enabled!r}."
            )

    provider_order = _resolve_effective_provider_order(
        system_policy=system_policy,
        agent_policy=agent_policy,
        effective_enabled=effective_enabled,
    )

    selected_provider = ""
    source = ""
    if system_default:
        selected_provider = system_default
        source = "system_runtime"
    if resolved_legacy_agent:
        selected_provider = resolved_legacy_agent
        source = "agent_runtime"
    if agent_default:
        selected_provider = agent_default
        source = "agent_runtime"
    if resolved_invocation:
        selected_provider = resolved_invocation
        source = "invocation_override"
    if not selected_provider:
        if effective_enabled:
            selected_provider = effective_enabled[0]
            source = "system_runtime" if system_has_enabled else "agent_runtime"
        else:
            selected_provider = resolved_code_default
            source = "capability_definition"

    if effective_enabled and selected_provider not in effective_enabled:
        selected_provider = effective_enabled[0]
        source = "system_runtime" if system_has_enabled else "agent_runtime"

    return ProviderResolution(
        selected_provider=selected_provider,
        source=source,
        effective_enabled=tuple(effective_enabled),
        provider_order=tuple(provider_order),
        system_default_provider=system_default,
        agent_default_provider=agent_default or resolved_legacy_agent,
        invocation_requested_provider=resolved_invocation,
    )


def _resolve_effective_provider_order(
    *,
    system_policy: ProviderRuntimePolicyConfig | None,
    agent_policy: ProviderRuntimePolicyConfig | None,
    effective_enabled: list[str],
) -> list[str]:
    allowed = set(effective_enabled)
    if (
        system_policy is not None
        and agent_policy is not None
        and agent_policy.has_provider_order
    ):
        extra = [
            item
            for item in agent_policy.provider_order
            if allowed and item not in allowed
        ]
        if extra:
            raise ConfigError(
                "Agent runtime override providers.provider_order cannot exceed the effective provider allowlist: "
                f"{extra!r}."
            )
    ordered = (
        list(agent_policy.provider_order)
        if agent_policy is not None and agent_policy.has_provider_order
        else list(system_policy.provider_order)
        if system_policy is not None and system_policy.has_provider_order
        else list(effective_enabled)
    )
    if not ordered:
        return list(effective_enabled)
    if not allowed:
        return ordered
    return [item for item in ordered if item in allowed]


def merge_tool_runtime_overrides(
    *,
    system_tools: ToolRuntimeConfig | None,
    agent_tools: ToolRuntimeConfig | None,
) -> ToolRuntimeConfig:
    merged: dict[str, ToolFamilyRuntimeConfig | None] = {}
    normalized_system = coerce_tool_runtime_config(system_tools)
    normalized_agent = coerce_tool_runtime_config(agent_tools)
    for family_name in ("search", "fetch", "browser", "weather"):
        merged[family_name] = _merge_tool_family_runtime_overrides(
            family_name=family_name,
            system_family=getattr(normalized_system, family_name),
            agent_family=getattr(normalized_agent, family_name),
        )
    return ToolRuntimeConfig(**merged)


def _merge_tool_family_runtime_overrides(
    *,
    family_name: str,
    system_family: ToolFamilyRuntimeConfig | None,
    agent_family: ToolFamilyRuntimeConfig | None,
) -> ToolFamilyRuntimeConfig | None:
    if system_family is None and agent_family is None:
        return None
    if system_family is None:
        return agent_family
    if agent_family is None:
        return system_family

    system_enabled = list(system_family.enabled_providers)
    agent_enabled = list(agent_family.enabled_providers)
    if system_enabled and agent_enabled:
        extra = [item for item in agent_enabled if item not in system_enabled]
        if extra:
            raise ConfigError(
                f"agent runtime override tools.{family_name}.enabled_providers cannot exceed runtime.tools.{family_name}.enabled_providers: {extra!r}."
            )

    effective_enabled = agent_enabled or system_enabled
    default_provider = agent_family.default_provider or system_family.default_provider
    if (
        effective_enabled
        and default_provider
        and default_provider not in effective_enabled
    ):
        raise ConfigError(
            f"agent runtime override tools.{family_name}.default_provider={default_provider!r} is blocked by the effective enabled_providers {effective_enabled!r}."
        )

    provider_order = list(agent_family.provider_order or system_family.provider_order)
    if effective_enabled and provider_order:
        extra = [item for item in provider_order if item not in effective_enabled]
        if extra:
            raise ConfigError(
                f"agent runtime override tools.{family_name}.provider_order cannot exceed the effective enabled_providers: {extra!r}."
            )

    effective_fallback = (
        agent_family.allow_fallback
        if agent_family.allow_fallback is not None
        else system_family.allow_fallback
    )
    if system_family.allow_fallback is False and agent_family.allow_fallback is True:
        raise ConfigError(
            f"agent runtime override tools.{family_name}.allow_fallback=true cannot override runtime.tools.{family_name}.allow_fallback=false."
        )

    return ToolFamilyRuntimeConfig(
        enabled_providers=effective_enabled,
        default_provider=default_provider,
        provider_order=provider_order,
        allow_fallback=effective_fallback,
    )


def resolve_mode_runtime_policy(
    *,
    system_modes: Mapping[str, ModeRuntimePolicyConfig] | None,
    agent_modes: Mapping[str, ModeRuntimePolicyConfig] | None,
) -> ModeRuntimeResolution:
    normalized_system = {
        str(name).strip().lower(): config
        for name, config in (system_modes or {}).items()
        if str(name).strip()
    }
    normalized_agent = {
        str(name).strip().lower(): config
        for name, config in (agent_modes or {}).items()
        if str(name).strip()
    }
    effective: dict[str, ModeRuntimePolicyConfig] = {}
    blocked: dict[str, str] = {}
    merged_fields = (
        "parallel_enabled",
        "parallel_writes_enabled",
        "max_parallel_workers",
        "checkpoint_interval",
        "max_resume_count",
        "max_depth",
        "priority_hint",
        "max_commands_per_turn",
        "max_adaptive_iterations",
        "max_adaptive_tool_calls_per_loop",
        "max_adaptive_llm_calls_per_loop",
        "adaptive_include_reflect",
        "max_subtasks",
        "max_decompose_depth",
        "max_research_iterations",
        "max_self_corrections",
        "tool_schema_shortlisting_enabled",
    )
    for mode_name in sorted(set(normalized_system) | set(normalized_agent)):
        system_entry = normalized_system.get(mode_name)
        agent_entry = normalized_agent.get(mode_name)
        if system_entry is not None and system_entry.enabled is False:
            effective[mode_name] = ModeRuntimePolicyConfig(
                enabled=False,
                **{
                    field_name: getattr(system_entry, field_name)
                    for field_name in merged_fields
                },
            )
            if agent_entry is not None and agent_entry.enabled is True:
                blocked[mode_name] = (
                    f"agent runtime override requested mode {mode_name!r}, but system.runtime.modes.{mode_name}.enabled=false."
                )
            continue
        if agent_entry is not None or system_entry is not None:
            effective[mode_name] = ModeRuntimePolicyConfig(
                enabled=(
                    agent_entry.enabled
                    if agent_entry is not None
                    else bool(system_entry.enabled)
                    if system_entry is not None
                    else True
                ),
                **{
                    field_name: (
                        getattr(agent_entry, field_name)
                        if agent_entry is not None
                        and getattr(agent_entry, field_name) is not None
                        else getattr(system_entry, field_name)
                        if system_entry is not None
                        else None
                    )
                    for field_name in merged_fields
                },
            )
    return ModeRuntimeResolution(effective_modes=effective, blocked_reasons=blocked)


def resolve_plugin_runtime_policy(
    *,
    compatibility_enabled_plugins: list[str] | tuple[str, ...],
    system_policy: PluginRuntimePolicyConfig | None,
) -> PluginRuntimeResolution:
    compat = [
        str(item).strip() for item in compatibility_enabled_plugins if str(item).strip()
    ]
    if system_policy is None:
        return PluginRuntimeResolution(
            effective_enabled=tuple(compat),
            blocked=tuple(),
            source="legacy_enabled_plugins",
        )
    blocked = [str(item).strip() for item in system_policy.blocked if str(item).strip()]
    blocked_set = set(blocked)
    if system_policy.has_enabled:
        base = [item for item in system_policy.enabled if item not in blocked_set]
        return PluginRuntimeResolution(
            effective_enabled=tuple(base),
            blocked=tuple(blocked),
            source="system_runtime",
        )
    return PluginRuntimeResolution(
        effective_enabled=tuple(item for item in compat if item not in blocked_set),
        blocked=tuple(blocked),
        source="legacy_enabled_plugins",
    )


__all__ = [
    "ModeRuntimePolicyConfig",
    "ModeRuntimeResolution",
    "PluginRuntimePolicyConfig",
    "PluginRuntimeResolution",
    "ProviderResolution",
    "ProviderRuntimePolicyConfig",
    "ThinkingRuntimePolicyConfig",
    "coerce_mode_runtime_policy_map",
    "coerce_plugin_runtime_policy_config",
    "coerce_provider_runtime_policy_config",
    "coerce_thinking_runtime_policy_config",
    "merge_tool_runtime_overrides",
    "mode_runtime_policy_to_dict",
    "plugin_runtime_policy_to_dict",
    "provider_runtime_policy_to_dict",
    "resolve_mode_runtime_policy",
    "resolve_plugin_runtime_policy",
    "resolve_provider_runtime_policy",
    "thinking_runtime_policy_to_dict",
]
