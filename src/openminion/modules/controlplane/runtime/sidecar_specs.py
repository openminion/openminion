"""Process-level controlplane sidecar specs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from openminion.modules.controlplane.config import ControlPlaneConfig
from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.runtime.health_probe import (
    ControlPlaneHealthProbeConfig,
    ControlPlaneHealthProbeSidecar,
)
from openminion.modules.controlplane.runtime.janitor import (
    ControlPlaneJanitor,
    ControlPlaneJanitorSidecar,
    ControlPlaneRetentionPolicy,
)
from openminion.modules.controlplane.runtime.metrics import MetricsRegistry


@dataclass(frozen=True)
class ControlPlaneSidecarSpec:
    name: str
    description: str
    autostart_env_key: str
    prompt: str
    adapter: Any


def build_controlplane_sidecar_specs(
    *,
    config: ControlPlaneConfig,
    store: Any,
    audit_logger: AuditLogger,
    metrics: MetricsRegistry,
) -> list[ControlPlaneSidecarSpec]:
    specs: list[ControlPlaneSidecarSpec] = []
    if config.janitor_enabled:
        specs.append(_janitor_spec(config, store=store, audit_logger=audit_logger))
    if config.health_probe_enabled:
        specs.append(_health_probe_spec(config, store=store, audit_logger=audit_logger, metrics=metrics))
    return specs


def _janitor_spec(
    config: ControlPlaneConfig, *, store: Any, audit_logger: AuditLogger
) -> ControlPlaneSidecarSpec:
    return ControlPlaneSidecarSpec(
        name="controlplane-janitor",
        description="Controlplane retention cleanup worker",
        autostart_env_key="OPENMINION_CONTROLPLANE_JANITOR_AUTOSTART",
        prompt="Start the controlplane janitor sidecar? [y/N] ",
        adapter=ControlPlaneJanitorSidecar(
            janitor=ControlPlaneJanitor(
                store=store,
                policy=ControlPlaneRetentionPolicy(
                    audit_retention_days=config.audit_retention_days,
                    outbox_terminal_retention_days=config.outbox_terminal_retention_days,
                    pair_token_retention_days=config.pair_token_retention_days,
                    pair_attempt_retention_days=config.pair_attempt_retention_days,
                    rate_limit_retention_days=config.rate_limit_retention_days,
                    wizard_terminal_retention_days=config.wizard_terminal_retention_days,
                ),
                audit_logger=audit_logger,
                dry_run=config.janitor_dry_run,
            ),
            interval_seconds=config.janitor_interval_seconds,
        ),
    )


def _health_probe_spec(
    config: ControlPlaneConfig,
    *,
    store: Any,
    audit_logger: AuditLogger,
    metrics: MetricsRegistry,
) -> ControlPlaneSidecarSpec:
    return ControlPlaneSidecarSpec(
        name="controlplane-health-probe",
        description="Controlplane health, readiness, status, and metrics probe",
        autostart_env_key="OPENMINION_CONTROLPLANE_HEALTH_PROBE_AUTOSTART",
        prompt="Start the controlplane health probe sidecar? [y/N] ",
        adapter=ControlPlaneHealthProbeSidecar(
            config=ControlPlaneHealthProbeConfig(
                host=config.health_probe_host,
                port=config.health_probe_port,
                allow_remote=config.health_probe_allow_remote,
                bearer_token=config.health_probe_bearer_token,
            ),
            get_status=_default_runtime_status,
            get_audit_health=audit_logger.health_status,
            probe_store=lambda: _probe_store(store),
            get_metrics=metrics.render_prometheus,
        ),
    )


def _default_runtime_status() -> dict[str, Any]:
    return {"channel_runtime": {"state": "running", "channels": {}}}


def _probe_store(store: Any) -> bool:
    lister: Callable[..., Any] | None = getattr(store, "list_audit", None)
    if lister is None:
        return True
    try:
        lister(limit=1)
        return True
    except (AttributeError, RuntimeError, ValueError, OSError):
        return False


__all__ = ["ControlPlaneSidecarSpec", "build_controlplane_sidecar_specs"]
