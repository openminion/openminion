from __future__ import annotations

from typing import Any, Mapping

from .constants import RUNTIME_MODE_HOT
from .events.catalog import LIFECYCLE_EVENT_TYPES
from .schemas import TelemetryEvent

LIFECYCLE_CONTRACT = "observability-lifecycle-event-v1"
_CANONICAL_EVENT_TYPES = LIFECYCLE_EVENT_TYPES
_PRIMARY_COMPONENT_ID = "primary"


def build_component_identity(
    *,
    component_kind: str,
    component_id: str,
    scope: str,
    owner_module: str,
    parent_component_id: str | None = None,
    host_component_id: str | None = None,
    capabilities: list[str] | None = None,
    labels: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    kind = str(component_kind or "").strip()
    identifier = str(component_id or "").strip()
    component_scope = str(scope or "").strip()
    module_name = str(owner_module or "").strip()
    if not kind or not identifier or not component_scope or not module_name:
        raise ValueError(
            "component lifecycle identity requires kind, id, scope, and owner_module"
        )
    payload: dict[str, Any] = {
        "component_kind": kind,
        "component_id": identifier,
        "scope": component_scope,
        "owner_module": module_name,
    }
    parent = str(parent_component_id or "").strip()
    if parent:
        payload["parent_component_id"] = parent
    host = str(host_component_id or "").strip()
    if host:
        payload["host_component_id"] = host
    if capabilities:
        payload["capabilities"] = [
            str(item).strip() for item in capabilities if str(item).strip()
        ]
    if labels:
        clean_labels: dict[str, str] = {}
        for raw_key, raw_value in labels.items():
            key = str(raw_key or "").strip()
            value = str(raw_value or "").strip()
            if key and value:
                clean_labels[key] = value
        if clean_labels:
            payload["labels"] = clean_labels
    return payload


def component_identity_key(component: Mapping[str, Any]) -> str:
    component_kind = str(component.get("component_kind") or "").strip()
    component_id = str(component.get("component_id") or "").strip()
    scope = str(component.get("scope") or "").strip()
    if not component_kind or not component_id or not scope:
        raise ValueError(
            "component identity key requires component_kind, component_id, and scope"
        )
    return f"{component_kind}:{component_id}:{scope}"


def build_runtime_manager_component_identity() -> dict[str, Any]:
    return build_component_identity(
        component_kind="runtime_manager",
        component_id=_PRIMARY_COMPONENT_ID,
        scope="system",
        owner_module="openminion-runtime",
        capabilities=["turn_dispatch", "heartbeat"],
        labels={"runtime_mode": RUNTIME_MODE_HOT},
    )


def build_agent_runtime_component_identity(agent_id: str) -> dict[str, Any]:
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id:
        raise ValueError("agent runtime lifecycle identity requires agent_id")
    return build_component_identity(
        component_kind="agent_runtime",
        component_id=normalized_agent_id,
        scope="agent",
        owner_module="openminion-runtime",
        parent_component_id=_PRIMARY_COMPONENT_ID,
        host_component_id=_PRIMARY_COMPONENT_ID,
        capabilities=["turn_execution"],
        labels={"runtime_mode": RUNTIME_MODE_HOT},
    )


def build_cron_scheduler_component_identity(
    *,
    daemon_component_id: str,
) -> dict[str, Any]:
    normalized_daemon_component_id = (
        str(daemon_component_id or "").strip() or _PRIMARY_COMPONENT_ID
    )
    return build_component_identity(
        component_kind="cron_scheduler",
        component_id=_PRIMARY_COMPONENT_ID,
        scope="system",
        owner_module="openminion-runtime",
        host_component_id=normalized_daemon_component_id,
        parent_component_id=normalized_daemon_component_id,
        capabilities=["scheduler"],
        labels={"topology": "daemon-hosted"},
    )


def build_lifecycle_telemetry_event(
    *,
    event_type: str,
    component: Mapping[str, Any],
    module_id: str,
    session_id: str,
    turn_id: str,
    status: str | None = None,
    reason: str | None = None,
    metrics: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
    source_event_type: str | None = None,
    source_classification: str = "native_canonical",
) -> TelemetryEvent:
    normalized_event_type = str(event_type or "").strip()
    if normalized_event_type not in _CANONICAL_EVENT_TYPES:
        raise ValueError(f"unsupported lifecycle event type: {normalized_event_type!r}")

    normalized_session_id = str(session_id or "").strip()
    normalized_turn_id = str(turn_id or "").strip()
    normalized_module_id = str(module_id or "").strip()
    if not normalized_session_id or not normalized_turn_id or not normalized_module_id:
        raise ValueError(
            "lifecycle telemetry requires session_id, turn_id, and module_id"
        )

    payload: dict[str, Any] = {
        "module_id": normalized_module_id,
        "contract": LIFECYCLE_CONTRACT,
        "component": dict(component),
    }
    normalized_status = str(status or "").strip()
    if normalized_status:
        payload["status"] = normalized_status
    normalized_reason = str(reason or "").strip()
    if normalized_reason:
        payload["reason"] = normalized_reason
    if metrics:
        payload["metrics"] = dict(metrics)
    if evidence:
        payload["evidence"] = dict(evidence)
    normalized_source_event_type = str(source_event_type or "").strip()
    if normalized_source_event_type:
        payload["source_event_type"] = normalized_source_event_type
    normalized_source_classification = (
        str(source_classification or "").strip() or "native_canonical"
    )
    payload["source_classification"] = normalized_source_classification

    return TelemetryEvent(
        session_id=normalized_session_id,
        turn_id=normalized_turn_id,
        event_type=normalized_event_type,
        data=payload,
    )


def lifecycle_event_from_payload(
    event_type: str,
    payload: Mapping[str, Any],
) -> TelemetryEvent | None:
    normalized_event_type = str(event_type or "").strip()
    if normalized_event_type not in _CANONICAL_EVENT_TYPES:
        return None
    source_payload = dict(payload or {})
    component = source_payload.get("component")
    if not isinstance(component, Mapping):
        return None
    metrics = source_payload.get("metrics")
    evidence = source_payload.get("evidence")
    return build_lifecycle_telemetry_event(
        event_type=normalized_event_type,
        component=component,
        module_id=str(source_payload.get("module_id") or "").strip()
        or "openminion-runtime",
        session_id=str(source_payload.get("session_id") or "").strip(),
        turn_id=str(source_payload.get("turn_id") or "").strip(),
        status=str(source_payload.get("status") or "").strip() or None,
        reason=str(source_payload.get("reason") or "").strip() or None,
        metrics=metrics if isinstance(metrics, Mapping) else None,
        evidence=evidence if isinstance(evidence, Mapping) else None,
        source_event_type=str(source_payload.get("source_event_type") or "").strip()
        or None,
        source_classification=str(
            source_payload.get("source_classification") or ""
        ).strip()
        or "native_canonical",
    )


def map_runtime_event_to_lifecycle_event(
    event_type: str,
    payload: Mapping[str, Any],
) -> TelemetryEvent | None:
    legacy_event_type = str(event_type or "").strip()
    if not legacy_event_type:
        return None
    source_payload = dict(payload or {})
    if bool(source_payload.get("native_lifecycle_emitted")):
        return None

    if legacy_event_type == "runtime.manager.shutdown":
        component = build_runtime_manager_component_identity()
        return build_lifecycle_telemetry_event(
            event_type="component.stopped",
            component=component,
            module_id="openminion-runtime",
            session_id="lifecycle:runtime_manager:primary",
            turn_id="runtime.manager.shutdown",
            status="ok",
            reason="manual_stop",
            evidence={"legacy_payload": source_payload},
            source_event_type=legacy_event_type,
            source_classification="legacy_mapped",
        )

    if legacy_event_type == "runtime.manager.kill":
        component = build_runtime_manager_component_identity()
        return build_lifecycle_telemetry_event(
            event_type="component.crashed",
            component=component,
            module_id="openminion-runtime",
            session_id="lifecycle:runtime_manager:primary",
            turn_id="runtime.manager.kill",
            status="error",
            reason="kill_switch",
            metrics={"active_traces": int(source_payload.get("active_traces", 0) or 0)},
            evidence={"legacy_payload": source_payload},
            source_event_type=legacy_event_type,
            source_classification="legacy_mapped",
        )

    if legacy_event_type == "runtime.agent.created":
        agent_id = str(source_payload.get("agent_id") or "").strip()
        if not agent_id:
            return None
        component = build_agent_runtime_component_identity(agent_id)
        return build_lifecycle_telemetry_event(
            event_type="component.started",
            component=component,
            module_id="openminion-runtime",
            session_id=f"lifecycle:agent_runtime:{agent_id}",
            turn_id=agent_id,
            status="ok",
            reason="worker_created",
            evidence={"legacy_payload": source_payload},
            source_event_type=legacy_event_type,
            source_classification="legacy_mapped",
        )

    if legacy_event_type == "runtime.agent.evicted":
        agent_id = str(source_payload.get("agent_id") or "").strip()
        if not agent_id:
            return None
        component = build_agent_runtime_component_identity(agent_id)
        return build_lifecycle_telemetry_event(
            event_type="component.stopped",
            component=component,
            module_id="openminion-runtime",
            session_id=f"lifecycle:agent_runtime:{agent_id}",
            turn_id=agent_id,
            status="ok",
            reason=str(source_payload.get("reason") or "evicted").strip() or "evicted",
            evidence={"legacy_payload": source_payload},
            source_event_type=legacy_event_type,
            source_classification="legacy_mapped",
        )

    return None


def map_cron_event_to_lifecycle_event(
    event_type: str,
    payload: Mapping[str, Any],
) -> TelemetryEvent | None:
    legacy_event_type = str(event_type or "").strip()
    if not legacy_event_type:
        return None
    source_payload = dict(payload or {})
    daemon_id = str(source_payload.get("daemon_id") or "").strip()
    daemon_component_id = (
        str(source_payload.get("daemon_component_id") or "").strip()
        or daemon_id
        or _PRIMARY_COMPONENT_ID
    )
    component = build_cron_scheduler_component_identity(
        daemon_component_id=daemon_component_id
    )

    if legacy_event_type == "cron.scheduler.started":
        return build_lifecycle_telemetry_event(
            event_type="component.started",
            component=component,
            module_id="openminion-runtime",
            session_id="lifecycle:cron_scheduler:primary",
            turn_id=daemon_id,
            status="ok",
            reason="scheduler_started",
            evidence={"legacy_payload": source_payload},
            source_event_type=legacy_event_type,
            source_classification="legacy_mapped",
        )

    if legacy_event_type == "cron.scheduler.stopped":
        return build_lifecycle_telemetry_event(
            event_type="component.stopped",
            component=component,
            module_id="openminion-runtime",
            session_id="lifecycle:cron_scheduler:primary",
            turn_id=daemon_id,
            status="ok",
            reason="scheduler_stopped",
            evidence={"legacy_payload": source_payload},
            source_event_type=legacy_event_type,
            source_classification="legacy_mapped",
        )

    if legacy_event_type == "cron.scheduler.error":
        return build_lifecycle_telemetry_event(
            event_type="component.degraded",
            component=component,
            module_id="openminion-runtime",
            session_id="lifecycle:cron_scheduler:primary",
            turn_id=daemon_id,
            status="degraded",
            reason="scheduler_error",
            evidence={"legacy_payload": source_payload},
            source_event_type=legacy_event_type,
            source_classification="legacy_mapped",
        )

    if legacy_event_type == "cron.lease.lost":
        run_id = str(source_payload.get("run_id") or "").strip() or daemon_component_id
        return build_lifecycle_telemetry_event(
            event_type="component.degraded",
            component=component,
            module_id="openminion-runtime",
            session_id="lifecycle:cron_scheduler:primary",
            turn_id=run_id,
            status="timeout",
            reason="lease_lost",
            evidence={"legacy_payload": source_payload},
            source_event_type=legacy_event_type,
            source_classification="legacy_mapped",
        )

    if legacy_event_type == "cron.scheduler.heartbeat":
        return _cron_heartbeat_lifecycle_event(
            component=component,
            daemon_component_id=daemon_component_id,
            legacy_event_type=legacy_event_type,
            source_payload=source_payload,
        )

    return None


def _cron_heartbeat_lifecycle_event(
    *,
    component: Mapping[str, Any],
    daemon_component_id: str,
    legacy_event_type: str,
    source_payload: Mapping[str, Any],
) -> TelemetryEvent:
    metrics: dict[str, Any] = {}
    for key in (
        "active_runs",
        "tick_duration_ms",
        "tick_seconds",
        "lag_seconds",
        "daemon_pid",
    ):
        value = source_payload.get(key)
        if value is not None:
            metrics[key] = value
    return build_lifecycle_telemetry_event(
        event_type="component.heartbeat",
        component=component,
        module_id="openminion-runtime",
        session_id="lifecycle:cron_scheduler:primary",
        turn_id=daemon_component_id,
        status="ok",
        reason="heartbeat",
        metrics=metrics or None,
        evidence={"legacy_payload": source_payload},
        source_event_type=legacy_event_type,
        source_classification="legacy_mapped",
    )


__all__ = [
    "LIFECYCLE_CONTRACT",
    "build_agent_runtime_component_identity",
    "build_component_identity",
    "build_cron_scheduler_component_identity",
    "build_lifecycle_telemetry_event",
    "build_runtime_manager_component_identity",
    "component_identity_key",
    "lifecycle_event_from_payload",
    "map_cron_event_to_lifecycle_event",
    "map_runtime_event_to_lifecycle_event",
]
