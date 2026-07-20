"""Typed registry for canonical controlplane audit events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class AuditEventSpec:
    event_type: str
    origin: str
    payload_schema: Mapping[str, str]
    severity: str = "info"
    metric_name: str | None = None
    alert_threshold: str = "operator-defined"

    def to_row(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "origin": self.origin,
            "payload_schema": dict(self.payload_schema),
            "severity": self.severity,
            "metric_name": self.metric_name,
            "alert_threshold": self.alert_threshold,
        }


_EVENT_SPECS = (
    AuditEventSpec(
        "inbound.received",
        "runtime/dispatcher.py::ControlPlaneDispatcher.handle_inbound",
        {"channel": "str"},
        metric_name="controlplane_inbound_total",
    ),
    AuditEventSpec(
        "inbound.resolved",
        "runtime/dispatcher.py::ControlPlaneDispatcher.handle_inbound",
        {"session_id": "str", "agent_id": "str"},
    ),
    AuditEventSpec(
        "outbound.sent",
        "runtime/dispatcher.py::ControlPlaneDispatcher.handle_inbound",
        {"kind": "str"},
    ),
    AuditEventSpec(
        "channel.message.received",
        "runtime/worker/inbox.py::InboxWorker.run_once",
        {"channel": "str"},
        metric_name="controlplane_inbound_total",
    ),
    AuditEventSpec(
        "channel.message.sent",
        "runtime/worker/outbox.py::OutboxWorker.run_once",
        {"channel": "str", "outbox_id": "str"},
    ),
    AuditEventSpec(
        "cp.access.allow",
        "channels/*/{polling,webhook}.py",
        {"channel": "str", "reason": "str"},
        metric_name="controlplane_inbound_total",
    ),
    AuditEventSpec(
        "cp.access.deny",
        "channels/*/{polling,webhook}.py",
        {"channel": "str", "reason": "str"},
        severity="warn",
        metric_name="controlplane_inbound_total",
        alert_threshold="alert on sustained deny spikes by channel",
    ),
    AuditEventSpec(
        "cp.chat.dispatched",
        "runtime/dispatcher.py::ControlPlaneDispatcher._dispatch_chat",
        {"session_id": "str", "agent_id": "str"},
    ),
    AuditEventSpec(
        "cp.command.detected",
        "runtime/dispatcher.py::ControlPlaneDispatcher._dispatch_command",
        {"command": "str"},
    ),
    AuditEventSpec(
        "cp.command.executed",
        "runtime/dispatcher.py::ControlPlaneDispatcher._dispatch_command",
        {"command": "str"},
    ),
    AuditEventSpec(
        "cp.command.failed",
        "runtime/dispatcher.py::ControlPlaneDispatcher._dispatch_command",
        {"command": "str"},
        severity="warn",
    ),
    AuditEventSpec(
        "cp.delivery.sent",
        "runtime/worker/outbox.py and channel delivery services",
        {"channel": "str", "outbox_id": "str"},
        metric_name="controlplane_delivery_total",
    ),
    AuditEventSpec(
        "cp.delivery.failed",
        "runtime/worker/outbox.py and channel delivery services",
        {"channel": "str", "outbox_id": "str", "reason": "str"},
        severity="error",
        metric_name="controlplane_delivery_total",
        alert_threshold="alert on >10 failures in 5 minutes by channel",
    ),
    AuditEventSpec(
        "cp.delivery.skipped",
        "channels/*/{polling,webhook}.py",
        {"channel": "str", "reason": "str"},
        severity="warn",
        metric_name="controlplane_delivery_total",
    ),
    AuditEventSpec(
        "cp.outbox.enqueued",
        "channels/*/runtime/helpers.py",
        {"channel": "str", "outbox_id": "str"},
        metric_name="controlplane_outbox_enqueued_total",
    ),
    AuditEventSpec(
        "cp.outbox.deadletter",
        "runtime/worker/outbox.py::OutboxWorker.run_once",
        {"outbox_id": "str", "reason": "str"},
        severity="critical",
        metric_name="controlplane_delivery_total",
        alert_threshold="page on any sustained deadletter growth",
    ),
    AuditEventSpec(
        "cp.rate_limit.exceeded",
        "channels/*/{polling,webhook}.py",
        {"channel": "str", "dimension": "str"},
        severity="warn",
        metric_name="controlplane_rate_limit_exceeded_total",
        alert_threshold="alert on sustained throttling for one dimension",
    ),
    AuditEventSpec(
        "cp.route.runtime_dispatch",
        "channels/*/{polling,webhook}.py",
        {"channel": "str", "reason": "str"},
    ),
    AuditEventSpec(
        "cp.route.runtime_failed",
        "channels/telegram/runtime/helpers.py",
        {"channel": "str", "reason": "str"},
        severity="error",
    ),
    AuditEventSpec(
        "cp.route.local_command",
        "channels/*/{polling,webhook}.py",
        {"channel": "str", "command": "str"},
    ),
    AuditEventSpec(
        "cp.route.pairing_handled",
        "channels/*/{polling,webhook}.py",
        {"channel": "str", "outcome": "str"},
    ),
    AuditEventSpec(
        "cp.route.outbox.selected",
        "runtime/worker/outbox.py::OutboxWorker.run_once",
        {"channel": "str", "outbox_id": "str"},
    ),
    AuditEventSpec(
        "cp.wizard.step.failure",
        "runtime/dispatcher.py::ControlPlaneDispatcher._dispatch_wizard",
        {"wizard_id": "str", "reason": "str"},
        severity="warn",
        metric_name="controlplane_wizard_step_failures_total",
    ),
    AuditEventSpec(
        "cp.wizard.step.failed",
        "runtime/dispatcher.py::ControlPlaneDispatcher._dispatch_wizard",
        {"wizard_id": "str", "reason": "str"},
        severity="warn",
        metric_name="controlplane_wizard_step_failures_total",
    ),
    AuditEventSpec(
        "cp.pairing.token.issued",
        "pairing/service.py::ControlPlanePairingService.issue_token",
        {"channel": "str"},
        metric_name="controlplane_pairing_total",
    ),
    AuditEventSpec(
        "cp.pairing.token.consumed",
        "pairing/service.py::ControlPlanePairingService.consume_token",
        {"channel": "str"},
        metric_name="controlplane_pairing_total",
    ),
    AuditEventSpec(
        "cp.pairing.token.rejected",
        "pairing/service.py::ControlPlanePairingService.consume_token",
        {"channel": "str", "reason": "str"},
        severity="warn",
        metric_name="controlplane_pairing_total",
    ),
    AuditEventSpec(
        "cp.pairing.legacy_redeem",
        "pairing/service.py::ControlPlanePairingService.consume_token",
        {"channel": "str"},
    ),
    AuditEventSpec(
        "cp.pairing.binding.scopes_updated",
        "pairing/admin.py::ControlPlanePairingAdmin.update_scopes",
        {"channel": "str"},
    ),
    AuditEventSpec(
        "cp.pairing.binding.revoked",
        "pairing/admin.py::ControlPlanePairingAdmin.revoke",
        {"channel": "str"},
        severity="warn",
    ),
    AuditEventSpec(
        "cp.pairing.migration.completed",
        "pairing/migration.py::PairingMigrationJob.run_once",
        {"migrated": "int"},
    ),
    AuditEventSpec(
        "cp.clarify.requested",
        "runtime/dispatcher.py::ControlPlaneDispatcher._dispatch_chat",
        {"session_id": "str"},
    ),
    AuditEventSpec(
        "cp.clarify.answered",
        "runtime/dispatcher.py::ControlPlaneDispatcher.handle_inbound",
        {"session_id": "str"},
    ),
    AuditEventSpec(
        "cp.clarify.answer_rejected",
        "runtime/dispatcher.py::ControlPlaneDispatcher.handle_inbound",
        {"session_id": "str", "reason": "str"},
        severity="warn",
    ),
    AuditEventSpec(
        "cp.resume.dispatched",
        "runtime/dispatcher.py::ControlPlaneDispatcher.handle_inbound",
        {"session_id": "str"},
    ),
    AuditEventSpec(
        "cp.error",
        "runtime/worker/inbox.py::InboxWorker.run_once",
        {"outcome": "str", "reason": "str"},
        severity="error",
    ),
    AuditEventSpec(
        "cp.janitor.cycle.completed",
        "runtime/janitor.py::ControlPlaneJanitor.run_once",
        {"deleted": "dict", "dry_run": "bool"},
        metric_name="controlplane_janitor_deleted_total",
    ),
    AuditEventSpec(
        "cp.telegram.runner.online_notice_sent",
        "channels/telegram/runtime/helpers.py",
        {"channel": "str"},
    ),
    AuditEventSpec(
        "cp.telegram.turn.progress_notice_sent",
        "channels/telegram/runtime/helpers.py",
        {"channel": "str"},
    ),
    AuditEventSpec(
        "cp.slack.event.duplicate",
        "channels/slack/runtime/helpers.py",
        {"channel": "str"},
    ),
    AuditEventSpec(
        "cp.slack.dispatch.failed",
        "channels/slack/runtime/helpers.py",
        {"channel": "str", "reason": "str"},
        severity="error",
    ),
    AuditEventSpec(
        "session.bind.admin_override",
        "runtime/router.py::Router.resolve",
        {"session_id": "str"},
        severity="warn",
    ),
    AuditEventSpec(
        "session.bind.denied",
        "runtime/router.py::Router.resolve",
        {"session_id": "str"},
        severity="warn",
    ),
)

CONTROLPLANE_AUDIT_EVENT_REGISTRY: dict[str, AuditEventSpec] = {
    spec.event_type: spec for spec in _EVENT_SPECS
}


def validate_audit_event(event: Any, *, enabled: bool = False) -> None:
    if not enabled:
        return
    event_type = _event_type(event)
    if event_type not in CONTROLPLANE_AUDIT_EVENT_REGISTRY:
        raise ValueError(f"Unregistered controlplane audit event: {event_type}")


def taxonomy_rows() -> list[dict[str, Any]]:
    return [
        CONTROLPLANE_AUDIT_EVENT_REGISTRY[name].to_row()
        for name in sorted(CONTROLPLANE_AUDIT_EVENT_REGISTRY)
    ]


def _event_type(event: Any) -> str:
    if hasattr(event, "event_type"):
        return str(getattr(event, "event_type"))
    if isinstance(event, Mapping):
        return str(event.get("event_type") or event.get("event") or "")
    return ""


__all__ = [
    "AuditEventSpec",
    "CONTROLPLANE_AUDIT_EVENT_REGISTRY",
    "taxonomy_rows",
    "validate_audit_event",
]
