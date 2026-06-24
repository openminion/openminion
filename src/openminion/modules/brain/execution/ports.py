from typing import Any, Protocol

from ..constants import RESPOND_KIND_ASSISTANT, RespondKindLiteral
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


class ModeServices(Protocol):
    def save_state(self, *, state: WorkingState) -> None: ...

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
    ) -> None: ...

    def respond_with_meta(
        self,
        *,
        state: WorkingState,
        logger: Any,
        message: str,
        status: str,
        action_result: ActionResult | None = None,
        kind: RespondKindLiteral = RESPOND_KIND_ASSISTANT,
    ) -> StepOutput: ...

    def direct_response(
        self,
        *,
        user_input: str | None,
        decision: Decision,
    ) -> str: ...

    def plan(
        self,
        *,
        state: WorkingState,
        user_input: str | None,
        logger: Any,
        decision: Decision | None = None,
    ) -> Plan: ...

    def approve_command(
        self,
        *,
        state: WorkingState,
        command: Command,
        logger: Any,
    ) -> Command: ...

    def act_command(
        self,
        *,
        state: WorkingState,
        command: Command,
        logger: Any,
    ) -> tuple[ActionResult, JobHandle | None]: ...

    def assess_plan_feasibility(
        self,
        *,
        state: WorkingState,
        user_input: str | None,
        logger: Any,
    ) -> Any: ...

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
    ) -> Any: ...

    def apply_meta_directive(
        self,
        *,
        state: WorkingState,
        directive: Any,
        logger: Any,
        hook: str,
        meta_state: str | None = None,
    ) -> None: ...

    def meta_override_response(
        self,
        *,
        state: WorkingState,
        logger: Any,
        directive: Any,
        fallback_message: str,
        action_result: ActionResult | None = None,
    ) -> StepOutput | None: ...

    def meta_tool_restriction_reason(
        self,
        *,
        command: Command,
        directive: Any,
    ) -> str | None: ...

    def command_has_side_effects(self, *, command: Command) -> bool: ...

    def resolve_verification_mode(
        self, *, current: Any, candidate: Any | None
    ) -> Any: ...

    def verify(
        self,
        *,
        state: WorkingState,
        command: Command,
        action_result: ActionResult,
        mode: Any,
        logger: Any,
    ) -> bool: ...

    def improve(
        self,
        *,
        state: WorkingState,
        report: Any,
        logger: Any,
    ) -> None: ...

    def compact(
        self,
        *,
        state: WorkingState,
        logger: Any,
        content: str = "",
    ) -> None: ...

    def evaluate_turn_closure(
        self,
        *,
        state: WorkingState,
        action_result: ActionResult | None,
        logger: Any,
        completion_reason: str,
    ) -> ClosureJudgment: ...

    def apply_closure_judgment(
        self,
        *,
        state: WorkingState,
        judgment: ClosureJudgment,
    ) -> str: ...

    def extract_success_memories(
        self,
        *,
        state: WorkingState,
        action_result: ActionResult | None,
        judgment: ClosureJudgment,
        logger: Any,
        outcome_snapshot: dict[str, Any] | None = None,
    ) -> list[str]: ...

    def create_task(
        self,
        *,
        session_id: str,
        mode_name: str,
        goal: str,
        agent_id: str | None,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> Any: ...

    def get_task(self, *, task_id: str) -> Any | None: ...

    def list_open_tasks_for_session(
        self,
        *,
        session_id: str,
        mode_name: str | None = None,
        limit: int = 100,
    ) -> list[Any]: ...

    def save_checkpoint(
        self,
        *,
        task_id: str,
        checkpoint_id: str,
        state: dict[str, Any],
    ) -> None: ...

    def get_latest_checkpoint(
        self, *, task_id: str
    ) -> tuple[str, dict[str, Any]] | None: ...

    def list_checkpoints(self, *, task_id: str) -> list[str]: ...

    def update_task_progress(
        self, *, task_id: str, progress: dict[str, Any]
    ) -> None: ...

    def transition_task(
        self,
        *,
        task_id: str,
        to_state: str,
        failure_reason: str | None = None,
    ) -> Any: ...


__all__ = ["ModeServices"]
