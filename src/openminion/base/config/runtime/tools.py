"""Runtime tool-family config normalization."""

from dataclasses import dataclass
from typing import Any, Mapping

from openminion.base.config.base import ConfigError

from .tool_family import (
    BlockchainToolRuntimeConfig,
    CommerceToolRuntimeConfig,
    ToolFamilyRuntimeConfig,
    blockchain_tool_runtime_config_to_dict,
    coerce_blockchain_tool_runtime_config,
    coerce_commerce_tool_runtime_config,
    coerce_tool_family_runtime_config,
    commerce_tool_runtime_config_to_dict,
)

_SUPPORTED_RUNTIME_TOOL_FAMILIES = ("search", "fetch", "browser", "weather")
_SUPPORTED_RUNTIME_TOOL_CONFIG_KEYS = (
    *_SUPPORTED_RUNTIME_TOOL_FAMILIES,
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
    commerce: CommerceToolRuntimeConfig | None = None
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
        self.commerce = coerce_commerce_tool_runtime_config(self.commerce)
        if self.gws is not None:
            if not isinstance(self.gws, Mapping):
                raise ConfigError("runtime.tools.gws must be an object.")
            self.gws = dict(self.gws)

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

    unknown_families = sorted(
        str(key)
        for key in value.keys()
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
    raw_gws = value.get("gws")
    if raw_gws is not None and not isinstance(raw_gws, Mapping):
        raise ConfigError("runtime.tools.gws must be an object.")
    normalized["gws"] = dict(raw_gws) if raw_gws is not None else None
    normalized["blockchain"] = coerce_blockchain_tool_runtime_config(
        value.get("blockchain")
    )
    normalized["commerce"] = coerce_commerce_tool_runtime_config(value.get("commerce"))
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
            family_payload["allow_fallback"] = family_cfg.allow_fallback
        payload[family_name] = family_payload
    if normalized.gws is not None:
        payload["gws"] = dict(normalized.gws)
    if normalized.blockchain is not None:
        payload["blockchain"] = blockchain_tool_runtime_config_to_dict(
            normalized.blockchain
        )
    if normalized.commerce is not None:
        payload["commerce"] = commerce_tool_runtime_config_to_dict(
            normalized.commerce
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
    return ToolRuntimeConfig(
        **families,
        blockchain=agent.blockchain or system.blockchain,
        commerce=_merge_commerce_runtime_overrides(
            system_commerce=system.commerce,
            agent_commerce=agent.commerce,
        ),
        gws=agent.gws if agent.gws is not None else system.gws,
    )


def _merge_commerce_runtime_overrides(
    *,
    system_commerce: CommerceToolRuntimeConfig | None,
    agent_commerce: CommerceToolRuntimeConfig | None,
) -> CommerceToolRuntimeConfig | None:
    if agent_commerce is None:
        return system_commerce
    protected_values = {
        "provider": agent_commerce.provider,
        "base_url": agent_commerce.base_url,
        "merchant_id": agent_commerce.merchant_id,
        "provider_secret_key": agent_commerce.provider_secret_key,
        "buyer_profile_record_id": agent_commerce.buyer_profile_record_id,
        "payment_token_record_id": agent_commerce.payment_token_record_id,
    }
    changed = [name for name, value in protected_values.items() if value]
    if changed:
        raise ConfigError(
            "agent runtime override tools.commerce cannot set system-owned fields: "
            f"{changed!r}."
        )
    if (
        agent_commerce.enabled
        or agent_commerce.writes_enabled
        or agent_commerce.order_actions_enabled
    ):
        raise ConfigError(
            "agent runtime override tools.commerce may only disable commerce."
        )
    if system_commerce is None:
        return agent_commerce
    return CommerceToolRuntimeConfig(
        provider=system_commerce.provider,
        base_url=system_commerce.base_url,
        merchant_id=system_commerce.merchant_id,
        provider_secret_key=system_commerce.provider_secret_key,
        buyer_profile_record_id=system_commerce.buyer_profile_record_id,
        payment_token_record_id=system_commerce.payment_token_record_id,
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


__all__ = (
    "BlockchainToolRuntimeConfig",
    "CommerceToolRuntimeConfig",
    "ToolFamilyRuntimeConfig",
    "ToolRuntimeConfig",
    "coerce_blockchain_tool_runtime_config",
    "coerce_tool_family_runtime_config",
    "coerce_tool_runtime_config",
    "merge_tool_runtime_overrides",
    "tool_runtime_config_to_dict",
)
