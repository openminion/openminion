"""Tool-exposure behavior for the API runtime facade."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any


class RuntimeToolExposureMixin:
    def tool_exposure_status(
        self,
        *,
        session_id: str = "",
        task_id: str = "",
        target_id: str = "",
    ) -> dict[str, Any]:
        return self.tools.exposure_service.snapshot(
            session_id=session_id,
            task_id=task_id,
            target_id=target_id,
        )

    def activate_tool_profile(
        self,
        profile_id: str,
        *,
        session_id: str,
        task_id: str = "",
        target_id: str = "",
        target_kind: str = "",
        credential_scopes: tuple[str, ...] = (),
        dependencies: tuple[str, ...] = (),
        approved: bool = False,
        ttl_seconds: float | None = None,
        activation_reason: str = "",
        approved_by: str = "",
        policy_source: str = "",
    ) -> dict[str, Any]:
        activation = self.tools.exposure_service.activate(
            profile_id,
            session_id=session_id,
            task_id=task_id,
            target_id=target_id,
            target_kind=target_kind,
            credential_scopes=credential_scopes,
            dependencies=dependencies,
            approved=approved,
            ttl_seconds=ttl_seconds,
            activation_reason=activation_reason,
            approved_by=approved_by,
            policy_source=policy_source,
        )
        return asdict(activation)

    def deactivate_tool_profile(
        self,
        profile_id: str,
        *,
        session_id: str,
        task_id: str = "",
        target_id: str = "",
    ) -> bool:
        return self.tools.exposure_service.deactivate(
            profile_id,
            session_id=session_id,
            task_id=task_id,
            target_id=target_id,
        )

    def _emit_tool_exposure_event(self, record: dict[str, Any]) -> None:
        from openminion.modules.telemetry.schemas import TelemetryEvent

        telemetry_service = getattr(self, "telemetry_service", None)
        record_sync = getattr(telemetry_service, "record_event_sync", None)
        if record_sync is None:
            return
        session_id = str(record.get("session_id", "") or "runtime")
        turn_id = str(
            record.get("task_id", "")
            or record.get("audit_id", "")
            or "tool-exposure"
        )
        record_sync(
            TelemetryEvent(
                session_id=session_id,
                turn_id=turn_id,
                event_type=f"tool.exposure.{record.get('event', 'unknown')}",
                data={
                    key: value
                    for key, value in record.items()
                    if key not in {"event", "session_id", "task_id", "timestamp"}
                },
            )
        )
