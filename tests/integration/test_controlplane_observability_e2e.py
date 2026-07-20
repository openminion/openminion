from __future__ import annotations

from openminion.modules.controlplane.runtime.audit import AuditEvent, AuditLogger
from openminion.modules.controlplane.runtime.health_probe import ControlPlaneHealthProbeSidecar
from openminion.modules.controlplane.runtime.janitor import ControlPlaneJanitor
from openminion.modules.controlplane.runtime.metrics import (
    MetricsAuditSink,
    MetricsRegistry,
    compose_audit_sinks,
)


class _Store:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self.deleted = 0

    def put_audit(self, event: AuditEvent) -> None:
        self.events.append(event)

    def list_audit(self, *, limit: int = 1) -> list[AuditEvent]:
        return self.events[:limit]

    def _execute_count(self, sql: str, _params: tuple[object, ...]) -> int:
        if "cp_audit_events" in sql:
            self.deleted += 1
            return 1
        return 0


def test_observability_e2e_keeps_audit_metrics_health_and_janitor_consistent() -> None:
    store = _Store()
    metrics = MetricsRegistry()
    metrics_sink = MetricsAuditSink(metrics)
    audit = AuditLogger(sink=compose_audit_sinks(store.put_audit, metrics_sink.observe))

    audit.emit("inbound.received", details={"channel": "telegram"})
    audit.emit("cp.access.deny", severity="warn", details={"channel": "slack"})
    audit.emit(
        "cp.rate_limit.exceeded",
        severity="warn",
        details={"channel": "telegram", "dimension": "chat"},
    )
    audit.emit("cp.delivery.failed", severity="error", details={"channel": "slack"})
    janitor_result = ControlPlaneJanitor(store=store, audit_logger=audit).run_once()

    sidecar = ControlPlaneHealthProbeSidecar(
        get_status=lambda: {"channel_runtime": {"state": "running", "channels": {}}},
        get_audit_health=audit.health_status,
        probe_store=lambda: bool(store.list_audit(limit=1)),
        get_metrics=metrics.render_prometheus,
    )
    ready, readiness = sidecar.readiness()

    assert len(store.events) == 5
    assert metrics.counter_value(
        "controlplane_inbound_total", labels={"channel": "telegram", "outcome": "accepted"}
    ) == 1
    assert metrics.counter_value(
        "controlplane_inbound_total", labels={"channel": "slack", "outcome": "deny"}
    ) == 1
    assert metrics.counter_value(
        "controlplane_delivery_total", labels={"channel": "slack", "status": "failed"}
    ) == 1
    assert janitor_result.deleted["cp_audit_events"] == 1
    assert ready is True
    assert readiness["audit"]["healthy"] is True
    assert b"controlplane_rate_limit_exceeded_total" in metrics.render_prometheus()
