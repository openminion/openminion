"""Runtime tool-family config normalization."""

from dataclasses import dataclass
from typing import Any, Mapping

from openminion.base.config.base import ConfigError

from .tool_family import (
    BlockchainToolRuntimeConfig,
    ToolFamilyRuntimeConfig,
    blockchain_tool_runtime_config_to_dict,
    coerce_blockchain_tool_runtime_config,
    coerce_tool_family_runtime_config,
)

_SUPPORTED_RUNTIME_TOOL_FAMILIES = ("search", "fetch", "browser", "weather")
_SUPPORTED_RUNTIME_TOOL_CONFIG_KEYS = _SUPPORTED_RUNTIME_TOOL_FAMILIES + (
    "blockchain",
    "commerce",
    "gws",
)


@dataclass
class ToolRuntimeConfig:
    search: ToolFamilyRuntimeConfig | None = None
    fetch: ToolFamilyRuntimeConfig | None = None
    browser: ToolFamilyRuntimeConfig | None = None
    weather: ToolFamilyRuntimeConfig | None = None
    blockchain: BlockchainToolRuntimeConfig | None = None
    commerce: dict[str, Any] | None = None
    gws: dict[str, Any] | None = None

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
        self.blockchain = coerce_blockchain_tool_runtime_config(self.blockchain)
        for family_name in ("commerce", "gws"):
            value = getattr(self, family_name)
            if value is not None and not isinstance(value, Mapping):
                raise ConfigError(f"runtime.tools.{family_name} must be an object.")
            setattr(self, family_name, dict(value) if value is not None else None)


def coerce_tool_runtime_config(value: object) -> ToolRuntimeConfig:
    if value is None:
        return ToolRuntimeConfig()
    if isinstance(value, ToolRuntimeConfig):
        return value
    if not isinstance(value, Mapping):
        raise ConfigError("runtime.tools must be an object.")

    unknown_families = sorted(
        str(key)
        for key in value
        if str(key).strip().lower() not in _SUPPORTED_RUNTIME_TOOL_CONFIG_KEYS
    )
    if unknown_families:
        supported = ", ".join(_SUPPORTED_RUNTIME_TOOL_CONFIG_KEYS)
        raise ConfigError(
            "runtime.tools only supports these families in Phase 1: "
            f"{supported}. Unsupported keys: {unknown_families!r}."
        )

    normalized: dict[str, Any] = {
        family_name: coerce_tool_family_runtime_config(
            value.get(family_name),
            family_name=family_name,
        )
        for family_name in _SUPPORTED_RUNTIME_TOOL_FAMILIES
    }
    for family_name in ("commerce", "gws"):
        raw = value.get(family_name)
        if raw is not None and not isinstance(raw, Mapping):
            raise ConfigError(f"runtime.tools.{family_name} must be an object.")
        normalized[family_name] = dict(raw) if raw is not None else None
    normalized["blockchain"] = coerce_blockchain_tool_runtime_config(
        value.get("blockchain")
    )
    return ToolRuntimeConfig(**normalized)


def tool_runtime_config_to_dict(config: ToolRuntimeConfig | None) -> dict[str, Any]:
    normalized = coerce_tool_runtime_config(config)
    payload: dict[str, Any] = {}
    for family_name in _SUPPORTED_RUNTIME_TOOL_FAMILIES:
        family_cfg = getattr(normalized, family_name)
        if family_cfg is None:
            continue
        family_payload = {
            key: list(value) if isinstance(value, list) else value
            for key in (
                "enabled_providers",
                "default_provider",
                "provider_order",
                "allow_fallback",
            )
            if (value := getattr(family_cfg, key)) not in (None, "", [])
        }
        payload[family_name] = family_payload
    for family_name in ("commerce", "gws"):
        value = getattr(normalized, family_name)
        if value is not None:
            payload[family_name] = dict(value)
    if normalized.blockchain is not None:
        payload["blockchain"] = blockchain_tool_runtime_config_to_dict(
            normalized.blockchain
        )
    return payload


def merge_tool_runtime_overrides(
    *,
    system_tools: ToolRuntimeConfig | None,
    agent_tools: ToolRuntimeConfig | None,
) -> ToolRuntimeConfig:
    system = coerce_tool_runtime_config(system_tools)
    agent = coerce_tool_runtime_config(agent_tools)
    families = {
        name: _merge_tool_family_runtime_overrides(
            family_name=name,
            system_family=getattr(system, name),
            agent_family=getattr(agent, name),
        )
        for name in _SUPPORTED_RUNTIME_TOOL_FAMILIES
    }
    commerce = dict(system.commerce or {})
    if agent.commerce is not None:
        if agent.commerce != {"enabled": False}:
            raise ConfigError("agent tools.commerce may only disable commerce.")
        for key in ("enabled", "writes_enabled", "order_actions_enabled"):
            commerce[key] = False
    return ToolRuntimeConfig(
        **families,
        blockchain=agent.blockchain or system.blockchain,
        commerce=commerce or None,
        gws=agent.gws if agent.gws is not None else system.gws,
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
    extra = [item for item in agent_enabled if item not in system_enabled]
    if system_enabled and extra:
        raise ConfigError(
            f"agent tools.{family_name}.enabled_providers exceed system config: {extra!r}."
        )
    enabled = agent_enabled or system_enabled
    default = agent_family.default_provider or system_family.default_provider
    if enabled and default and default not in enabled:
        raise ConfigError(f"tools.{family_name}.default_provider is disabled.")
    order = list(agent_family.provider_order or system_family.provider_order)
    extra = [item for item in order if enabled and item not in enabled]
    if extra:
        raise ConfigError(
            f"tools.{family_name}.provider_order is not enabled: {extra!r}."
        )
    fallback = (
        agent_family.allow_fallback
        if agent_family.allow_fallback is not None
        else system_family.allow_fallback
    )
    if system_family.allow_fallback is False and agent_family.allow_fallback is True:
        raise ConfigError(
            f"tools.{family_name}.allow_fallback cannot widen system policy."
        )
    return ToolFamilyRuntimeConfig(enabled, default, order, fallback)


__all__ = (
    "BlockchainToolRuntimeConfig",
    "ToolFamilyRuntimeConfig",
    "ToolRuntimeConfig",
    "coerce_blockchain_tool_runtime_config",
    "coerce_tool_family_runtime_config",
    "coerce_tool_runtime_config",
    "merge_tool_runtime_overrides",
    "tool_runtime_config_to_dict",
)
