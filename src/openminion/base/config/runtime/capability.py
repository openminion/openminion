"""Runtime capability policy config and resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, cast

from openminion.base.config.runtime.reasoning import (
    normalize_optional_reasoning_profile,
)
from openminion.base.config.base import ConfigError
from openminion.base.config.parse import _as_bool


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


_MODE_BOOL_FIELDS = (
    "parallel_enabled",
    "parallel_writes_enabled",
    "adaptive_include_reflect",
    "tool_schema_shortlisting_enabled",
)
_MODE_INT_BOUNDS: dict[str, tuple[int | None, int | None]] = {
    "max_parallel_workers": (1, 10),
    "checkpoint_interval": (1, None),
    "max_resume_count": (0, None),
    "max_depth": (0, None),
    "priority_hint": (None, None),
    "max_commands_per_turn": (1, None),
    "max_adaptive_iterations": (1, 100),
    "max_adaptive_tool_calls_per_loop": (1, 100),
    "max_adaptive_llm_calls_per_loop": (1, 100),
    "max_subtasks": (2, 20),
    "max_decompose_depth": (1, 5),
    "max_research_iterations": (1, 20),
    "max_self_corrections": (1, 20),
}


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
        values: dict[str, Any] = {
            "enabled": _as_bool(raw_item.get("enabled"), True),
            **{
                name: _as_bool(raw_item.get(name), False) if name in raw_item else None
                for name in _MODE_BOOL_FIELDS
            },
        }
        for name, (minimum, maximum) in _MODE_INT_BOUNDS.items():
            values[name] = _optional_mode_int(
                raw_item.get(name),
                field_path=f"{field_path}.{mode_name}.{name}",
                minimum=minimum,
                maximum=maximum,
            )
        normalized[mode_name] = ModeRuntimePolicyConfig(**values)
    return normalized


def mode_runtime_policy_to_dict(
    policy: Mapping[str, ModeRuntimePolicyConfig] | None,
) -> dict[str, Any]:
    if not policy:
        return {}
    payload: dict[str, Any] = {}
    for name, config in sorted(policy.items(), key=lambda item: item[0]):
        entry: dict[str, Any] = {"enabled": bool(config.enabled)}
        for field_name in (*_MODE_BOOL_FIELDS, *_MODE_INT_BOUNDS):
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


from .capability_resolution import (  # noqa: E402
    merge_tool_runtime_overrides,
    resolve_mode_runtime_policy,
    resolve_plugin_runtime_policy,
    resolve_provider_runtime_policy,
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
