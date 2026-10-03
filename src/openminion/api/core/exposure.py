"""Tool-exposure behavior for the API runtime facade."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from openminion.modules.storage import is_room_session_key
from openminion.modules.tool import (
    dependency_probe_context_from_api_runtime,
    evaluate_tool_dependencies,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.exposure.service import (
    SECURITY_LAB_ALLOWED_TOOL_IDS,
    resolve_security_lab_metadata,
    security_lab_scope_fingerprint,
)
from openminion.modules.tool.exposure.contracts import ToolExposureSession

ToolExposureError = ToolRuntimeError


def _security_lab_activation(
    runtime: Any,
    *,
    session_id: str,
    target_id: str,
    approved: bool,
    ttl_seconds: float | None,
    activation_reason: str,
    approved_by: str,
    policy_source: str,
) -> ToolExposureSession:
    config, runner = runtime.config.runtime.security_lab, runtime.security_lab_runner
    if config is None or runner is None:
        raise ToolRuntimeError("INVALID_ARGUMENT", "security lab is not configured")
    if not approved or not str(approved_by or "").strip():
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "security lab requires an identified human approver"
        )
    if ttl_seconds is None:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "security lab approval requires a finite ttl_seconds"
        )
    requested_target = str(target_id or "").strip()
    if requested_target and requested_target != config.target_container:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "security lab target does not match configuration"
        )

    session = runtime.sessions.get_session(str(session_id or "").strip())
    if session is None or is_room_session_key(str(session.session_key or "")):
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "security lab requires one existing non-room session"
        )
    if str(session.owner_agent_id or "").strip() != config.agent_identity_id:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "security lab session is bound to a different agent"
        )

    agent_service = runtime.resolve_agent_service(config.agent_identity_id)
    identity = agent_service._identity_security_lab_facts()  # noqa: SLF001
    allowed_tools = frozenset(identity.get("allowed_tools", ()))
    if (
        not identity.get("lab_required")
        or identity.get("tool_use") != "restricted"
        or allowed_tools != SECURITY_LAB_ALLOWED_TOOL_IDS
    ):
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "security lab identity must use the exact restricted tool posture",
        )
    profile_revision = int(identity.get("profile_revision", 0) or 0)
    profile_version = str(identity.get("profile_version", "") or "").strip()
    if profile_revision <= 0 or not profile_version:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "security lab identity version is unavailable"
        )

    try:
        scope = runner.preflight()
    except RuntimeError as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", f"security lab preflight failed: {exc}"
        ) from exc
    resolved_fingerprint = security_lab_scope_fingerprint(
        runner_scope_fingerprint=scope.resolved_scope_fingerprint,
        session_id=session_id,
        agent_id=config.agent_identity_id,
        profile_revision=profile_revision,
        profile_version=profile_version,
        allowed_tools=allowed_tools,
    )
    return runtime.tools.exposure_service.activate(
        "security_lab",
        session_id=session_id,
        target_id=config.target_container,
        target_kind="local_docker",
        approved=True,
        ttl_seconds=ttl_seconds,
        activation_reason=activation_reason,
        approved_by=approved_by,
        policy_source=policy_source,
        agent_id=config.agent_identity_id,
        profile_revision=profile_revision,
        profile_version=profile_version,
        config_fingerprint=scope.config_fingerprint,
        resolved_scope_fingerprint=resolved_fingerprint,
        daemon_id=scope.daemon_id,
        target_container_id=scope.target_container_id,
        target_image_id=scope.target_image_id,
        worker_image_digest=scope.worker_image_digest,
        isolation_mode="target-network-namespace",
    )


class RuntimeToolExposureMixin:
    def tool_exposure_status(
        self,
        *,
        session_id: str = "",
        task_id: str = "",
        target_id: str = "",
    ) -> dict[str, Any]:
        snapshot = self.tools.exposure_service.snapshot(
            session_id=session_id,
            task_id=task_id,
            target_id=target_id,
        )
        readiness = evaluate_tool_dependencies(
            getattr(self, "tools"),
            context=dependency_probe_context_from_api_runtime(self),
        )
        profiled_tools: set[str] = set()
        for profile in snapshot.get("profiles", []):
            profiled_tools.update(str(name) for name in profile.get("tool_names", []))
            statuses = {
                status.dependency_id: status
                for tool_name in profile.get("tool_names", [])
                for status in readiness.get(str(tool_name), ())
            }
            profile["dependency_readiness"] = (
                "degraded"
                if any(status.state != "ready" for status in statuses.values())
                else "ready"
            )
            profile["dependency_statuses"] = [
                statuses[key].as_dict() for key in sorted(statuses)
            ]
        snapshot["unprofiled_dependency_tools"] = [
            {
                "tool_name": tool_name,
                "dependency_readiness": (
                    "degraded"
                    if any(status.state != "ready" for status in statuses)
                    else "ready"
                ),
                "dependency_statuses": [status.as_dict() for status in statuses],
            }
            for tool_name, statuses in sorted(readiness.items())
            if tool_name not in profiled_tools
        ]
        snapshot["security_lab"] = self._security_lab_status(session_id=session_id)
        return snapshot

    def _security_lab_status(self, *, session_id: str) -> dict[str, Any]:
        config = getattr(getattr(self, "config"), "runtime").security_lab
        if config is None:
            return {"state": "not_configured"}
        runner = getattr(self, "security_lab_runner", None)
        service = getattr(self, "resolve_agent_service")(config.agent_identity_id)
        identity = service._identity_security_lab_facts()  # noqa: SLF001
        status: dict[str, Any] = {
            "state": "unavailable",
            "label": config.label,
            "target": config.target_container,
            "worker_image_digest": config.worker_image,
            "isolation_mode": "target-network-namespace",
            "allowed_tools": sorted(identity.get("allowed_tools", ())),
            "limits": {
                "timeout_seconds": config.command_timeout_seconds,
                "max_output_bytes": config.max_output_bytes,
                "cpu": config.cpu_limit,
                "memory_bytes": config.memory_bytes,
                "pids": config.pids_limit,
            },
        }
        try:
            scope = runner.preflight() if runner is not None else None
        except RuntimeError as exc:
            status.update(daemon_state="unavailable", daemon_reason=str(exc))
        else:
            status["daemon_state"] = "ready" if scope is not None else "unavailable"
            if scope is not None:
                status.update(
                    daemon_id=scope.daemon_id,
                    target_container_id=scope.target_container_id,
                    target_image_id=scope.target_image_id,
                    worker_image_digest=scope.worker_image_digest,
                )
        sessions = getattr(self, "sessions")
        session = sessions.get_session(session_id) if session_id else None
        if session is None:
            status["reason"] = "existing session required"
            return status
        if (
            is_room_session_key(str(session.session_key or ""))
            or str(session.owner_agent_id or "") != config.agent_identity_id
        ):
            status["reason"] = "session is not bound to the configured lab agent"
            return status
        tools = getattr(self, "tools")
        metadata = resolve_security_lab_metadata(
            tools.exposure_service,
            config=config,
            runner=runner,
            identity=identity,
            session_id=session_id,
        )
        status.update(
            state=str(metadata.get("security_lab_state", "unavailable")),
            reason=str(metadata.get("security_lab_reason", "")),
            activation_id=str(metadata.get("activation_id", "")),
            expires_at=str(metadata.get("activation_expires_at", "")),
            scope=str(metadata.get("resolved_scope_fingerprint", "")),
        )
        if status["state"] == "ready":
            activation = tools.exposure_service.resolve_exact_activation(
                "security_lab", session_id=session_id
            )
            status["approved_by"] = activation.approved_by
        return status

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
        if profile_id == "security_lab":
            activation = _security_lab_activation(
                self,
                session_id=session_id,
                target_id=target_id,
                approved=approved,
                ttl_seconds=ttl_seconds,
                activation_reason=activation_reason,
                approved_by=approved_by,
                policy_source=policy_source,
            )
            return asdict(activation)
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
            record.get("task_id", "") or record.get("audit_id", "") or "tool-exposure"
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
