from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

from openminion.base.config.base import ConfigError

_CONFIG_KEYS = frozenset(
    {
        "enabled",
        "provider",
        "base_url",
        "merchant_id",
        "provider_secret_key",
        "buyer_profile_record_id",
        "payment_token_record_id",
        "writes_enabled",
        "order_actions_enabled",
    }
)
_SUPPORTED_PROVIDERS: frozenset[str] = frozenset()


@dataclass
class CommerceToolRuntimeConfig:
    enabled: bool = False
    provider: str = ""
    base_url: str = ""
    merchant_id: str = ""
    provider_secret_key: str = ""
    buyer_profile_record_id: str = ""
    payment_token_record_id: str = ""
    writes_enabled: bool = False
    order_actions_enabled: bool = False


def coerce_commerce_tool_runtime_config(
    value: object,
) -> CommerceToolRuntimeConfig | None:
    if value is None:
        return None
    if isinstance(value, CommerceToolRuntimeConfig):
        return value
    if isinstance(value, Mapping):
        unknown = sorted(str(key) for key in value if key not in _CONFIG_KEYS)
        if unknown:
            raise ConfigError(
                f"runtime.tools.commerce contains unsupported keys: {unknown!r}."
            )
        for field_name in ("enabled", "writes_enabled", "order_actions_enabled"):
            if field_name in value and not isinstance(value[field_name], bool):
                raise ConfigError(
                    f"runtime.tools.commerce.{field_name} must be a boolean."
                )
        config = CommerceToolRuntimeConfig(
            enabled=value.get("enabled", False),
            provider=str(value.get("provider", "")).strip().lower(),
            base_url=str(value.get("base_url", "")).strip(),
            merchant_id=str(value.get("merchant_id", "")).strip(),
            provider_secret_key=str(value.get("provider_secret_key", "")).strip(),
            buyer_profile_record_id=str(
                value.get("buyer_profile_record_id", "")
            ).strip(),
            payment_token_record_id=str(
                value.get("payment_token_record_id", "")
            ).strip(),
            writes_enabled=value.get("writes_enabled", False),
            order_actions_enabled=value.get("order_actions_enabled", False),
        )
    else:
        raise ConfigError("runtime.tools.commerce must be an object.")

    flags = (config.enabled, config.writes_enabled, config.order_actions_enabled)
    if any(not isinstance(flag, bool) for flag in flags):
        raise ConfigError("runtime.tools.commerce enabled flags must be booleans.")
    if config.writes_enabled and not config.enabled:
        raise ConfigError(
            "runtime.tools.commerce.writes_enabled=true requires enabled=true."
        )
    if config.order_actions_enabled and not config.writes_enabled:
        raise ConfigError(
            "runtime.tools.commerce.order_actions_enabled=true requires "
            "writes_enabled=true."
        )
    if config.enabled and not config.provider:
        raise ConfigError("runtime.tools.commerce.provider is required when enabled.")
    if config.enabled:
        try:
            base_url = urlsplit(config.base_url)
            valid_base_url = (
                base_url.scheme.lower() == "https"
                and bool(base_url.hostname)
                and base_url.username is None
                and base_url.password is None
                and not base_url.query
                and not base_url.fragment
                and "%" not in base_url.path
            )
        except ValueError:
            valid_base_url = False
        if not valid_base_url:
            raise ConfigError(
                "runtime.tools.commerce.base_url must be a credential-free HTTPS URL."
            )
        if config.provider not in _SUPPORTED_PROVIDERS:
            raise ConfigError(
                f"runtime.tools.commerce.provider={config.provider!r} is not supported."
            )
    return config


__all__ = ["CommerceToolRuntimeConfig", "coerce_commerce_tool_runtime_config"]
