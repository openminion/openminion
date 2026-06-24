"""Runtime tool-family config normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from openminion.base.config.base import ConfigError
from openminion.base.config.parse import _as_bool

_SUPPORTED_RUNTIME_TOOL_FAMILIES = ("search", "fetch", "browser", "weather")


def _normalize_provider_tokens(raw_value: object, *, field_path: str) -> list[str]:
    if raw_value is None:
        return []
    if not isinstance(raw_value, list):
        raise ConfigError(f"{field_path} must be an array of provider ids.")

    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_value):
        token = str(item or "").strip().lower()
        if not token:
            raise ConfigError(f"{field_path}[{index}] must be a non-empty provider id.")
        if token in seen:
            raise ConfigError(
                f"{field_path} must not contain duplicate provider ids: {token!r}."
            )
        seen.add(token)
        normalized.append(token)
    return normalized


def _normalize_default_provider(raw_value: object) -> str:
    return str(raw_value or "").strip().lower()


def _normalize_allow_fallback(
    raw_value: object,
    *,
    field_path: str,
) -> bool | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, (bool, int, float, str)):
        return _as_bool(raw_value, False)
    raise ConfigError(f"{field_path} must be a boolean when provided.")


@dataclass
class ToolFamilyRuntimeConfig:
    enabled_providers: list[str] = field(default_factory=list)
    default_provider: str = ""
    provider_order: list[str] = field(default_factory=list)
    allow_fallback: bool | None = None


def coerce_tool_family_runtime_config(
    value: object,
    *,
    family_name: str,
) -> ToolFamilyRuntimeConfig | None:
    if value is None:
        return None
    if isinstance(value, ToolFamilyRuntimeConfig):
        return value
    if not isinstance(value, Mapping):
        raise ConfigError(f"runtime.tools.{family_name} must be an object.")

    field_path = f"runtime.tools.{family_name}"
    enabled_providers = _normalize_provider_tokens(
        value.get("enabled_providers"),
        field_path=f"{field_path}.enabled_providers",
    )
    provider_order = _normalize_provider_tokens(
        value.get("provider_order"),
        field_path=f"{field_path}.provider_order",
    )
    default_provider = _normalize_default_provider(value.get("default_provider"))
    allow_fallback = _normalize_allow_fallback(
        value.get("allow_fallback"),
        field_path=f"{field_path}.allow_fallback",
    )

    if "enabled_providers" in value and not enabled_providers:
        raise ConfigError(
            f"{field_path}.enabled_providers must contain at least one provider id."
        )
    if "provider_order" in value and not provider_order:
        raise ConfigError(
            f"{field_path}.provider_order must contain at least one provider id."
        )
    if (
        default_provider
        and enabled_providers
        and default_provider not in enabled_providers
    ):
        raise ConfigError(
            f"{field_path}.default_provider must be listed in "
            f"{field_path}.enabled_providers."
        )
    if default_provider and provider_order and default_provider not in provider_order:
        raise ConfigError(
            f"{field_path}.default_provider must be listed in "
            f"{field_path}.provider_order."
        )
    if enabled_providers and provider_order:
        extra = [token for token in provider_order if token not in enabled_providers]
        if extra:
            raise ConfigError(
                f"{field_path}.provider_order must be a subset of "
                f"{field_path}.enabled_providers: {extra!r}."
            )

    return ToolFamilyRuntimeConfig(
        enabled_providers=enabled_providers,
        default_provider=default_provider,
        provider_order=provider_order,
        allow_fallback=allow_fallback,
    )


@dataclass
class ToolRuntimeConfig:
    search: ToolFamilyRuntimeConfig | None = None
    fetch: ToolFamilyRuntimeConfig | None = None
    browser: ToolFamilyRuntimeConfig | None = None
    weather: ToolFamilyRuntimeConfig | None = None

    def __post_init__(self) -> None:
        self.search = coerce_tool_family_runtime_config(
            self.search, family_name="search"
        )
        self.fetch = coerce_tool_family_runtime_config(self.fetch, family_name="fetch")
        self.browser = coerce_tool_family_runtime_config(
            self.browser, family_name="browser"
        )
        self.weather = coerce_tool_family_runtime_config(
            self.weather, family_name="weather"
        )

    def configured_families(self) -> dict[str, ToolFamilyRuntimeConfig]:
        configured: dict[str, ToolFamilyRuntimeConfig] = {}
        for family_name in _SUPPORTED_RUNTIME_TOOL_FAMILIES:
            family_cfg = getattr(self, family_name)
            if family_cfg is not None:
                configured[family_name] = family_cfg
        return configured


def coerce_tool_runtime_config(value: object) -> ToolRuntimeConfig:
    if value is None:
        return ToolRuntimeConfig()
    if isinstance(value, ToolRuntimeConfig):
        return value
    if not isinstance(value, Mapping):
        raise ConfigError("runtime.tools must be an object.")

    normalized: dict[str, ToolFamilyRuntimeConfig | None] = {}
    unknown_families = sorted(
        str(key)
        for key in value.keys()
        if str(key).strip().lower() not in _SUPPORTED_RUNTIME_TOOL_FAMILIES
    )
    if unknown_families:
        supported = ", ".join(_SUPPORTED_RUNTIME_TOOL_FAMILIES)
        raise ConfigError(
            "runtime.tools only supports these families in Phase 1: "
            f"{supported}. Unsupported keys: {unknown_families!r}."
        )

    for family_name in _SUPPORTED_RUNTIME_TOOL_FAMILIES:
        raw_family = value.get(family_name)
        normalized[family_name] = coerce_tool_family_runtime_config(
            raw_family,
            family_name=family_name,
        )
    return ToolRuntimeConfig(**normalized)


def tool_runtime_config_to_dict(config: ToolRuntimeConfig | None) -> dict[str, Any]:
    if config is None:
        return {}
    normalized = coerce_tool_runtime_config(config)
    payload: dict[str, Any] = {}
    for family_name, family_cfg in normalized.configured_families().items():
        family_payload: dict[str, Any] = {}
        if family_cfg.enabled_providers:
            family_payload["enabled_providers"] = list(family_cfg.enabled_providers)
        if family_cfg.default_provider:
            family_payload["default_provider"] = family_cfg.default_provider
        if family_cfg.provider_order:
            family_payload["provider_order"] = list(family_cfg.provider_order)
        if family_cfg.allow_fallback is not None:
            family_payload["allow_fallback"] = bool(family_cfg.allow_fallback)
        payload[family_name] = family_payload
    return payload


__all__ = [
    "ToolFamilyRuntimeConfig",
    "ToolRuntimeConfig",
    "coerce_tool_family_runtime_config",
    "coerce_tool_runtime_config",
    "tool_runtime_config_to_dict",
]
