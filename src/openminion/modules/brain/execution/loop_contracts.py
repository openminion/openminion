from dataclasses import dataclass, field
from typing import Any

from ..constants import (
    RESPOND_KIND_ASSISTANT,
    RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    RespondKindLiteral,
)
from ..schemas import (
    ActionResult,
    Command,
    Decision,
    JobHandle,
    Plan,
    StepOutput,
    WorkingState,
)
from ..schemas.closure import ClosureJudgment
from ..tools.executor import CommandExecutor
from .ports import ModeServices


@dataclass(slots=True)
class ExecutionResult:
    status: str
    working_state: WorkingState
    message: str | None = None
    action_result: ActionResult | None = None
    judgment: ClosureJudgment | None = None
    kind: RespondKindLiteral = RESPOND_KIND_ASSISTANT

    @classmethod
    def from_step_output(
        cls,
        output: StepOutput,
        *,
        judgment: ClosureJudgment | None = None,
    ) -> "ExecutionResult":
        raw_kind = str(
            getattr(output, "kind", RESPOND_KIND_ASSISTANT) or RESPOND_KIND_ASSISTANT
        )
        kind: RespondKindLiteral = (
            RESPOND_KIND_POLICY_CONFIRMATION_PROMPT
            if raw_kind == RESPOND_KIND_POLICY_CONFIRMATION_PROMPT
            else RESPOND_KIND_ASSISTANT
        )
        return cls(
            status=output.status,
            working_state=output.working_state,
            message=output.message,
            action_result=output.action_result,
            judgment=judgment,
            kind=kind,
        )

    def to_step_output(self) -> StepOutput:
        return StepOutput(
            session_id=self.working_state.session_id,
            status=self.status,
            message=self.message,
            working_state=self.working_state,
            action_result=self.action_result,
            kind=self.kind,
        )


@dataclass(slots=True)
class ExecutionContext:
    state: WorkingState
    decision: Decision
    user_input: str | None
    logger: Any
    options: Any
    llm_adapter: Any
    command_executor: CommandExecutor
    _services: ModeServices = field(repr=False)

    @property
    def mode_name(self) -> str:
        return str(
            getattr(self.decision, "route", getattr(self.decision, "mode", "")) or ""
        ).strip()

    def save_state(self, state: WorkingState | None = None) -> None:
        self._services.save_state(state=state or self.state)

    def emit_status(
        self,
        *,
        state: WorkingState | None = None,
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
        self._services.emit_phase_status(
            state=state or self.state,
            logger=self.logger if logger is None else logger,
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
            log_event=log_event,
        )

    def respond(
        self,
        *,
        state: WorkingState | None = None,
        message: str,
        status: str,
        action_result: ActionResult | None = None,
        kind: RespondKindLiteral = RESPOND_KIND_ASSISTANT,
    ) -> StepOutput:
        return self._services.respond_with_meta(
            state=state or self.state,
            logger=self.logger,
            message=message,
            status=status,
            action_result=action_result,
            kind=kind,
        )

    def direct_response(
        self,
        *,
        user_input: str | None = None,
        decision: Decision | None = None,
    ) -> str:
        return self._services.direct_response(
            user_input=self.user_input if user_input is None else user_input,
            decision=decision or self.decision,
        )

    def plan(
        self,
        *,
        user_input: str | None = None,
        decision: Decision | None = None,
    ) -> Plan:
        return self._services.plan(
            state=self.state,
            user_input=self.user_input if user_input is None else user_input,
            logger=self.logger,
            decision=decision or self.decision,
        )

    def approve_command(self, *, command: Command) -> Command:
        return self._services.approve_command(
            state=self.state,
            command=command,
            logger=self.logger,
        )

    def act_command(
        self,
        *,
        command: Command,
    ) -> tuple[ActionResult, JobHandle | None]:
        return self._services.act_command(
            state=self.state,
            command=command,
            logger=self.logger,
        )

    def assess_plan_feasibility(self, *, user_input: str | None = None) -> Any:
        return self._services.assess_plan_feasibility(
            state=self.state,
            user_input=self.user_input if user_input is None else user_input,
            logger=self.logger,
        )

    def evaluate_meta(
        self,
        *,
        hook: str,
        user_input: str | None = None,
        user_feedback_flags: dict[str, bool] | None = None,
        decision: Decision | None = None,
        command: Command | None = None,
        action_result: ActionResult | None = None,
    ) -> Any:
        return self._services.evaluate_meta(
            state=self.state,
            logger=self.logger,
            hook=hook,
            user_input=self.user_input if user_input is None else user_input,
            user_feedback_flags=user_feedback_flags,
            decision=decision or self.decision,
            command=command,
            action_result=action_result,
        )

    def apply_meta_directive(
        self,
        *,
        directive: Any,
        hook: str,
        meta_state: str | None = None,
    ) -> None:
        self._services.apply_meta_directive(
            state=self.state,
            directive=directive,
            logger=self.logger,
            hook=hook,
            meta_state=meta_state,
        )

    def meta_override_response(
        self,
        *,
        directive: Any,
        fallback_message: str,
        action_result: ActionResult | None = None,
    ) -> StepOutput | None:
        return self._services.meta_override_response(
            state=self.state,
            logger=self.logger,
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
        return self._services.meta_tool_restriction_reason(
            command=command,
            directive=directive,
        )

    def command_has_side_effects(self, *, command: Command) -> bool:
        return self._services.command_has_side_effects(command=command)

    def resolve_verification_mode(
        self,
        *,
        current: Any,
        candidate: Any | None,
    ) -> Any:
        return self._services.resolve_verification_mode(
            current=current, candidate=candidate
        )

    def verify(
        self,
        *,
        command: Command,
        action_result: ActionResult,
        mode: Any,
    ) -> bool:
        return self._services.verify(
            state=self.state,
            command=command,
            action_result=action_result,
            mode=mode,
            logger=self.logger,
        )

    def improve(self, *, report: Any) -> None:
        self._services.improve(state=self.state, report=report, logger=self.logger)

    def compact(self, *, content: str = "") -> None:
        self._services.compact(state=self.state, logger=self.logger, content=content)

    def evaluate_turn_closure(
        self,
        *,
        action_result: ActionResult | None,
        completion_reason: str,
    ) -> ClosureJudgment:
        return self._services.evaluate_turn_closure(
            state=self.state,
            action_result=action_result,
            logger=self.logger,
            completion_reason=completion_reason,
        )

    def apply_closure_judgment(self, *, judgment: ClosureJudgment) -> str:
        return self._services.apply_closure_judgment(
            state=self.state,
            judgment=judgment,
        )

    def extract_success_memories(
        self,
        *,
        action_result: ActionResult | None,
        judgment: ClosureJudgment,
        outcome_snapshot: dict[str, Any] | None = None,
    ) -> list[str]:
        return self._services.extract_success_memories(
            state=self.state,
            action_result=action_result,
            judgment=judgment,
            logger=self.logger,
            outcome_snapshot=outcome_snapshot,
        )

    def create_task(
        self,
        *,
        session_id: str,
        mode_name: str,
        goal: str,
        agent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> Any:
        return self._services.create_task(
            session_id=session_id,
            mode_name=mode_name,
            goal=goal,
            agent_id=agent_id,
            metadata=metadata,
            task_id=task_id,
        )

    def get_task(self, *, task_id: str) -> Any | None:
        return self._services.get_task(task_id=task_id)

    def list_open_tasks_for_session(
        self,
        *,
        session_id: str,
        mode_name: str | None = None,
        limit: int = 100,
    ) -> list[Any]:
        return self._services.list_open_tasks_for_session(
            session_id=session_id,
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
        self._services.save_checkpoint(
            task_id=task_id,
            checkpoint_id=checkpoint_id,
            state=state,
        )

    def get_latest_checkpoint(
        self, *, task_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        return self._services.get_latest_checkpoint(task_id=task_id)

    def list_checkpoints(self, *, task_id: str) -> list[str]:
        return self._services.list_checkpoints(task_id=task_id)

    def update_task_progress(self, *, task_id: str, progress: dict[str, Any]) -> None:
        self._services.update_task_progress(task_id=task_id, progress=progress)

    def transition_task(
        self,
        *,
        task_id: str,
        to_state: str,
        failure_reason: str | None = None,
    ) -> Any:
        return self._services.transition_task(
            task_id=task_id,
            to_state=to_state,
            failure_reason=failure_reason,
        )


__all__ = [
    "ExecutionContext",
    "ExecutionResult",
    "ModeServices",
]
