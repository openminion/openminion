"""Runtime bootstrap and API-facing runtime facade."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Optional

from openminion.base.config import EnvironmentConfig, RunProfileOverrides
from openminion.modules.llm import RuntimeLLMHandle

from openminion.api.core.bootstrap import RuntimeBootstrapMixin
from openminion.api.core.exposure import RuntimeToolExposureMixin
from openminion.api.core.lifecycle import (
    close_runtime_components,
    initialize_runtime_components,
)
from openminion.api.core.profiles import RuntimeProfilesMixin

_CANONICAL_TURN_PATH = (
    "services/runtime/ingress.run_turn_payload",
    "services/request_orchestrator.run_turn",
    "GatewayService.run_once",
    "BrainBridgeService.run_turn",
    "BrainRunner.run",
)
_CANONICAL_TURN_PATH_REF = "openminion.api.runtime.APIRuntime.runtime_posture"
_EXECUTION_BOUNDARY_POLICY_REF = "openminion.services.security.tool_execution.build_execution_boundary_policy_adapter"
_CAPABILITY_REF = "openminion.api.queries.runtime_reports.build_runtime_posture_report"
_DISABLE_SECURITY_POLICY_ENV = "OPENMINION_DISABLE_SECURITY_POLICY"


@dataclass
class APIRuntime(RuntimeBootstrapMixin, RuntimeProfilesMixin, RuntimeToolExposureMixin):
    @staticmethod
    def _security_policy_disabled(runtime_env: object) -> bool:
        return EnvironmentConfig.from_sources(runtime_env=runtime_env).get_bool(
            _DISABLE_SECURITY_POLICY_ENV, False
        )

    def __post_init__(self) -> None:
        self._finalizer = initialize_runtime_components(
            self,
            tool_exposure_event_sink=self._emit_tool_exposure_event,
        )

    @property
    def llm(self) -> RuntimeLLMHandle:
        return self.llm_runtime

    def tool_inventory_report(self) -> list[dict[str, Any]]:
        reports = import_module("openminion.api.queries.runtime_reports")
        return reports.build_tool_inventory_report(self)

    def tool_schema_report(self, *, tool_name: str) -> dict[str, Any] | None:
        reports = import_module("openminion.api.queries.runtime_reports")
        return reports.build_tool_schema_report(self, tool_name=tool_name)

    def capability_report(
        self,
        agent_id: Optional[str] = None,
        overrides: RunProfileOverrides | None = None,
    ) -> dict[str, Any]:
        reports = import_module("openminion.api.queries.runtime_reports")
        return reports.build_capability_report(
            self,
            agent_id=agent_id,
            overrides=overrides,
        )

    def runtime_posture(
        self,
        agent_id: Optional[str] = None,
        overrides: RunProfileOverrides | None = None,
    ) -> dict[str, Any]:
        reports = import_module("openminion.api.queries.runtime_reports")
        return reports.build_runtime_posture_report(
            self,
            agent_id=agent_id,
            overrides=overrides,
            canonical_turn_path=_CANONICAL_TURN_PATH,
            canonical_turn_path_ref=_CANONICAL_TURN_PATH_REF,
            execution_boundary_policy_ref=_EXECUTION_BOUNDARY_POLICY_REF,
            capability_layering_ref=_CAPABILITY_REF,
        )

    def runtime_self_model(
        self,
        agent_id: Optional[str] = None,
        overrides: RunProfileOverrides | None = None,
    ) -> dict[str, Any]:
        queries = import_module("openminion.api.queries.self_model")
        snapshot = queries.build_runtime_self_model(
            self,
            agent_id=agent_id,
            overrides=overrides,
        )
        self._emit_runtime_self_model_snapshot(snapshot.model_dump(mode="json"))
        return snapshot.model_dump(mode="json")

    def _emit_runtime_self_model_snapshot(self, snapshot: dict[str, Any]) -> None:
        try:
            from openminion.modules.telemetry.self_awareness import (
                build_self_model_snapshot_event,
            )
            from openminion.modules.telemetry.schemas import TelemetryEvent

            telemetry_service = getattr(self, "telemetry_service", None)
            record_sync = getattr(telemetry_service, "record_event_sync", None)
            if record_sync is None:
                return
            event_type, data = build_self_model_snapshot_event(snapshot)
            record_sync(
                TelemetryEvent(
                    session_id="runtime",
                    turn_id="self-model",
                    event_type=event_type,
                    data=data,
                )
            )
        except Exception:
            return

    def run_turn(
        self,
        *,
        payload: dict[str, object],
        request_id: str | None = None,
        progress_callback=None,  # noqa: ANN001
        approval_callback=None,  # noqa: ANN001
    ) -> dict[str, object]:
        from openminion.services.runtime.ingress import run_turn_payload

        return run_turn_payload(
            runtime=self,
            payload=dict(payload),
            request_id=request_id,
            progress_callback=progress_callback,
            approval_callback=approval_callback,
        )

    def submit_turn(self, *, payload: dict[str, object]):
        from openminion.services.runtime.ingress import submit_turn_payload

        return submit_turn_payload(runtime=self, payload=dict(payload))

    def evict_agent(self, agent_id: str, *, reason: str = "manual") -> bool:
        normalized = str(agent_id or "").strip()
        if not normalized:
            return False
        with self._agent_runtime_lock:
            had_gateway = normalized in self._gateways
            had_agent = normalized in self._agent_services
        self.evict_agent_runtime(agent_id=normalized, reason=reason)
        return bool(had_gateway or had_agent)

    def close(self) -> None:
        if self._closed:
            return
        finalizer = getattr(self, "_finalizer", None)
        if finalizer is not None and finalizer.alive:
            finalizer.detach()
        close_runtime_components(
            retrieve_ctl=getattr(self, "retrieve_ctl", None),
            action_policy=getattr(self, "action_policy", None),
            runtime_manager=getattr(self, "runtime_manager", None),
            lifecycle_bridge=getattr(self, "_lifecycle_event_bridge", None),
            tools=getattr(self, "tools", None),
            runtime_storage=getattr(self, "runtime_storage", None),
            sandbox_runner=getattr(self, "sandbox_runner", None),
            authored_tools=getattr(self, "authored_tools", None),
            telemetry_service=getattr(self, "telemetry_service", None),
        )
        self._closed = True


def _bootstrap_openminion_brain_import_path() -> None:
    return
