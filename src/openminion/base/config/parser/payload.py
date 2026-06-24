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
    known_top_level_keys = {
        "gateway",
        "channel_policy",
        "channel_authenticity",
        "security",
        "runtime",
        "storage",
        "vector",
        "self_improvement",
        "context",
        "identity",
        "providers",
        "action_policy",
        "agents",
        "default_agent",
        "enabled_channels",
        "channels",
        "enabled_plugins",
        "system",
    }
    if "agent" in payload:
        raise ConfigError(
            "Legacy 'agent' top-level block is no longer supported. "
            "Move fields to 'runtime.*' (system-wide) or 'agents.<id>.*' "
            "(per-agent). "
            "See the config-shape migration guide."
        )
    system_payload = mapping_payload(payload, "system")
    system_runtime_payload = mapping_payload(system_payload, "runtime")
    system_providers_payload = mapping_payload(system_payload, "providers")

    gateway_payload = mapping_payload(payload, "gateway")
    channel_policy_payload = mapping_payload(payload, "channel_policy")
    channel_authenticity_payload = mapping_payload(payload, "channel_authenticity")
    security_payload = mapping_payload(payload, "security")

    if "action_policy" in payload and not isinstance(
        payload.get("action_policy"), dict
    ):
        raise ConfigError("action_policy must be an object")
    action_policy_payload = mapping_payload(payload, "action_policy")

    default_agent = str(payload.get("default_agent", "") or "").strip()

    runtime_payload = mapping_payload(payload, "runtime")
    effective_runtime_payload = _merge_mapping_dicts(
        runtime_payload,
        system_runtime_payload,
    )

    storage_payload = mapping_payload(payload, "storage")
    vector_payload = mapping_payload(payload, "vector")
    self_improvement_payload = mapping_payload(payload, "self_improvement")

    if "context" in payload and not isinstance(payload.get("context"), dict):
        raise ConfigError("context must be an object")
    context_payload = mapping_payload(payload, "context")
    identity_payload = mapping_payload(payload, "identity")

    providers_payload = mapping_payload(payload, "providers")
    effective_providers_payload = _merge_mapping_dicts(
        providers_payload,
        system_providers_payload,
    )
    if "provider" in payload:
        raise ConfigError(
            "Legacy config shape `provider` is no longer supported. "
            "Use `agent.provider` and `providers.<name>` instead."
        )

    normalized_channel_defaults = _normalize_channel_defaults(payload)
    resolved_env = resolve_environment_config()
    gateway_security_sections = _build_gateway_security_sections(
        gateway_payload=gateway_payload,
        channel_policy_payload=channel_policy_payload,
        channel_authenticity_payload=channel_authenticity_payload,
        security_payload=security_payload,
        action_policy_payload=action_policy_payload,
        normalized_channel_defaults=normalized_channel_defaults,
    )
    storage_context_sections = _build_storage_context_sections(
        storage_payload=storage_payload,
        vector_payload=vector_payload,
        self_improvement_payload=self_improvement_payload,
        context_payload=context_payload,
        identity_payload=identity_payload,
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
        if key not in known_top_level_keys and isinstance(value, dict)
    }

    return OpenMinionConfig(
        gateway=gateway_security_sections["gateway"],
        channel_policy=gateway_security_sections["channel_policy"],
        channel_authenticity=gateway_security_sections["channel_authenticity"],
        security=gateway_security_sections["security"],
        action_policy=gateway_security_sections["action_policy"],
        runtime=_build_runtime_config(effective_runtime_payload),
        storage=storage_context_sections["storage"],
        vector=storage_context_sections["vector"],
        self_improvement=storage_context_sections["self_improvement"],
        context=storage_context_sections["context"],
        identity=storage_context_sections["identity"],
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
