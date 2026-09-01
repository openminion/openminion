from dataclasses import dataclass, field
import re
from typing import Any, Mapping

from openminion.base.config.base import ConfigError
from openminion.base.config.parse import _as_bool


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


def _normalize_allow_fallback(
    raw_value: object,
    *,
    field_path: str,
) -> bool | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, (bool, int, float, str)):
        raise ConfigError(f"{field_path} must be a boolean when provided.")
    return bool(_as_bool(raw_value, False))


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
    default_provider = str(value.get("default_provider") or "").strip().lower()
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
    if default_provider and enabled_providers and default_provider not in enabled_providers:
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


_BLOCKCHAIN_CONFIG_KEYS = frozenset(
    {
        "enabled",
        "rpc_url",
        "chain_id",
        "signer_secret_key",
        "signer_secret_namespace",
        "writes_enabled",
        "max_total_fee_wei",
        "receipt_timeout_seconds",
    }
)
_CANONICAL_UNSIGNED_DECIMAL_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")


@dataclass
class BlockchainToolRuntimeConfig:
    enabled: bool = False
    rpc_url: str = ""
    chain_id: int | None = None
    signer_secret_key: str = ""
    signer_secret_namespace: str = "blockchain"
    writes_enabled: bool = False
    max_total_fee_wei: str = "10000000000000000"
    receipt_timeout_seconds: int = 60


def _validate_blockchain_config(
    config: BlockchainToolRuntimeConfig,
) -> BlockchainToolRuntimeConfig:
    if not isinstance(config.enabled, bool) or not isinstance(
        config.writes_enabled, bool
    ):
        raise ConfigError("runtime.tools.blockchain enabled flags must be booleans.")
    if config.chain_id is not None and (
        not isinstance(config.chain_id, int)
        or isinstance(config.chain_id, bool)
        or config.chain_id < 1
    ):
        raise ConfigError(
            "runtime.tools.blockchain.chain_id must be an integer >= 1."
        )
    timeout = config.receipt_timeout_seconds
    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or not 1 <= timeout <= 300
    ):
        raise ConfigError(
            "runtime.tools.blockchain.receipt_timeout_seconds must be an integer "
            "from 1 through 300."
        )
    if (
        not _CANONICAL_UNSIGNED_DECIMAL_RE.fullmatch(config.max_total_fee_wei)
        or int(config.max_total_fee_wei) <= 0
    ):
        raise ConfigError(
            "runtime.tools.blockchain.max_total_fee_wei must be a canonical "
            "positive decimal string."
        )
    if not config.signer_secret_namespace:
        raise ConfigError(
            "runtime.tools.blockchain.signer_secret_namespace must be non-empty."
        )
    if config.enabled and (not config.rpc_url or config.chain_id is None):
        raise ConfigError(
            "runtime.tools.blockchain.rpc_url and chain_id are required when enabled."
        )
    if config.writes_enabled and not config.enabled:
        raise ConfigError(
            "runtime.tools.blockchain.writes_enabled=true requires enabled=true."
        )
    if config.writes_enabled and not config.signer_secret_key:
        raise ConfigError(
            "runtime.tools.blockchain.signer_secret_key is required when "
            "writes_enabled=true."
        )
    return config


def coerce_blockchain_tool_runtime_config(
    value: object,
) -> BlockchainToolRuntimeConfig | None:
    if value is None:
        return None
    if isinstance(value, BlockchainToolRuntimeConfig):
        return _validate_blockchain_config(value)
    if not isinstance(value, Mapping):
        raise ConfigError("runtime.tools.blockchain must be an object.")
    unknown = sorted(
        str(key) for key in value if key not in _BLOCKCHAIN_CONFIG_KEYS
    )
    if unknown:
        raise ConfigError(
            f"runtime.tools.blockchain contains unsupported keys: {unknown!r}."
        )
    for field_name in ("enabled", "writes_enabled"):
        if field_name in value and not isinstance(value[field_name], bool):
            raise ConfigError(
                f"runtime.tools.blockchain.{field_name} must be a boolean."
            )
    return _validate_blockchain_config(
        BlockchainToolRuntimeConfig(
            enabled=value.get("enabled", False),
            rpc_url=str(value.get("rpc_url", "")).strip(),
            chain_id=value.get("chain_id"),
            signer_secret_key=str(value.get("signer_secret_key", "")).strip(),
            signer_secret_namespace=str(
                value.get("signer_secret_namespace", "blockchain")
            ).strip(),
            writes_enabled=value.get("writes_enabled", False),
            max_total_fee_wei=str(
                value.get("max_total_fee_wei", "10000000000000000")
            ),
            receipt_timeout_seconds=value.get("receipt_timeout_seconds", 60),
        )
    )


def blockchain_tool_runtime_config_to_dict(
    config: BlockchainToolRuntimeConfig,
) -> dict[str, Any]:
    return {
        "enabled": config.enabled,
        "rpc_url": config.rpc_url,
        "chain_id": config.chain_id,
        "signer_secret_key": config.signer_secret_key,
        "signer_secret_namespace": config.signer_secret_namespace,
        "writes_enabled": config.writes_enabled,
        "max_total_fee_wei": config.max_total_fee_wei,
        "receipt_timeout_seconds": config.receipt_timeout_seconds,
    }
