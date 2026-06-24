"""Runtime config parsing helpers."""

from __future__ import annotations

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
from openminion.base.config.tool_selection import (
    _normalize_schema_exposure,
    _normalize_tool_selection_mode,
    _parse_tool_selection_config,
)
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
            "See the config-shape migration guide."
        )
    if isinstance(payload.get("thinking"), dict):
        raise ConfigError(
            "'runtime.thinking' as a policy object is no longer supported. "
            "Rename to 'runtime.thinking_policy'. "
            "See the config-shape migration guide."
        )
    if isinstance(payload.get("providers"), dict):
        raise ConfigError(
            "'runtime.providers' as a policy object is no longer supported. "
            "Rename to 'runtime.provider_policy'. "
            "See the config-shape migration guide."
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


def _build_runtime_config(effective_runtime_payload: dict[str, Any]) -> RuntimeConfig:
    _reject_legacy_runtime_shape(effective_runtime_payload)
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
    runtime_kwargs.update(
        {
            "env": (
                effective_runtime_payload.get("env")
                if isinstance(effective_runtime_payload.get("env"), dict)
                else {}
            ),
            "process_mode": _normalize_process_mode(
                effective_runtime_payload.get("process_mode")
            ),
            "session_context_chars_per_token": max(
                0.1,
                _as_float(
                    effective_runtime_payload.get("session_context_chars_per_token"),
                    4.0,
                ),
            ),
            "chat_turn_timeout_seconds": max(
                10.0,
                float(
                    effective_runtime_payload.get("chat_turn_timeout_seconds") or 90.0
                ),
            ),
            "chat_turn_max_attempts": max(
                1,
                _as_int(effective_runtime_payload.get("chat_turn_max_attempts"), 2),
            ),
            "memory_provider": _normalize_memory_provider(
                effective_runtime_payload.get("memory_provider")
            ),
            "memory_capsule_strategy": _normalize_memory_capsule_strategy(
                effective_runtime_payload.get("memory_capsule_strategy")
            ),
            "telemetry_exporter": _parse_telemetry_exporter_config(
                effective_runtime_payload.get("telemetry_exporter")
            ),
            "clarify_llm_temperature": _as_float(
                effective_runtime_payload.get("clarify_llm_temperature"),
                0.0,
            ),
            "complex_request_plan_policy": _normalize_complex_request_plan_policy(
                effective_runtime_payload.get("complex_request_plan_policy")
            ),
            "tool_selection": _parse_tool_selection_config(
                effective_runtime_payload.get("tool_selection")
            ),
            "tools": coerce_tool_runtime_config(effective_runtime_payload.get("tools")),
            "provider_policy": coerce_provider_runtime_policy_config(
                effective_runtime_payload.get("provider_policy"),
                field_path="system.runtime.provider_policy",
            ),
            "thinking_policy": coerce_thinking_runtime_policy_config(
                effective_runtime_payload.get("thinking_policy"),
                field_path="system.runtime.thinking_policy",
            ),
            "modes": coerce_mode_runtime_policy_map(
                effective_runtime_payload.get("modes"),
                field_path="system.runtime.modes",
            ),
            "plugins": coerce_plugin_runtime_policy_config(
                effective_runtime_payload.get("plugins"),
                field_path="system.runtime.plugins",
            ),
            "mcp_servers": coerce_mcp_server_configs(
                effective_runtime_payload.get("mcp_servers")
            ),
            "mcp_publish": coerce_mcp_publish_config(
                effective_runtime_payload.get("mcp_publish")
            ),
            "mcp_sampling_mode": normalize_mcp_sampling_mode(
                effective_runtime_payload.get("mcp_sampling_mode")
            ),
            "mcp_discovery_cache_ttl_seconds": max(
                0.0,
                _as_float(
                    effective_runtime_payload.get("mcp_discovery_cache_ttl_seconds"),
                    0.0,
                ),
            ),
            "mcp_deferred_discovery_enabled": _as_bool(
                effective_runtime_payload.get("mcp_deferred_discovery_enabled"),
                False,
            ),
        }
    )
    if "tool_schema_shortlisting_enabled" in effective_runtime_payload:
        runtime_kwargs["tool_schema_shortlisting_enabled"] = _as_bool(
            effective_runtime_payload.get("tool_schema_shortlisting_enabled"),
            True,
        )
        runtime_kwargs["has_tool_schema_shortlisting_enabled"] = True
    if "allow_background_write_authorization" in effective_runtime_payload:
        runtime_kwargs["allow_background_write_authorization"] = _as_bool(
            effective_runtime_payload.get("allow_background_write_authorization"),
            False,
        )
        runtime_kwargs["has_allow_background_write_authorization"] = True
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
            "telemetry_exporter": {
                "enabled": bool(config.telemetry_exporter.enabled),
                "endpoint": str(config.telemetry_exporter.endpoint or ""),
                "service_name": str(
                    config.telemetry_exporter.service_name or "openminion"
                ),
                "protocol": str(config.telemetry_exporter.protocol or "http"),
                "include_assistant_body": bool(
                    config.telemetry_exporter.include_assistant_body
                ),
                "sample_rate": float(config.telemetry_exporter.sample_rate),
                "backend": str(config.telemetry_exporter.backend or ""),
                "headers": dict(config.telemetry_exporter.headers or {}),
            },
            "clarify_llm_temperature": float(config.clarify_llm_temperature),
            "complex_request_plan_policy": _normalize_complex_request_plan_policy(
                config.complex_request_plan_policy
            ),
            "tool_selection": {
                "mode": _normalize_tool_selection_mode(config.tool_selection.mode),
                "max_tools_per_turn": config.tool_selection.max_tools_per_turn,
                "tool_prompt_token_budget": (
                    config.tool_selection.tool_prompt_token_budget
                ),
                "enforce_required_tool_call": bool(
                    config.tool_selection.enforce_required_tool_call
                ),
                "allow_runtime_direct_fallback": bool(
                    config.tool_selection.allow_runtime_direct_fallback
                ),
                "bindings": dict(config.tool_selection.bindings),
                "bindings_fallback": {
                    key: list(value)
                    for key, value in config.tool_selection.bindings_fallback.items()
                },
                "capabilities": {
                    key: {
                        "primary": value.primary,
                        "fallback_tools": list(value.fallback_tools),
                    }
                    for key, value in config.tool_selection.capabilities.items()
                },
                "runtime_bindings": {
                    key: {
                        "primary": value.primary,
                        "fallback_tools": list(value.fallback_tools),
                    }
                    for key, value in config.tool_selection.runtime_bindings.items()
                },
                "runtime_binding_selection_strategy": (
                    config.tool_selection.runtime_binding_selection_strategy
                ),
                "runtime_fallback_on": list(config.tool_selection.runtime_fallback_on),
                "runtime_no_fallback_on": list(
                    config.tool_selection.runtime_no_fallback_on
                ),
                "schema_exposure": _normalize_schema_exposure(
                    config.tool_selection.schema_exposure
                ),
                "validation_retry_max": config.tool_selection.validation_retry_max,
            },
            "tools": tool_runtime_config_to_dict(config.tools),
            "mcp_servers": [
                {
                    "name": item.name,
                    "transport": item.transport,
                    "command": list(item.command),
                    "url": item.url,
                    "authorization": item.authorization.redacted_dict(),
                    "env": dict(item.env),
                    "env_secret_refs": dict(item.env_secret_refs),
                    "cwd": item.cwd,
                    "startup_timeout_seconds": item.startup_timeout_seconds,
                    "request_timeout_seconds": item.request_timeout_seconds,
                    "stderr_buffer_bytes": item.stderr_buffer_bytes,
                    "trusted": item.trusted,
                    "stdio_sandbox": {
                        "require_trust": item.stdio_sandbox.require_trust,
                        "cwd_allowlist": list(item.stdio_sandbox.cwd_allowlist),
                        "env_allowlist": list(item.stdio_sandbox.env_allowlist),
                        "package_name": item.stdio_sandbox.package_name,
                        "package_version": item.stdio_sandbox.package_version,
                        "trust_reason": item.stdio_sandbox.trust_reason,
                    },
                    "package_metadata": item.package_metadata.to_dict(),
                    "tool_risk_overrides": [
                        {
                            "pattern": override.pattern,
                            "min_scope": override.min_scope,
                            "dangerous": override.dangerous,
                            "idempotent": override.idempotent,
                        }
                        for override in item.tool_risk_overrides
                    ],
                    "approval": {
                        "mode": item.approval.mode,
                        "tool_patterns": list(item.approval.tool_patterns),
                        "risk_levels": list(item.approval.risk_levels),
                    },
                }
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
        payload["trailer_guidance_variant"] = dict(
            config.trailer_guidance_variant or {}
        )
    provider_policy_payload = provider_runtime_policy_to_dict(config.provider_policy)
    if provider_policy_payload:
        payload["provider_policy"] = provider_policy_payload
    thinking_policy_payload = thinking_runtime_policy_to_dict(config.thinking_policy)
    if thinking_policy_payload:
        payload["thinking_policy"] = thinking_policy_payload
    modes_payload = mode_runtime_policy_to_dict(config.modes)
    if modes_payload:
        payload["modes"] = modes_payload
    plugins_payload = plugin_runtime_policy_to_dict(config.plugins)
    if plugins_payload:
        payload["plugins"] = plugins_payload
    return payload


def _system_runtime_mirror(config: RuntimeConfig) -> dict[str, Any]:
    system_payload: dict[str, Any] = {
        "tools": tool_runtime_config_to_dict(config.tools),
        "provider_policy": provider_runtime_policy_to_dict(config.provider_policy),
        "thinking_policy": thinking_runtime_policy_to_dict(config.thinking_policy),
        "modes": mode_runtime_policy_to_dict(config.modes),
        "plugins": plugin_runtime_policy_to_dict(config.plugins),
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
