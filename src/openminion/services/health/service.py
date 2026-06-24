import logging
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Optional

from openminion.api.runtime import APIRuntime
from openminion.base.config import ConfigManager, EnvironmentConfig
from openminion.base.config.runtime.capability import resolve_plugin_runtime_policy
from openminion.base.config.core import resolve_default_agent_id
from openminion.modules.llm.providers.factory import SUPPORTED_PROVIDERS, build_provider
from openminion.modules.storage.runtime.context import build_runtime_storage
from openminion.modules.storage.runtime.sqlite import resolve_database_path
from openminion.services.config import resolve_services_env
from openminion.services.bootstrap.config import bootstrap_config_manager
from .observability import (
    _build_brain_llm_mode_observability_check,
    _resolve_brain_observability_event_limit,
)
from .lifecycle import (
    _evaluate_supervision_decision,
    _load_lifecycle_facts,
    _supervision_policies_for_lifecycle_facts,
)
from .probes import (
    ProbeResult,
    probe_channels_enabled,
    probe_config_exists,
    probe_default_channel_in_enabled,
    probe_plugins_enabled,
    probe_provider_key,
    probe_provider_session,
    probe_provider_supported,
    probe_runtime_bootstrap,
    probe_storage_ready,
)
from .reporting import (
    _build_dependency_timing_summary,
    _build_latency_budget_check,
    _build_operator_hints,
    _build_readiness_by_group,
    _duration_ms,
    _emit_normalization_telemetry,
    _normalize_metrics_consistency,
    _resolve_health_latency_budget_threshold_ms,
)
from .snapshot import (
    _build_normalized_health_snapshot,
    _merge_health_counts_with_supervision_components,
)
from .types import HealthCheck
from openminion.services.runtime.lifecycle import LifecycleService
from openminion.services.security.policy import SecurityPolicyEngine, ToolBudgetPolicy

_HEALTH_LOG = logging.getLogger("openminion.health")


def _health_check_from_probe(probe: ProbeResult) -> HealthCheck:
    return HealthCheck(
        id=probe.id,
        status=probe.status,
        message=probe.message,
        details=probe.details,
    )


def _build_integration_mode_check(config: Any) -> HealthCheck:
    integration_mode = getattr(
        config.gateway, "brain_integration_mode", "contextctl_authoritative"
    )
    if str(integration_mode or "").strip().lower() == "ctxctl_authoritative":
        integration_mode = "contextctl_authoritative"
    if integration_mode == "contextctl_authoritative":
        return HealthCheck(
            id="runtime.integration_mode",
            status="ok",
            message=f"Integration mode: {integration_mode}",
            details={"mode": integration_mode},
        )
    return HealthCheck(
        id="runtime.integration_mode",
        status="fail",
        message=(
            f"Unsupported integration mode '{integration_mode}'. "
            "Legacy integration modes are disabled."
        ),
        details={
            "mode": integration_mode,
            "expected": "contextctl_authoritative",
        },
    )


def _build_health_payload(
    *,
    checks: list[HealthCheck],
    snapshot_started_at: float,
    resolved_home_root: Path | None,
    provider_name: str,
    default_agent_id: str,
    default_agent_profile: Any,
    storage_path: Path,
    env_config: EnvironmentConfig,
    metrics_consistency: Optional[Dict[str, Any]],
) -> dict[str, Any]:
    fail_count = len([check for check in checks if check.status == "fail"])
    warn_count = len([check for check in checks if check.status == "warn"])
    ok_count = len([check for check in checks if check.status == "ok"])
    readiness_by_group = _build_readiness_by_group(checks)
    dependency_timing_ms = _build_dependency_timing_summary(
        checks=checks,
        started_at=snapshot_started_at,
    )
    observed_at = datetime.now(tz=timezone.utc).isoformat()
    lifecycle_facts = _load_lifecycle_facts(home_root=resolved_home_root)
    normalized_health_snapshot = _build_normalized_health_snapshot(
        checks=checks,
        provider_name=provider_name,
        default_channel=default_agent_profile.default_channel or "console",
        storage_path=str(storage_path),
        observed_at=observed_at,
        lifecycle_facts=lifecycle_facts,
    )
    normalized_summary_state = str(
        (normalized_health_snapshot.get("summary", {}) or {}).get("health_state", "")
    ).strip()
    ok_count, warn_count, fail_count = _merge_health_counts_with_supervision_components(
        ok_count=ok_count,
        warn_count=warn_count,
        fail_count=fail_count,
        normalized_health_snapshot=normalized_health_snapshot,
    )
    payload_ok = fail_count == 0 and normalized_summary_state != "failed"

    payload = {
        "status": "ok" if payload_ok else "fail",
        "ok": payload_ok,
        "timestamp_utc": observed_at,
        "agent": default_agent_profile.name or default_agent_id,
        "provider": provider_name,
        "default_channel": default_agent_profile.default_channel or "console",
        "counts": {"ok": ok_count, "warn": warn_count, "fail": fail_count},
        "readiness_by_group": readiness_by_group,
        "dependency_timing_ms": dependency_timing_ms,
        "normalized_health_snapshot": normalized_health_snapshot,
        "operator_hints": _build_operator_hints(env_config),
        "checks": [check.to_dict() for check in checks],
    }
    _emit_normalization_telemetry(
        checks=checks,
        normalized_health_snapshot=normalized_health_snapshot,
        fail_count=fail_count,
        warn_count=warn_count,
    )
    if metrics_consistency is not None:
        payload["consistency"] = _normalize_metrics_consistency(metrics_consistency)
    return payload


def collect_health_snapshot(
    config_path: Optional[str],
    runtime: Optional[APIRuntime] = None,
    metrics_consistency: Optional[Dict[str, Any]] = None,
    probe_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    snapshot_started_at = perf_counter()
    checks: List[HealthCheck] = []
    resolved_home_root: Path | None = None
    env_config: EnvironmentConfig
    if runtime is not None:
        config = runtime.config
        resolved_config_path = runtime.config_path
        resolved_home_root = runtime.home_root
        env_config = (
            runtime.config_manager.env
            if runtime.config_manager is not None
            else resolve_services_env(runtime_env=getattr(config.runtime, "env", None))
        )
    else:
        manager = ConfigManager.load(config_path)
        bootstrap_config_manager(manager)
        config = manager.base_config
        if manager.config_path is None:
            raise RuntimeError("ConfigManager.load did not resolve a config path")
        resolved_config_path = manager.config_path
        resolved_home_root = manager.home_root
        env_config = manager.env
    storage_path = resolve_database_path(config.storage.path)
    default_agent_id = resolve_default_agent_id(config)
    default_agent_profile = config.agents[default_agent_id]
    provider_name = (default_agent_profile.provider or "echo").strip().lower() or "echo"
    runtime_storage_probe = None

    config_exists_started_at = perf_counter()
    config_exists_check = _health_check_from_probe(
        probe_config_exists(resolved_config_path)
    )
    config_exists_check.duration_ms = _duration_ms(config_exists_started_at)
    checks.append(config_exists_check)

    storage_started_at = perf_counter()
    storage_probe = probe_storage_ready(storage_path, keep_open=True)
    storage_check = _health_check_from_probe(storage_probe.probe)
    runtime_storage_probe = storage_probe.runtime_storage
    storage_check.duration_ms = _duration_ms(storage_started_at)
    checks.append(storage_check)

    provider_supported_started_at = perf_counter()
    provider_supported_check = _health_check_from_probe(
        probe_provider_supported(
            provider_name=provider_name,
            supported_providers=SUPPORTED_PROVIDERS,
        )
    )
    provider_supported_check.duration_ms = _duration_ms(provider_supported_started_at)
    checks.append(provider_supported_check)

    provider_key_started_at = perf_counter()
    provider_key_probe = probe_provider_key(config=config, provider_name=provider_name)
    if provider_key_probe is not None:
        provider_key_check = _health_check_from_probe(provider_key_probe)
        provider_key_check.duration_ms = _duration_ms(provider_key_started_at)
        checks.append(provider_key_check)

    provider_session_started_at = perf_counter()
    provider_session_probe = probe_provider_session(
        config=config,
        provider_name=provider_name,
    )
    if provider_session_probe is not None:
        provider_session_check = _health_check_from_probe(provider_session_probe)
        provider_session_check.duration_ms = _duration_ms(provider_session_started_at)
        checks.append(provider_session_check)

    channels_enabled_started_at = perf_counter()
    enabled_channels = list(config.enabled_channels)
    channels_enabled_check = _health_check_from_probe(
        probe_channels_enabled(enabled_channels)
    )
    channels_enabled_check.duration_ms = _duration_ms(channels_enabled_started_at)
    checks.append(channels_enabled_check)

    default_channel_started_at = perf_counter()
    default_channel = default_agent_profile.default_channel or "console"
    default_channel_check = _health_check_from_probe(
        probe_default_channel_in_enabled(
            default_channel=default_channel,
            enabled_channels=enabled_channels,
        )
    )
    default_channel_check.duration_ms = _duration_ms(default_channel_started_at)
    checks.append(default_channel_check)

    plugins_enabled_started_at = perf_counter()
    plugins_enabled_check = _health_check_from_probe(
        probe_plugins_enabled(
            resolve_plugin_runtime_policy(
                compatibility_enabled_plugins=list(config.enabled_plugins),
                system_policy=getattr(config.runtime, "plugins", None),
            ).effective_enabled
        )
    )
    plugins_enabled_check.duration_ms = _duration_ms(plugins_enabled_started_at)
    checks.append(plugins_enabled_check)

    runtime_bootstrap_started_at = perf_counter()

    def _runtime_bootstrap_details() -> Dict[str, Any]:
        nonlocal runtime_storage_probe
        channels: Any
        plugins: Any
        provider: Any
        if runtime is not None:
            channels = runtime.channels
            plugins = runtime.plugins
            provider = runtime.provider
        else:
            logger = logging.getLogger("openminion.health")
            extension_manager = LifecycleService.from_config(
                config,
                config_path=str(resolved_config_path),
                logger=logger,
            )
            security_policy = SecurityPolicyEngine(
                tool_budget_policy=ToolBudgetPolicy(
                    max_calls_per_run=config.security.tool_policy.max_calls_per_run,
                    max_calls_per_tool=config.security.tool_policy.max_calls_per_tool,
                    max_budget_cost_per_run=config.security.tool_policy.max_budget_cost_per_run,
                ),
                default_tool_required_scopes=frozenset(
                    config.security.tool_policy.default_required_scopes
                ),
            )
            extension_runtime = extension_manager.build(
                security_policy=security_policy,
                load_tool_plugins=False,
            )
            channels = extension_runtime.channels
            plugins = extension_runtime.plugins
            provider = build_provider(
                config,
                logger.getChild("provider"),
            )
            if runtime_storage_probe is None:
                runtime_storage_probe = build_runtime_storage(storage_path)
            runtime_storage_probe.connection.execute("SELECT 1")
        return {
            "provider": str(getattr(provider, "name", "")),
            "loaded_channels": channels.names(),
            "loaded_plugins": plugins.names(),
        }

    runtime_bootstrap_check = _health_check_from_probe(
        probe_runtime_bootstrap(
            bootstrap_fn=_runtime_bootstrap_details,
            success_message="Runtime components initialized successfully",
        )
    )
    runtime_bootstrap_check.duration_ms = _duration_ms(runtime_bootstrap_started_at)
    checks.append(runtime_bootstrap_check)

    integration_mode_started_at = perf_counter()
    integration_check = _build_integration_mode_check(config)
    integration_check.duration_ms = _duration_ms(integration_mode_started_at)
    checks.append(integration_check)

    brain_observability_started_at = perf_counter()
    brain_observability_check = _build_brain_llm_mode_observability_check(
        storage_path=storage_path,
        probe_session_id=probe_session_id,
        event_limit=_resolve_brain_observability_event_limit(env_config),
        env_config=env_config,
    )
    brain_observability_check.duration_ms = _duration_ms(brain_observability_started_at)
    checks.append(brain_observability_check)

    latency_budget_started_at = perf_counter()
    latency_budget_check = _build_latency_budget_check(
        checks=checks,
        threshold_ms=_resolve_health_latency_budget_threshold_ms(env_config),
    )
    latency_budget_check.duration_ms = _duration_ms(latency_budget_started_at)
    checks.append(latency_budget_check)

    payload = _build_health_payload(
        checks=checks,
        snapshot_started_at=snapshot_started_at,
        resolved_home_root=resolved_home_root,
        provider_name=provider_name,
        default_agent_id=default_agent_id,
        default_agent_profile=default_agent_profile,
        storage_path=storage_path,
        env_config=env_config,
        metrics_consistency=metrics_consistency,
    )
    if runtime is None and runtime_storage_probe is not None:
        runtime_storage_probe.close()
    return payload


__all__ = [
    "_evaluate_supervision_decision",
    "_load_lifecycle_facts",
    "_supervision_policies_for_lifecycle_facts",
    "collect_health_snapshot",
]
