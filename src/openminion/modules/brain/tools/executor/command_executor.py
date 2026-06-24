from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ...diagnostics.events import CanonicalEventLogger
from ...constants import (
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_COMMAND_KIND_ASK_USER,
    BRAIN_COMMAND_KIND_TOOL,
)
from ...execution.skill_binding import activate_skill_for_command
from ...loop.tools.contracts import (
    CommandExecutionOutcome,
    PreparedToolDispatch,
    PrepareOutcome,
    RawToolResult,
)
from ...schemas import (
    ActionResult,
    Command,
)
from .tool import (
    _TOOL_OUTCOME_RECORD_TYPE,
    _prepare_outcome_disposition,
    _stage_tool_outcome_candidate,
    execute_prepared_tool_dispatch,
    finalize_tool_result,
    prepare_tool_dispatch,
    resolve_tool_spec_payload,
    sanitize_tool_command_args,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...schemas import WorkingState
    from ...runner import BrainRunner


class CommandExecutor(Protocol):
    def execute_command(
        self,
        *,
        state: "WorkingState",
        command: Command,
        logger: CanonicalEventLogger,
        preapproved: bool = False,
        approve_only: bool = False,
        include_reflect: bool = True,
    ) -> CommandExecutionOutcome: ...

    def prepare_tool_dispatch(
        self,
        *,
        state: "WorkingState",
        command: Command,
        logger: CanonicalEventLogger,
        include_reflect: bool = True,
    ) -> PreparedToolDispatch | PrepareOutcome: ...

    def execute_prepared_tool_dispatch(
        self,
        *,
        prepared_dispatch: PreparedToolDispatch,
    ) -> RawToolResult: ...

    def finalize_tool_result(
        self,
        *,
        state: "WorkingState",
        prepared_dispatch: PreparedToolDispatch,
        raw_result: RawToolResult,
        logger: CanonicalEventLogger,
    ) -> CommandExecutionOutcome: ...

    def advance_after_action(
        self,
        *,
        state: "WorkingState",
        action_result: ActionResult,
        force_replan: bool = False,
        logger: CanonicalEventLogger | None = None,
    ) -> None: ...


@dataclass(slots=True)
class RunnerCommandExecutor:
    runner: "BrainRunner"

    def execute_command(
        self,
        *,
        state: "WorkingState",
        command: Command,
        logger: CanonicalEventLogger,
        preapproved: bool = False,
        approve_only: bool = False,
        include_reflect: bool = True,
    ) -> CommandExecutionOutcome:
        activate_skill_for_command(state, command)
        approved = (
            command
            if preapproved
            else self.runner._approve(state=state, command=command, logger=logger)
        )
        if approve_only:
            return CommandExecutionOutcome(approved_command=approved)
        if approved.kind == BRAIN_COMMAND_KIND_ASK_USER:
            outcome = CommandExecutionOutcome(
                approved_command=approved,
                action_result=ActionResult(
                    command_id=approved.command_id or command.command_id,
                    status=BRAIN_ACTION_STATUS_NEEDS_USER,
                    summary=approved.question or "Need clarification.",
                ),
            )
            if getattr(command, "kind", "") == BRAIN_COMMAND_KIND_TOOL:
                self.stage_tool_outcome_candidate(
                    state=state,
                    tool_name=str(getattr(command, "tool_name", "") or "").strip(),
                    action_result=outcome.action_result,
                    command=command,
                    forced_outcome="policy_denied",
                )
            return outcome
        action_result, job = self.runner._act(
            state=state,
            command=approved,
            logger=logger,
        )
        if getattr(approved, "kind", "") == BRAIN_COMMAND_KIND_TOOL:
            self.stage_tool_outcome_candidate(
                state=state,
                tool_name=str(getattr(approved, "tool_name", "") or "").strip(),
                action_result=action_result,
                command=approved,
            )
        # include_reflect parameter retained for interface compatibility only.
        return CommandExecutionOutcome(
            approved_command=approved,
            action_result=action_result,
            job=job,
        )

    def stage_tool_outcome_candidate(
        self,
        *,
        state: "WorkingState",
        tool_name: str,
        action_result: ActionResult | None,
        command: Command | None,
        forced_outcome: str | None = None,
    ) -> str | None:
        return _stage_tool_outcome_candidate(
            self.runner,
            state=state,
            tool_name=tool_name,
            action_result=action_result,
            command=command,
            forced_outcome=forced_outcome,
        )

    def advance_after_action(
        self,
        *,
        state: "WorkingState",
        action_result: ActionResult,
        force_replan: bool = False,
        logger: CanonicalEventLogger | None = None,
    ) -> None:
        self.runner._advance_after_action(
            state=state,
            action_result=action_result,
            force_replan=force_replan,
            logger=logger,
        )

    def prepare_tool_dispatch(
        self,
        *,
        state: "WorkingState",
        command: Command,
        logger: CanonicalEventLogger,
        include_reflect: bool = True,
    ) -> PreparedToolDispatch | PrepareOutcome:
        del include_reflect
        approved = self.runner._approve(state=state, command=command, logger=logger)
        if approved.kind == BRAIN_COMMAND_KIND_ASK_USER:
            summary = approved.question or "Need clarification."
            disposition = _prepare_outcome_disposition(approved)
            action_result = ActionResult(
                command_id=str(
                    getattr(approved, "command_id", "") or command.command_id
                ),
                status=BRAIN_ACTION_STATUS_NEEDS_USER,
                summary=summary,
            )
            if getattr(command, "kind", "") == BRAIN_COMMAND_KIND_TOOL:
                self.stage_tool_outcome_candidate(
                    state=state,
                    tool_name=str(getattr(command, "tool_name", "") or "").strip(),
                    action_result=action_result,
                    command=command,
                    forced_outcome="policy_denied",
                )
            return PrepareOutcome(
                approved_command=approved,
                original_command=command,
                command_id=str(
                    getattr(approved, "command_id", "") or command.command_id
                ),
                tool_name=str(getattr(command, "tool_name", "") or "").strip(),
                disposition=disposition,
                action_result=action_result,
            )
        return prepare_tool_dispatch(
            self.runner,
            state=state,
            command=approved,
            original_command=command,
            logger=logger,
        )

    def execute_prepared_tool_dispatch(
        self,
        *,
        prepared_dispatch: PreparedToolDispatch,
    ) -> RawToolResult:
        return execute_prepared_tool_dispatch(self.runner, prepared_dispatch)

    def finalize_tool_result(
        self,
        *,
        state: "WorkingState",
        prepared_dispatch: PreparedToolDispatch,
        raw_result: RawToolResult,
        logger: CanonicalEventLogger,
    ) -> CommandExecutionOutcome:
        return finalize_tool_result(
            self.runner,
            state=state,
            prepared_dispatch=prepared_dispatch,
            raw_result=raw_result,
            logger=logger,
        )


__all__ = [
    "CommandExecutionOutcome",
    "CommandExecutor",
    "RunnerCommandExecutor",
    "_TOOL_OUTCOME_RECORD_TYPE",
    "execute_prepared_tool_dispatch",
    "finalize_tool_result",
    "prepare_tool_dispatch",
    "resolve_tool_spec_payload",
    "sanitize_tool_command_args",
]
