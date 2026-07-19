"""Parse and serialize OpenMinion config payloads."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from openminion.base.config.base import ConfigError
from openminion.base.config.env import resolve_environment_config
from openminion.base.config.core import OpenMinionConfig
from openminion.base.constants import (
    OPENMINION_STORAGE_BACKEND_ENV,
    OPENMINION_STORAGE_POSTGRES_POOL_MAX_ENV,
    OPENMINION_STORAGE_POSTGRES_POOL_MIN_ENV,
    OPENMINION_STORAGE_POSTGRES_URL_ENV,
)

from .profiles import _parse_agent_profiles
from .channels import _normalize_channel_defaults
from .security import (
    _build_gateway_security_sections,
    _gateway_security_to_payload,
)
from .mapping import mapping_payload
from .providers import (
    _build_providers_config,
    _extract_provider_payloads,
    _providers_config_to_payload,
)
from .runtime import (
    _build_runtime_config,
    _runtime_config_to_payload,
    _system_runtime_mirror,
)
from .storage import (
    _build_storage_context_sections,
    _storage_context_to_payload,
)

_KNOWN_TOP_LEVEL_KEYS = frozenset(
    "gateway channel_policy channel_authenticity security runtime storage vector "
    "self_improvement context identity providers action_policy agents default_agent "
    "enabled_channels channels enabled_plugins system".split()
)


def _merge_mapping_dicts(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_mapping_dicts(existing, value)
            continue
        merged[key] = deepcopy(value)
    return merged


def openminion_config_from_dict(payload: dict[str, Any]) -> OpenMinionConfig:
    if "agent" in payload:
        raise ConfigError(
            "Legacy 'agent' top-level block is no longer supported. "
            "Move fields to 'runtime.*' (system-wide) or 'agents.<id>.*' "
            "(per-agent). "
            "See docs/reference/config-shape-migration-2026.md."
        )
    system_payload = mapping_payload(payload, "system")
    system_runtime_payload = mapping_payload(system_payload, "runtime")
    system_providers_payload = mapping_payload(system_payload, "providers")

    if "action_policy" in payload and not isinstance(
        payload.get("action_policy"), dict
    ):
        raise ConfigError("action_policy must be an object")
    default_agent = str(payload.get("default_agent", "") or "").strip()
    effective_runtime_payload = _merge_mapping_dicts(
        mapping_payload(payload, "runtime"),
        system_runtime_payload,
    )
    if "context" in payload and not isinstance(payload.get("context"), dict):
        raise ConfigError("context must be an object")
    effective_providers_payload = _merge_mapping_dicts(
        mapping_payload(payload, "providers"),
        system_providers_payload,
    )
    if "provider" in payload:
        raise ConfigError(
            "Legacy config shape `provider` is no longer supported. "
            "Use `agent.provider` and `providers.<name>` instead."
        )

    normalized_channel_defaults = _normalize_channel_defaults(payload)
    sections = {
        name: mapping_payload(payload, name)
        for name in (
            "gateway channel_policy channel_authenticity security action_policy "
            "storage vector self_improvement context identity"
        ).split()
    }
    resolved_env = resolve_environment_config()
    gateway_security_sections = _build_gateway_security_sections(
        gateway_payload=sections["gateway"],
        channel_policy_payload=sections["channel_policy"],
        channel_authenticity_payload=sections["channel_authenticity"],
        security_payload=sections["security"],
        action_policy_payload=sections["action_policy"],
        normalized_channel_defaults=normalized_channel_defaults,
    )
    storage_context_sections = _build_storage_context_sections(
        storage_payload=sections["storage"],
        vector_payload=sections["vector"],
        self_improvement_payload=sections["self_improvement"],
        context_payload=sections["context"],
        identity_payload=sections["identity"],
        storage_backend_env=resolved_env.get(OPENMINION_STORAGE_BACKEND_ENV, ""),
        storage_postgres_url_env=resolved_env.get(
            OPENMINION_STORAGE_POSTGRES_URL_ENV, ""
        ),
        storage_postgres_pool_min_env=resolved_env.get(
            OPENMINION_STORAGE_POSTGRES_POOL_MIN_ENV, ""
        ),
        storage_postgres_pool_max_env=resolved_env.get(
            OPENMINION_STORAGE_POSTGRES_POOL_MAX_ENV, ""
        ),
    )
    module_configs = {
        str(key): dict(value)
        for key, value in payload.items()
        if key not in _KNOWN_TOP_LEVEL_KEYS and isinstance(value, dict)
    }

    return OpenMinionConfig(
        **gateway_security_sections,
        **storage_context_sections,
        runtime=_build_runtime_config(effective_runtime_payload),
        providers=_build_providers_config(
            _extract_provider_payloads(effective_providers_payload)
        ),
        agents=_parse_agent_profiles(mapping_payload(payload, "agents")),
        default_agent=default_agent,
        enabled_channels=normalized_channel_defaults["enabled_channels"],
        channels=normalized_channel_defaults["channels"],
        enabled_plugins=normalized_channel_defaults["enabled_plugins"],
        module_configs=module_configs,
    )


def openminion_config_to_dict(config: OpenMinionConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {
        **_gateway_security_to_payload(config),
        "runtime": _runtime_config_to_payload(config.runtime),
        **_storage_context_to_payload(config),
        "providers": _providers_config_to_payload(config.providers),
        "agents": {
            key: profile.to_dict()
            for key, profile in sorted(config.agents.items(), key=lambda item: item[0])
        },
        "enabled_channels": list(config.enabled_channels),
        "channels": deepcopy(config.channels),
        "enabled_plugins": list(config.enabled_plugins),
        "system": {
            "runtime": _system_runtime_mirror(config.runtime),
            "providers": _providers_config_to_payload(config.providers),
        },
    }
    if str(config.default_agent or "").strip():
        payload["default_agent"] = str(config.default_agent).strip()
    for key, value in dict(getattr(config, "module_configs", {}) or {}).items():
        if not str(key).strip() or not isinstance(value, dict):
            continue
        payload[str(key)] = dict(value)
    return payload
