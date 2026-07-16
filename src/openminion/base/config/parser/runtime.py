"""Runtime config parsing helpers."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any

from openminion.base.config.base import ConfigError
from openminion.base.config.runtime.capability import (
    coerce_mode_runtime_policy_map,
    coerce_plugin_runtime_policy_config,
    coerce_provider_runtime_policy_config,
    coerce_thinking_runtime_policy_config,
    mode_runtime_policy_to_dict,
    plugin_runtime_policy_to_dict,
    provider_runtime_policy_to_dict,
    thinking_runtime_policy_to_dict,
)
from openminion.base.config.mcp import (
    coerce_mcp_publish_config,
    coerce_mcp_server_configs,
    mcp_publish_config_to_dict,
    normalize_mcp_sampling_mode,
)
from openminion.base.config.parse import (
    _as_bool,
    _as_float,
    _as_int,
    _normalize_complex_request_plan_policy,
    _normalize_memory_capsule_strategy,
    _normalize_memory_provider,
    _normalize_process_mode,
)
from openminion.base.config.runtime import OTELExporterConfig, RuntimeConfig
from openminion.base.config.runtime.tools import (
    coerce_tool_runtime_config,
    tool_runtime_config_to_dict,
)
from openminion.base.config.tool_selection import _parse_tool_selection_config
from .profiles import _parse_trailer_guidance_variant_map

_STRING_DEFAULTS = (
    ("log_level", "INFO"),
    ("ipc_host", "127.0.0.1"),
    ("ipc_token", ""),
    ("daemon_pid_file", ""),
    ("daemon_log_file", ""),
    ("session_archive_root_path", ""),
    ("memory_root_path", ""),
    ("tool_workspace_root", ""),
    ("telemetry_db_path", ""),
    ("reactions_default_policy", "allow"),
    ("clarify_llm_provider", ""),
    ("clarify_llm_model", ""),
)
_BOOL_DEFAULTS = (
    ("demo_mode", False),
    ("daemon_auto_start", False),
    ("session_archive_enabled", True),
    ("session_summary_enrichment_enabled", False),
    ("memory_enabled", True),
    ("memory_dynamic_retrieval_enabled", False),
    ("telemetry_enabled", False),
    ("debug_enabled", True),
    ("debug_cli_enabled", True),
    ("debug_api_enabled", True),
    ("debug_chat_enabled", True),
    ("debug_module_probes_enabled", True),
    ("menu_pairing_enabled", True),
    ("reactions_enabled", True),
)
_INT_MIN_DEFAULTS = (
    ("ipc_port", 0, 18789),
    ("session_keep_recent_messages", 1, 20),
    ("session_max_compact_per_turn", 1, 100),
    ("session_summary_max_chars", 256, 8000),
    ("session_archive_ref_limit", 1, 3),
    ("session_context_token_budget", 0, 0),
    ("session_thread_ttl_seconds", 0, 0),
    ("session_writer_lease_seconds", 0, 0),
    ("agent_loop_max_steps", 1, 4),
    ("agent_loop_tool_result_max_chars", 256, 4000),
    ("brain_turn_timeout_seconds", 1, 120),
    ("memory_retrieval_max_chars", 256, 2000),
    ("memory_log_retention_days", 1, 30),
    ("memory_max_facts", 1, 200),
    ("memory_max_todos", 1, 200),
    ("memory_patch_retention_count", 1, 200),
    ("memory_lock_ttl_seconds", 1, 30),
    ("memory_lock_acquire_timeout_seconds", 1, 5),
    ("clarify_llm_max_tokens", 1, 256),
)


def _reject_legacy_runtime_shape(payload: dict[str, Any]) -> None:
    if "brain" in payload:
        raise ConfigError(
            "Nested 'runtime.brain.*' is no longer supported. "
            "Flatten to 'runtime.*'. "
            "See docs/reference/config-shape-migration-2026.md."
        )
    if isinstance(payload.get("thinking"), dict):
        raise ConfigError(
            "'runtime.thinking' as a policy object is no longer supported. "
            "Rename to 'runtime.thinking_policy'. "
            "See docs/reference/config-shape-migration-2026.md."
        )
    if isinstance(payload.get("providers"), dict):
        raise ConfigError(
            "'runtime.providers' as a policy object is no longer supported. "
            "Rename to 'runtime.provider_policy'. "
            "See docs/reference/config-shape-migration-2026.md."
        )


def _parse_telemetry_exporter_config(raw: Any) -> OTELExporterConfig:
    if isinstance(raw, OTELExporterConfig):
        return raw
    if not isinstance(raw, dict):
        return OTELExporterConfig()
    headers_raw = raw.get("headers")
    headers: dict[str, str] = {}
    if isinstance(headers_raw, dict):
        for key, value in headers_raw.items():
            clean_key = str(key or "").strip()
            if not clean_key:
                continue
            headers[clean_key] = str(value or "")
    return OTELExporterConfig(
        enabled=_as_bool(raw.get("enabled"), False),
        endpoint=str(raw.get("endpoint", "") or "").strip(),
        protocol=str(raw.get("protocol", "http") or "http").strip() or "http",
        service_name=(
            str(raw.get("service_name", "openminion") or "openminion").strip()
            or "openminion"
        ),
        sample_rate=max(0.0, min(1.0, _as_float(raw.get("sample_rate"), 1.0))),
        include_assistant_body=_as_bool(raw.get("include_assistant_body"), False),
        backend=str(raw.get("backend", "") or "").strip(),
        headers=headers,
    )


def _config_value_to_payload(value: Any) -> Any:
    if is_dataclass(value):
        return {
            item.name: _config_value_to_payload(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, dict):
        return {key: _config_value_to_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_config_value_to_payload(item) for item in value]
    return value


def _mcp_server_to_payload(server: Any) -> dict[str, Any]:
    payload = {
        item.name: _config_value_to_payload(getattr(server, item.name))
        for item in fields(server)
        if item.name != "authorization"
    }
    payload["authorization"] = server.authorization.redacted_dict()
    return payload


def _runtime_special_values(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "env": payload.get("env") if isinstance(payload.get("env"), dict) else {},
        "process_mode": _normalize_process_mode(payload.get("process_mode")),
        "session_context_chars_per_token": max(
            0.1, _as_float(payload.get("session_context_chars_per_token"), 4.0)
        ),
        "chat_turn_timeout_seconds": max(
            10.0, float(payload.get("chat_turn_timeout_seconds") or 90.0)
        ),
        "chat_turn_max_attempts": max(
            1, _as_int(payload.get("chat_turn_max_attempts"), 2)
        ),
        "memory_provider": _normalize_memory_provider(payload.get("memory_provider")),
        "memory_capsule_strategy": _normalize_memory_capsule_strategy(
            payload.get("memory_capsule_strategy")
        ),
        "telemetry_exporter": _parse_telemetry_exporter_config(
            payload.get("telemetry_exporter")
        ),
        "clarify_llm_temperature": _as_float(
            payload.get("clarify_llm_temperature"), 0.0
        ),
        "complex_request_plan_policy": _normalize_complex_request_plan_policy(
            payload.get("complex_request_plan_policy")
        ),
        "tool_selection": _parse_tool_selection_config(payload.get("tool_selection")),
        "tools": coerce_tool_runtime_config(payload.get("tools")),
        "provider_policy": coerce_provider_runtime_policy_config(
            payload.get("provider_policy"), field_path="system.runtime.provider_policy"
        ),
        "thinking_policy": coerce_thinking_runtime_policy_config(
            payload.get("thinking_policy"), field_path="system.runtime.thinking_policy"
        ),
        "modes": coerce_mode_runtime_policy_map(
            payload.get("modes"), field_path="system.runtime.modes"
        ),
        "plugins": coerce_plugin_runtime_policy_config(
            payload.get("plugins"), field_path="system.runtime.plugins"
        ),
        "ops": dict(payload.get("ops", {})),
        "mcp_servers": coerce_mcp_server_configs(payload.get("mcp_servers")),
        "mcp_publish": coerce_mcp_publish_config(payload.get("mcp_publish")),
        "mcp_sampling_mode": normalize_mcp_sampling_mode(
            payload.get("mcp_sampling_mode")
        ),
        "mcp_discovery_cache_ttl_seconds": max(
            0.0, _as_float(payload.get("mcp_discovery_cache_ttl_seconds"), 0.0)
        ),
        "mcp_deferred_discovery_enabled": _as_bool(
            payload.get("mcp_deferred_discovery_enabled"), False
        ),
    }


def _build_runtime_config(effective_runtime_payload: dict[str, Any]) -> RuntimeConfig:
    _reject_legacy_runtime_shape(effective_runtime_payload)
    if not isinstance(effective_runtime_payload.get("ops", {}), dict):
        raise ConfigError("'runtime.ops' must be an object")
    runtime_kwargs: dict[str, Any] = {
        key: str(effective_runtime_payload.get(key, default))
        for key, default in _STRING_DEFAULTS
    }
    runtime_kwargs.update(
        {
            key: _as_bool(effective_runtime_payload.get(key), default)
            for key, default in _BOOL_DEFAULTS
        }
    )
    runtime_kwargs.update(
        {
            key: max(minimum, _as_int(effective_runtime_payload.get(key), default))
            for key, minimum, default in _INT_MIN_DEFAULTS
        }
    )
    runtime_kwargs.update(_runtime_special_values(effective_runtime_payload))
    optional_bools = (
        ("tool_schema_shortlisting_enabled", True),
        ("allow_background_write_authorization", False),
    )
    for key, default in optional_bools:
        if key in effective_runtime_payload:
            runtime_kwargs[key] = _as_bool(effective_runtime_payload.get(key), default)
            runtime_kwargs[f"has_{key}"] = True
    if "trailer_guidance_variant" in effective_runtime_payload:
        variant, has_variant = _parse_trailer_guidance_variant_map(
            effective_runtime_payload.get("trailer_guidance_variant"),
            field_path="runtime.trailer_guidance_variant",
        )
        runtime_kwargs["trailer_guidance_variant"] = variant
        runtime_kwargs["has_trailer_guidance_variant"] = has_variant
    return RuntimeConfig(**runtime_kwargs)


def _runtime_config_to_payload(config: RuntimeConfig) -> dict[str, Any]:
    payload = {key: getattr(config, key) for key, _ in _STRING_DEFAULTS}
    payload.update({key: bool(getattr(config, key)) for key, _ in _BOOL_DEFAULTS})
    payload.update({key: getattr(config, key) for key, _, _ in _INT_MIN_DEFAULTS})
    payload.update(
        {
            "env": dict(config.env),
            "process_mode": _normalize_process_mode(config.process_mode),
            "session_context_chars_per_token": config.session_context_chars_per_token,
            "chat_turn_timeout_seconds": config.chat_turn_timeout_seconds,
            "chat_turn_max_attempts": config.chat_turn_max_attempts,
            "memory_provider": _normalize_memory_provider(config.memory_provider),
            "memory_capsule_strategy": _normalize_memory_capsule_strategy(
                config.memory_capsule_strategy
            ),
            "telemetry_exporter": _config_value_to_payload(config.telemetry_exporter),
            "clarify_llm_temperature": float(config.clarify_llm_temperature),
            "complex_request_plan_policy": _normalize_complex_request_plan_policy(
                config.complex_request_plan_policy
            ),
            "tool_selection": _config_value_to_payload(config.tool_selection),
            "tools": tool_runtime_config_to_dict(config.tools),
            "ops": _config_value_to_payload(config.ops),
            "mcp_servers": [
                _mcp_server_to_payload(item)
                for item in coerce_mcp_server_configs(config.mcp_servers)
            ],
            "mcp_publish": mcp_publish_config_to_dict(config.mcp_publish),
            "mcp_sampling_mode": normalize_mcp_sampling_mode(config.mcp_sampling_mode),
            "mcp_discovery_cache_ttl_seconds": float(
                config.mcp_discovery_cache_ttl_seconds
            ),
            "mcp_deferred_discovery_enabled": bool(
                config.mcp_deferred_discovery_enabled
            ),
        }
    )
    if config.has_tool_schema_shortlisting_enabled:
        payload["tool_schema_shortlisting_enabled"] = bool(
            config.tool_schema_shortlisting_enabled
        )
    if config.has_allow_background_write_authorization:
        payload["allow_background_write_authorization"] = bool(
            config.allow_background_write_authorization
        )
    if config.has_trailer_guidance_variant:
        variant = dict(config.trailer_guidance_variant or {})
        payload["trailer_guidance_variant"] = variant
    optional_policies = {
        "provider_policy": provider_runtime_policy_to_dict(config.provider_policy),
        "thinking_policy": thinking_runtime_policy_to_dict(config.thinking_policy),
        "modes": mode_runtime_policy_to_dict(config.modes),
        "plugins": plugin_runtime_policy_to_dict(config.plugins),
    }
    payload.update({key: value for key, value in optional_policies.items() if value})
    return payload


def _system_runtime_mirror(config: RuntimeConfig) -> dict[str, Any]:
    system_payload: dict[str, Any] = {
        "tools": tool_runtime_config_to_dict(config.tools),
        "provider_policy": provider_runtime_policy_to_dict(config.provider_policy),
        "thinking_policy": thinking_runtime_policy_to_dict(config.thinking_policy),
        "modes": mode_runtime_policy_to_dict(config.modes),
        "plugins": plugin_runtime_policy_to_dict(config.plugins),
        "ops": _config_value_to_payload(config.ops),
    }
    brain_mirror: dict[str, Any] = {}
    if config.has_tool_schema_shortlisting_enabled:
        brain_mirror["tool_schema_shortlisting_enabled"] = bool(
            config.tool_schema_shortlisting_enabled
        )
    if config.has_allow_background_write_authorization:
        brain_mirror["allow_background_write_authorization"] = bool(
            config.allow_background_write_authorization
        )
    if config.has_trailer_guidance_variant:
        brain_mirror["trailer_guidance_variant"] = dict(
            config.trailer_guidance_variant or {}
        )
    system_payload.update(brain_mirror)
    return {key: value for key, value in system_payload.items() if value}
