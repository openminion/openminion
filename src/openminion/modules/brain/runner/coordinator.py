from __future__ import annotations

from typing import Any, Callable, Mapping

from openminion.tools.exec.command_parser import is_read_only_exec_command
from openminion.tools.exec.process import resolve_shell_family

from .tick import run_step
from .lifecycle import (
    configure_runtime_controls as configure_runtime_controls_runner_lifecycle,
    run_until_idle as run_until_idle_runner_lifecycle,
)
from .delegates import RUNNER_DELEGATES
from ..interfaces import (
    A2AAPI,
    BRAIN_RUNNER_INTERFACE_VERSION,
    ContextAPI,
    LLMAPI,
    MemoryAPI,
    MetaAPI,
    PolicyAPI,
    RetrieveAPI,
    RLMAPI,
    SafetyAPI,
    SessionAPI,
    SkillAPI,
    ToolAPI,
)
from ..schemas import (
    AgentProfile,
    MetaResult,
    StepOutput,
    WorkingState,
    new_uuid,
)
from ..diagnostics.status import PhaseStatus, normalize_phase_status

from ..config import RunnerOptions
from ..execution.public_taxonomy import public_surface_payload_for_state
from ..state import MetaApplication
from ..diagnostics.telemetry import emit_brain_operation
from openminion.base.constants import STATE_KEY_WORKING


class BrainRunner:
    contract_version = BRAIN_RUNNER_INTERFACE_VERSION

    def __init__(
        self,
        *,
        profile: AgentProfile,
        session_api: SessionAPI,
        context_api: ContextAPI | None = None,
        llm_api: LLMAPI | None = None,
        tool_api: ToolAPI | None = None,
        a2a_api: A2AAPI | None = None,
        memory_api: MemoryAPI | None = None,
        policy_api: PolicyAPI | None = None,
        meta_api: MetaAPI | None = None,
        skill_api: SkillAPI | None = None,
        retrieve_api: RetrieveAPI | None = None,
        rlm_api: RLMAPI | None = None,
        artifact_api: Any | None = None,
        safety_api: SafetyAPI | None = None,
        compress_api: Any | None = None,
        telemetryctl: Any | None = None,
        task_manager: Any | None = None,
        cron_api: Any | None = None,
        goal_runtime: Any | None = None,
        trace_id: str | None = None,
        options: RunnerOptions | None = None,
    ) -> None:
        self.profile = profile
        self.session_api = session_api
        self.context_api = context_api
        self.llm_api = llm_api
        self.tool_api = tool_api
        self.a2a_api = a2a_api
        self.memory_api = memory_api
        self.policy_api = policy_api
        self.meta_api = meta_api
        self.skill_api = skill_api
        self.retrieve_api = retrieve_api
        self.rlm_api = rlm_api
        self.artifact_api = artifact_api
        self.safety_api = safety_api
        self.compress_api = compress_api
        self.telemetryctl = telemetryctl
        self.task_manager = task_manager
        self.cron_api = cron_api
        self.goal_runtime = goal_runtime
        self._lgmh_hydrated_sessions: set[str] = set()
        self._pending_run_trigger: str | None = None
        self._pending_gateway_system_context: str | None = None
        self._trace_id = trace_id or new_uuid()
        self._call_order_tracker: dict[str, dict[str, Any]] = {}
        self._progress_callback: Callable[[PhaseStatus], None] | None = None
        self._telemetry_turn_active = False
        configure_runtime_controls_runner_lifecycle(
            self,
            meta_api=meta_api,
            options=options,
        )

    def _emit_brain_operation(
        self,
        *,
        session_id: str,
        turn_id: str,
        operation: str,
        status: str = "ok",
        count: int = 1,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        return emit_brain_operation(
            telemetryctl=self.telemetryctl,
            session_id=session_id,
            turn_id=turn_id,
            operation=operation,
            status=status,
            count=count,
            extra=extra,
        )

    def set_meta_override(self, hook: str, result: MetaResult | None) -> None:
        if self._meta_overrides is None:
            self._meta_overrides = {}
        self._meta_overrides[hook] = result

    def get_last_meta_application(self) -> MetaApplication | None:
        return self._last_meta_application

    def _emit_turn_outcome(
        self,
        *,
        session_id: str,
        result: StepOutput,
        entrypoint: str,
    ) -> None:
        state = getattr(result, STATE_KEY_WORKING, None)
        if state is None:
            return
        trace_id = str(getattr(state, "trace_id", "") or self._trace_id or "").strip()
        try:
            events = list(self.session_api.list_events(session_id))
        except Exception:
            events = []

        def _event_type(event: dict[str, Any]) -> str:
            return str(
                event.get("type", "") or event.get("event_type", "") or ""
            ).strip()

        trace_events = [
            event
            for event in events
            if str(event.get("trace_id", "") or "").strip() == trace_id
        ]

        def _count(event_type: str) -> int:
            return sum(1 for event in trace_events if _event_type(event) == event_type)

        public_surface = public_surface_payload_for_state(state)
        public_mode_name = (
            str(public_surface.pop("mode_name", "") or "").strip() or None
        )

        payload = {
            "entrypoint": entrypoint,
            "status": str(getattr(result, "status", "") or "").strip().lower(),
            "mode_name": public_mode_name,
            "workflow_name": str(
                getattr(state, "active_workflow_name", "") or ""
            ).strip()
            or None,
            "workflow_kind": str(
                getattr(state, "active_workflow_kind", "") or ""
            ).strip()
            or None,
            "final_command_id": str(getattr(state, "last_command_id", "") or "").strip()
            or None,
            "tool_request_count": _count("tool.request"),
            "tool_completed_count": _count("tool.completed"),
            "a2a_request_count": _count("a2a.request"),
            "a2a_completed_count": _count("a2a.completed"),
            "step_output_count": len(list(getattr(state, "step_outputs", []) or [])),
        }
        payload.update(
            {
                key: value
                for key, value in public_surface.items()
                if value is not None and str(value).strip() != ""
            }
        )
        payload = {
            key: value
            for key, value in payload.items()
            if value is not None or key in {"workflow_name", "workflow_kind"}
        }
        try:
            self.session_api.append_event(
                session_id,
                "turn.outcome",
                payload,
                actor_type="agent",
                actor_id=self.profile.agent_id,
                trace={"trace_id": trace_id} if trace_id else None,
                importance=3,
                redaction="none",
                status="ok"
                if str(getattr(result, "status", "") or "").strip().lower()
                not in {"error", "failed"}
                else "error",
            )
        except Exception:
            return

    def run(
        self,
        *,
        session_id: str,
        user_input: str | None = None,
        trace_id: str | None = None,
        forced_tools: list[str] | None = None,
        capability_category: str | None = None,
        progress_callback: Callable[[PhaseStatus], None] | None = None,
        approval_callback: Any | None = None,
        trigger: str = "user_input",
    ) -> StepOutput:
        previous_callback = self._progress_callback
        approval_setter = getattr(self.tool_api, "set_approval_callback", None)
        previous_approval_callback = (
            approval_setter(approval_callback) if callable(approval_setter) else None
        )
        effective_trace_id = str(trace_id or "").strip() or new_uuid()
        self._trace_id = effective_trace_id
        if (
            self.goal_runtime is not None
            and session_id not in self._lgmh_hydrated_sessions
        ):
            hydrate = getattr(self.goal_runtime, "hydrate_session_start", None)
            if callable(hydrate):
                try:
                    hydrate(session_id=session_id, session_api=self.session_api)
                except Exception:
                    pass
            self._lgmh_hydrated_sessions.add(session_id)
        if progress_callback is not None:
            self._progress_callback = progress_callback
        self._telemetry_turn_active = True
        self._emit_brain_operation(
            session_id=session_id,
            turn_id=effective_trace_id,
            operation="turn_start",
            extra={
                "entrypoint": "run",
                "forced_tools_count": len(forced_tools or []),
                "capability_category": str(capability_category or "").strip(),
                "trigger": str(trigger or "user_input"),
            },
        )
        try:
            result = run_until_idle_runner_lifecycle(
                self,
                session_id=session_id,
                user_input=user_input,
                trace_id=effective_trace_id,
                forced_tools=forced_tools,
                capability_category=capability_category,
                trigger=trigger,
            )
            turn_id = (
                str(
                    getattr(getattr(result, STATE_KEY_WORKING, None), "trace_id", "")
                    or ""
                ).strip()
                or effective_trace_id
            )
            result_status = str(getattr(result, "status", "") or "").strip().lower()
            self._emit_brain_operation(
                session_id=session_id,
                turn_id=turn_id,
                operation="turn_finish",
                status="error" if result_status in {"error", "failed"} else "ok",
                extra={
                    "entrypoint": "run",
                    "brain_status": result_status,
                },
            )
            self._emit_turn_outcome(
                session_id=session_id,
                result=result,
                entrypoint="run",
            )
            return result
        finally:
            self._telemetry_turn_active = False
            if progress_callback is not None:
                self._progress_callback = previous_callback
            if callable(approval_setter):
                approval_setter(previous_approval_callback)

    def step(
        self,
        *,
        session_id: str,
        user_input: str | None = None,
        trace_id: str | None = None,
        forced_tools: list[str] | None = None,
        capability_category: str | None = None,
        progress_callback: Callable[[PhaseStatus], None] | None = None,
    ) -> StepOutput:
        previous_callback = self._progress_callback
        effective_trace_id = str(trace_id or "").strip() or new_uuid()
        self._trace_id = effective_trace_id
        if progress_callback is not None:
            self._progress_callback = progress_callback
        if not self._telemetry_turn_active:
            self._emit_brain_operation(
                session_id=session_id,
                turn_id=effective_trace_id,
                operation="turn_start",
                extra={
                    "entrypoint": "step",
                    "forced_tools_count": len(forced_tools or []),
                    "capability_category": str(capability_category or "").strip(),
                },
            )
        try:
            result = run_step(
                self,
                session_id=session_id,
                user_input=user_input,
                trace_id=effective_trace_id,
                forced_tools=forced_tools,
                capability_category=capability_category,
            )
            if not self._telemetry_turn_active:
                turn_id = (
                    str(
                        getattr(
                            getattr(result, STATE_KEY_WORKING, None), "trace_id", ""
                        )
                        or ""
                    ).strip()
                    or effective_trace_id
                )
                result_status = str(getattr(result, "status", "") or "").strip().lower()
                self._emit_brain_operation(
                    session_id=session_id,
                    turn_id=turn_id,
                    operation="turn_finish",
                    status="error" if result_status in {"error", "failed"} else "ok",
                    extra={
                        "entrypoint": "step",
                        "brain_status": result_status,
                    },
                )
                self._emit_turn_outcome(
                    session_id=session_id,
                    result=result,
                    entrypoint="step",
                )
            return result
        finally:
            if progress_callback is not None:
                self._progress_callback = previous_callback

    def _emit_phase_status(
        self,
        *,
        state: WorkingState,
        source_phase: str | None = None,
        source_event: str | None = None,
        payload: dict[str, Any] | None = None,
        runtime_status: str | None = None,
        detail_text: str | None = None,
        terminal: bool | None = None,
        mode: str | None = None,
        mode_state: str | None = None,
        mode_label: str | None = None,
        mode_step_index: int | None = None,
        mode_step_total: int | None = None,
    ) -> None:
        callback = self._progress_callback
        if not callable(callback):
            return
        trace_id = str(getattr(state, "trace_id", "") or self._trace_id or "").strip()
        if not trace_id:
            trace_id = new_uuid()
        try:
            callback(
                normalize_phase_status(
                    trace_id=trace_id,
                    source_phase=source_phase,
                    source_event=source_event,
                    payload=payload,
                    runtime_status=runtime_status,
                    detail_text=detail_text,
                    terminal=terminal,
                    mode=mode,
                    mode_state=mode_state,
                    mode_label=mode_label,
                    mode_step_index=mode_step_index,
                    mode_step_total=mode_step_total,
                )
            )
        except Exception:
            return

    def _emit_tool_progress_event(
        self,
        *,
        kind: str,
        tool_name: str,
        args: Mapping[str, Any] | None = None,
        call_id: str = "",
        duration_ms: int | None = None,
        ok: bool | None = None,
        content: str = "",
    ) -> None:
        """Emit tool progress event helper."""
        callback = self._progress_callback
        if not callable(callback):
            return
        kind_norm = str(kind or "").strip()
        if kind_norm not in {"tool_started", "tool_completed"}:
            return
        payload: dict[str, Any] = {
            "kind": kind_norm,
            "tool_name": str(tool_name or ""),
            "args": dict(args or {}),
            "call_id": str(call_id or ""),
            "model_tool_name": "",
            "runtime_tool_name": "",
            "runtime_binding_id": "",
            "runtime_fallback_used": False,
            "runtime_fallback_chain": [],
            "runtime_resolution_source": "",
            "fallback_index": 0,
        }
        if kind_norm == "tool_started":
            payload["state"] = "running"
        else:
            ok_value = bool(ok) if ok is not None else False
            payload["ok"] = ok_value
            payload["state"] = "ok" if ok_value else "error"
            payload["content"] = str(content or "")
            if duration_ms is not None:
                try:
                    payload["duration_ms"] = int(duration_ms)
                except (TypeError, ValueError):
                    pass
        try:
            callback(payload)
        except Exception:
            return

    @staticmethod
    def _is_read_only_shell_command(command: str) -> bool:
        return is_read_only_exec_command(
            command,
            shell_family=resolve_shell_family(),
        )


def _build_delegate_method(name: str):
    def _delegate(self, *args, **kwargs):
        return RUNNER_DELEGATES[name](self, *args, **kwargs)

    _delegate.__name__ = name
    _delegate.__qualname__ = f"{BrainRunner.__name__}.{name}"
    return _delegate


for _delegate_name in RUNNER_DELEGATES:
    if not hasattr(BrainRunner, _delegate_name):
        setattr(BrainRunner, _delegate_name, _build_delegate_method(_delegate_name))


StateMachineRunner = BrainRunner
