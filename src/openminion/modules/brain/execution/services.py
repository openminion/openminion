from dataclasses import dataclass
import inspect
from typing import Any

from ..constants import RESPOND_KIND_ASSISTANT, RespondKindLiteral
from ..diagnostics.status import normalize_phase_status
from ..diagnostics.transitions import set_status_unchecked
from ..schemas import ActionResult, Command, Decision, Plan, StepOutput, WorkingState
from ..schemas.closure import ClosureJudgment


@dataclass(slots=True)
class RunnerExecutionServices:
    runner: Any
    suppress_lifecycle_exit_statuses: bool = False

    def save_state(self, *, state: WorkingState) -> None:
        self.runner._save_state(state)

    def emit_phase_status(
        self,
        *,
        state: WorkingState,
        logger: Any | None = None,
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
        log_event: bool = True,
    ) -> None:
        normalized_event = str(source_event or "").strip()
        if self.suppress_lifecycle_exit_statuses and normalized_event in {
            "brain.execution.exited",
            "brain.execution.failed",
        }:
            return
        self.runner._emit_phase_status(
            state=state,
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
        if logger is None or not log_event:
            return
        trace_id = str(
            getattr(state, "trace_id", "")
            or getattr(self.runner, "_trace_id", "")
            or ""
        ).strip()
        normalized = normalize_phase_status(
            trace_id=trace_id or "execution-status",
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
        event_payload = dict(payload or {})
        event_payload.update(normalized.model_dump(mode="json", exclude_none=True))
        logger.emit(
            "brain.execution_status",
            event_payload,
            trace_id=normalized.trace_id,
            status=normalized.status_key,
        )

    def respond_with_meta(
        self,
        *,
        state: WorkingState,
        logger: Any,
        message: str,
        status: str,
        action_result: ActionResult | None = None,
        kind: RespondKindLiteral = RESPOND_KIND_ASSISTANT,
    ) -> StepOutput:
        if self.suppress_lifecycle_exit_statuses:
            preserve_clarify_phase = (
                str(status).strip().lower() == "waiting_user"
                and str(getattr(state, "phase", "")).strip().upper() == "CLARIFY"
            )
            if not preserve_clarify_phase:
                state.phase = "RESPOND"
            set_status_unchecked(state, status, reason="nested_respond_passthrough")
            return StepOutput(
                session_id=state.session_id,
                status=state.status,
                message=message,
                working_state=state,
                action_result=action_result,
                kind=kind,
            )
        delegate = self.runner._respond_with_meta
        signature = inspect.signature(delegate)
        accepts_kind = "kind" in signature.parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        kwargs = {
            "state": state,
            "logger": logger,
            "message": message,
            "status": status,
            "action_result": action_result,
        }
        if accepts_kind:
            kwargs["kind"] = kind
        return delegate(**kwargs)

    def direct_response(
        self,
        *,
        user_input: str | None,
        decision: Decision,
    ) -> str:
        return self.runner._direct_response(user_input=user_input, decision=decision)

    def plan(
        self,
        *,
        state: WorkingState,
        user_input: str | None,
        logger: Any,
        decision: Decision | None = None,
    ) -> Plan:
        return self.runner._plan(
            state=state,
            user_input=user_input,
            logger=logger,
            decision=decision,
        )

    def approve_command(
        self,
        *,
        state: WorkingState,
        command: Command,
        logger: Any,
    ) -> Command:
        return self.runner._approve(state=state, command=command, logger=logger)

    def act_command(
        self,
        *,
        state: WorkingState,
        command: Command,
        logger: Any,
    ) -> tuple[ActionResult, Any]:
        return self.runner._act(state=state, command=command, logger=logger)

    def assess_plan_feasibility(
        self,
        *,
        state: WorkingState,
        user_input: str | None,
        logger: Any,
    ) -> Any:
        return self.runner._assess_plan_feasibility(
            state=state,
            user_input=user_input,
            logger=logger,
        )

    def evaluate_meta(
        self,
        *,
        state: WorkingState,
        logger: Any,
        hook: str,
        user_input: str | None = None,
        user_feedback_flags: dict[str, bool] | None = None,
        decision: Decision | None = None,
        command: Command | None = None,
        action_result: ActionResult | None = None,
    ) -> Any:
        return self.runner._evaluate_meta(
            state=state,
            logger=logger,
            hook=hook,
            user_input=user_input,
            user_feedback_flags=user_feedback_flags,
            decision=decision,
            command=command,
            action_result=action_result,
        )

    def apply_meta_directive(
        self,
        *,
        state: WorkingState,
        directive: Any,
        logger: Any,
        hook: str,
        meta_state: str | None = None,
    ) -> None:
        self.runner._apply_meta_directive(
            state=state,
            directive=directive,
            logger=logger,
            hook=hook,
            meta_state=meta_state,
        )

    def meta_override_response(
        self,
        *,
        state: WorkingState,
        logger: Any,
        directive: Any,
        fallback_message: str,
        action_result: ActionResult | None = None,
    ) -> StepOutput | None:
        return self.runner._meta_override_response(
            state=state,
            logger=logger,
            directive=directive,
            fallback_message=fallback_message,
            action_result=action_result,
        )

    def meta_tool_restriction_reason(
        self,
        *,
        command: Command,
        directive: Any,
    ) -> str | None:
        return self.runner._meta_tool_restriction_reason(
            command=command,
            directive=directive,
        )

    def command_has_side_effects(self, *, command: Command) -> bool:
        return self.runner._command_has_side_effects(command=command)

    def resolve_verification_mode(self, *, current: Any, candidate: Any | None) -> Any:
        return self.runner._resolve_verification_mode(
            current=current, candidate=candidate
        )

    def verify(
        self,
        *,
        state: WorkingState,
        command: Command,
        action_result: ActionResult,
        mode: Any,
        logger: Any,
    ) -> bool:
        return self.runner._verify(
            state=state,
            command=command,
            action_result=action_result,
            mode=mode,
            logger=logger,
        )

    def improve(
        self,
        *,
        state: WorkingState,
        report: Any,
        logger: Any,
    ) -> None:
        self.runner._improve(state=state, report=report, logger=logger)

    def compact(
        self,
        *,
        state: WorkingState,
        logger: Any,
        content: str = "",
    ) -> None:
        self.runner._compact(state=state, logger=logger, content=content)

    def evaluate_turn_closure(
        self,
        *,
        state: WorkingState,
        action_result: ActionResult | None,
        logger: Any,
        completion_reason: str,
    ) -> ClosureJudgment:
        return self.runner._evaluate_turn_closure(
            state=state,
            action_result=action_result,
            logger=logger,
            completion_reason=completion_reason,
        )

    def apply_closure_judgment(
        self,
        *,
        state: WorkingState,
        judgment: ClosureJudgment,
    ) -> str:
        return self.runner._apply_closure_judgment(state=state, judgment=judgment)

    def extract_success_memories(
        self,
        *,
        state: WorkingState,
        action_result: ActionResult | None,
        judgment: ClosureJudgment,
        logger: Any,
        outcome_snapshot: dict[str, Any] | None = None,
    ) -> list[str]:
        from .memory import extract_success_memories

        return extract_success_memories(
            self.runner,
            state=state,
            action_result=action_result,
            judgment=judgment,
            logger=logger,
            outcome_snapshot=outcome_snapshot,
        )

    def create_task(
        self,
        *,
        session_id: str,
        mode_name: str,
        goal: str,
        agent_id: str | None,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> Any:
        manager = getattr(self.runner, "task_manager", None)
        if manager is None:
            raise RuntimeError("Task service is unavailable")
        return manager.create_task(
            session_id=session_id,
            mode_name=mode_name,
            goal=goal,
            agent_id=agent_id,
            metadata=metadata,
            task_id=task_id,
        )

    def get_task(self, *, task_id: str) -> Any | None:
        manager = getattr(self.runner, "task_manager", None)
        if manager is None:
            return None
        return manager.get_task(task_id)

    def list_open_tasks_for_session(
        self,
        *,
        session_id: str,
        mode_name: str | None = None,
        limit: int = 100,
    ) -> list[Any]:
        manager = getattr(self.runner, "task_manager", None)
        if manager is None:
            return []
        return manager.list_open_tasks_for_session(
            session_id,
            mode_name=mode_name,
            limit=limit,
        )

    def save_checkpoint(
        self,
        *,
        task_id: str,
        checkpoint_id: str,
        state: dict[str, Any],
    ) -> None:
        manager = getattr(self.runner, "task_manager", None)
        if manager is None:
            raise RuntimeError("Task service is unavailable")
        manager.save_checkpoint(task_id, checkpoint_id, state)

    def get_latest_checkpoint(
        self, *, task_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        manager = getattr(self.runner, "task_manager", None)
        if manager is None:
            return None
        return manager.get_latest_checkpoint(task_id)

    def list_checkpoints(self, *, task_id: str) -> list[str]:
        manager = getattr(self.runner, "task_manager", None)
        if manager is None:
            return []
        return manager.list_checkpoints(task_id)

    def update_task_progress(self, *, task_id: str, progress: dict[str, Any]) -> None:
        manager = getattr(self.runner, "task_manager", None)
        if manager is None:
            raise RuntimeError("Task service is unavailable")
        manager.update_progress(task_id, progress)

    def transition_task(
        self,
        *,
        task_id: str,
        to_state: str,
        failure_reason: str | None = None,
    ) -> Any:
        manager = getattr(self.runner, "task_manager", None)
        if manager is None:
            raise RuntimeError("Task service is unavailable")
        return manager.transition_task(
            task_id=task_id,
            to_state=to_state,
            failure_reason=failure_reason,
        )


__all__ = ["RunnerExecutionServices"]
