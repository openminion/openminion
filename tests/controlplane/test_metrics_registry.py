from __future__ import annotations

import pytest

from openminion.modules.controlplane.runtime.audit import AuditEvent, AuditLogger
from openminion.modules.controlplane.runtime.metrics import (
    MetricsAuditSink,
    MetricsRegistry,
    compose_audit_sinks,
)


def test_metrics_sink_maps_audit_events_to_low_cardinality_counters() -> None:
    registry = MetricsRegistry()
    sink = MetricsAuditSink(registry)
    sink.observe(AuditEvent("inbound.received", details={"channel": "telegram"}))
    sink.observe(AuditEvent("cp.access.deny", details={"channel": "slack"}))
    sink.observe(
        AuditEvent(
            "cp.rate_limit.exceeded",
            details={"channel": "telegram", "dimension": "chat"},
        )
    )
    sink.observe(AuditEvent("cp.delivery.failed", details={"channel": "telegram"}))
    sink.observe(
        AuditEvent(
            "cp.janitor.cycle.completed",
            details={"deleted": {"cp_outbox": 2}},
        )
    )

    assert registry.counter_value(
        "controlplane_inbound_total",
        labels={"channel": "telegram", "outcome": "accepted"},
    ) == 1
    assert registry.counter_value(
        "controlplane_inbound_total",
        labels={"channel": "slack", "outcome": "deny"},
    ) == 1
    assert registry.counter_value(
        "controlplane_rate_limit_exceeded_total",
        labels={"channel": "telegram", "dimension": "chat"},
    ) == 1
    assert registry.counter_value(
        "controlplane_delivery_total",
        labels={"channel": "telegram", "status": "failed"},
    ) == 1
    assert registry.counter_value(
        "controlplane_janitor_deleted_total",
        labels={"table": "cp_outbox"},
    ) == 2


def test_metric_labels_reject_subject_session_prompt_token_and_raw_errors() -> None:
    registry = MetricsRegistry()
    with pytest.raises(ValueError, match="Unsupported controlplane metric label"):
        registry.inc("controlplane_inbound_total", labels={"session_id": "s1"})
    with pytest.raises(ValueError, match="Unsafe controlplane metric label value"):
        registry.inc("controlplane_inbound_total", labels={"channel": "x" * 65})


def test_composed_audit_sink_preserves_storage_when_metrics_fails() -> None:
    persisted: list[str] = []

    def storage(event: AuditEvent) -> None:
        persisted.append(event.event_type)

    def broken_metrics(_event: AuditEvent) -> None:
        raise RuntimeError("metrics down")

    logger = AuditLogger(sink=compose_audit_sinks(storage, broken_metrics))
    logger.emit("cp.delivery.sent")

    assert persisted == ["cp.delivery.sent"]
    assert logger.failure_count() == 0


def test_prometheus_rendering_uses_text_exposition_shape() -> None:
    registry = MetricsRegistry()
    registry.inc("controlplane_outbox_enqueued_total", labels={"channel": "telegram"})
    assert b'controlplane_outbox_enqueued_total{channel="telegram"} 1' in registry.render_prometheus()
