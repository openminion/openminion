from __future__ import annotations

import logging
from typing import Any

from openminion.modules.telemetry.events.canonical import build_canonical_event
from openminion.modules.telemetry.events.module import emit_module_telemetry
from openminion.modules.telemetry.schemas import (
    TelemetryEvent,
    normalize_telemetry_event,
)
from openminion.modules.telemetry.storage.base import TelemetryEventConflictError

_LOG = logging.getLogger(__name__)


def invocation_artifact_paths(events: list[Any]) -> set[str]:
    artifact_paths: set[str] = set()
    for event in events:
        paths = event.data.get("trace_artifact_paths", [])
        if isinstance(paths, list):
            artifact_paths.update(path for path in paths if isinstance(path, str))
    return artifact_paths


def repair_event_sync(service: Any, event: TelemetryEvent) -> str:
    normalized = normalize_telemetry_event(event)
    try:
        created = service._store.insert_event_if_absent(
            service._content_policy_event(
                normalized,
                allow_sensitive_content=service._include_local_content,
            )
        )
    except TelemetryEventConflictError:
        return "conflict"
    except (RuntimeError, ValueError, OSError):
        return "storage_failed"
    if not created:
        return "already_identical"
    emit_module_telemetry(
        service._external_exporter,
        "export",
        service._content_policy_event(
            normalized,
            allowed_sensitive_fields=service._external_sensitive_fields,
        ),
        logger=_LOG,
    )
    return "created"


def repair_canonical_event_sync(
    telemetryctl: Any,
    session_id: str,
    turn_id: str,
    event_type: str,
    payload: dict[str, Any] | None,
    *,
    event_id: str,
    timestamp: float,
    invocation_id: str,
    agent_id: str | None,
) -> str:
    event = build_canonical_event(
        event_factory=telemetryctl._event,
        bound_correlation={},
        session_id=session_id,
        turn_id=turn_id,
        event_type=event_type,
        payload=payload,
        trace_id=None,
        actor_type=None,
        status=None,
        error=None,
        mode=None,
        event_id=event_id,
        timestamp=timestamp,
        trace_key=None,
        invocation_id=invocation_id,
        execution_id=None,
        agent_id=agent_id,
    )
    return telemetryctl._service.repair_event_sync(event)


__all__ = [
    "invocation_artifact_paths",
    "repair_canonical_event_sync",
    "repair_event_sync",
]
