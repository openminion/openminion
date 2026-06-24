"""Context adapter for the coding strategy tool loop."""

from typing import Any, Callable

from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_NEEDS_USER
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.loop.tools.confirmation import (
    confirmation_required_user_message,
)
from openminion.modules.brain.loop.tools.iteration.helpers import (
    _execute_prepared_tool_dispatch_from_context,
    _finalize_tool_result_from_context,
)
from openminion.modules.brain.runner.tick.context import (
    _store_pending_confirmation_metadata,
)
from openminion.modules.brain.schemas import ActionResult, new_uuid


class _CodingLoopContextAdapter:
    def __init__(
        self,
        ctx: ExecutionContext,
        *,
        on_command_result: Callable[[Any, ActionResult], None] | None = None,
    ) -> None:
        self.state = ctx.state
        self._ctx = ctx
        self._on_command_result = on_command_result

    def execute_command(
        self,
        *,
        command: Any,
        include_reflect: bool = False,
    ):
        outcome = self._ctx.command_executor.execute_command(
            state=self._ctx.state,
            command=command,
            logger=self._ctx.logger,
            include_reflect=include_reflect,
        )
        return self._postprocess_outcome(outcome, original_command=command)

    def prepare_tool_dispatch(
        self,
        *,
        command: Any,
        include_reflect: bool = False,
    ):
        prepare_fn = getattr(self._ctx.command_executor, "prepare_tool_dispatch", None)
        if callable(prepare_fn):
            return prepare_fn(
                state=self._ctx.state,
                command=command,
                logger=self._ctx.logger,
                include_reflect=include_reflect,
            )
        from openminion.modules.brain.loop.tools.contracts import (
            PreparedToolDispatch,
        )  # noqa: PLC0415

        return PreparedToolDispatch(
            approved_command=command,
            original_command=command,
            command_id=str(getattr(command, "command_id", "") or new_uuid()),
            tool_name=str(getattr(command, "tool_name", "") or "").strip(),
            validated_args=dict(getattr(command, "args", {}) or {}),
            session_id=str(getattr(self._ctx.state, "session_id", "") or ""),
            trace_id=str(getattr(self._ctx.state, "trace_id", "") or ""),
            agent_id=str(getattr(self._ctx.state, "agent_id", "") or ""),
            lineage={},
            permission_mode=str(
                getattr(self._ctx.state, "permission_mode", "default") or "default"
            ),
            payload={},
        )

    def execute_prepared_tool_dispatch(
        self,
        *,
        prepared_dispatch,
    ):
        return _execute_prepared_tool_dispatch_from_context(
            self._ctx,
            prepared_dispatch=prepared_dispatch,
        )

    def finalize_tool_result(
        self,
        *,
        prepared_dispatch,
        raw_result,
    ):
        return _finalize_tool_result_from_context(
            self._ctx,
            prepared_dispatch=prepared_dispatch,
            raw_result=raw_result,
            postprocess_outcome=self._postprocess_outcome,
        )

    def finalize_prepare_outcome(
        self,
        *,
        prepare_outcome,
    ):
        from openminion.modules.brain.loop.tools.contracts import (
            CommandExecutionOutcome,
        )  # noqa: PLC0415

        outcome = CommandExecutionOutcome(
            approved_command=prepare_outcome.approved_command,
            action_result=prepare_outcome.action_result,
        )
        return self._postprocess_outcome(
            outcome,
            original_command=getattr(prepare_outcome, "original_command", None),
        )

    def _postprocess_outcome(
        self,
        outcome: Any,
        *,
        original_command: Any | None,
    ) -> Any:
        approved_command = getattr(outcome, "approved_command", original_command)
        action_result = getattr(outcome, "action_result", None)
        raw_error = getattr(action_result, "error", None)
        if isinstance(raw_error, dict):
            error_code = str(raw_error.get("code", "") or "")
        else:
            error_code = str(getattr(raw_error, "code", "") or "")
        if (
            action_result is not None
            and str(getattr(action_result, "status", "") or "").strip()
            == BRAIN_ACTION_STATUS_NEEDS_USER
            and error_code.strip().upper() == "CONFIRM_REQUIRED"
            and approved_command is not None
        ):
            self.state.pending_confirmation_command = approved_command.model_copy(
                deep=True
            )
            _store_pending_confirmation_metadata(self.state)
            self.state.post_action_user_message = confirmation_required_user_message(
                approved_command
            )
        if (
            action_result is not None
            and approved_command is not None
            and callable(self._on_command_result)
        ):
            self._on_command_result(approved_command, action_result)
        return outcome

    def emit_status(self, **kwargs) -> None:
        self._ctx.emit_status(**kwargs)

    def advance_after_action(
        self,
        *,
        action_result: ActionResult,
        force_replan: bool = False,
    ) -> None:
        self._ctx.command_executor.advance_after_action(
            state=self._ctx.state,
            action_result=action_result,
            force_replan=force_replan,
            logger=self._ctx.logger,
        )
