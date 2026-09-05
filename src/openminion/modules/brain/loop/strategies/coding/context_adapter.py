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
from openminion.modules.brain.loop.services import runner_from_context
from openminion.modules.brain.loop.providers.retry import build_provider_retry_policy
from openminion.modules.brain.runner.tick.context import (
    _store_pending_confirmation_metadata,
)
from openminion.modules.brain.schemas import ActionResult, ToolCommand
from openminion.modules.tool.contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED


_VERIFICATION_TARGET_KIND_ARG = "verification_target_kind"
_VERIFICATION_TARGET_ID_ARG = "verification_target_id"


def _bind_verification_target(command: Any) -> Any:
    if not isinstance(command, ToolCommand):
        return command
    args = dict(command.args)
    inputs = dict(command.inputs)
    target_kind = str(
        args.pop(
            _VERIFICATION_TARGET_KIND_ARG,
            inputs.pop(
                _VERIFICATION_TARGET_KIND_ARG,
                command.verification_target_kind or "",
            ),
        )
        or ""
    ).strip()
    target_id = str(
        args.pop(
            _VERIFICATION_TARGET_ID_ARG,
            inputs.pop(
                _VERIFICATION_TARGET_ID_ARG,
                command.verification_target_id or "",
            ),
        )
        or ""
    ).strip()
    if target_kind not in {"criterion", "deliverable"} or not target_id:
        target_kind = ""
        target_id = ""
    return command.model_copy(
        update={
            "args": args,
            "inputs": inputs,
            "verification_target_kind": target_kind or None,
            "verification_target_id": target_id or None,
        },
        deep=True,
    )


class _CodingLoopContextAdapter:
    def __init__(
        self,
        ctx: ExecutionContext,
        *,
        on_command_result: Callable[[Any, ActionResult], None] | None = None,
    ) -> None:
        self.state = ctx.state
        self._ctx = ctx
        runner = runner_from_context(ctx)
        self.session_api = getattr(runner, "session_api", None)
        self.provider_retry_max_attempts = build_provider_retry_policy(
            getattr(runner, "options", None)
        ).max_attempts
        self.prepared_parallel_dispatch_supported = all(
            callable(getattr(ctx.command_executor, name, None))
            for name in (
                "prepare_tool_dispatch",
                "execute_prepared_tool_dispatch",
                "finalize_tool_result",
            )
        )
        self._on_command_result = on_command_result

    def execute_command(
        self,
        *,
        command: Any,
        include_reflect: bool = False,
    ):
        command = _bind_verification_target(command)
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
        command = _bind_verification_target(command)
        return self._ctx.command_executor.prepare_tool_dispatch(
            state=self._ctx.state,
            command=command,
            logger=self._ctx.logger,
            include_reflect=include_reflect,
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
            tool_budget_debited=prepare_outcome.tool_budget_debited,
            policy_approval_id=prepare_outcome.policy_approval_id,
            policy_confirmation_preview=(prepare_outcome.policy_confirmation_preview),
        )
        self.state.pending_policy_approval_id = prepare_outcome.policy_approval_id
        self.state.pending_policy_confirmation_preview = (
            prepare_outcome.policy_confirmation_preview
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
        approved_command = outcome.approved_command or original_command
        action_result = outcome.action_result
        error_code = action_result.error.code if action_result.error is not None else ""
        if (
            action_result is not None
            and str(getattr(action_result, "status", "") or "").strip()
            == BRAIN_ACTION_STATUS_NEEDS_USER
            and error_code.strip().upper() == TOOL_ERROR_CONFIRM_REQUIRED
            and approved_command is not None
        ):
            self.state.pending_confirmation_command = approved_command.model_copy(
                deep=True
            )
            _store_pending_confirmation_metadata(self.state)
            self.state.post_action_user_message = confirmation_required_user_message(
                approved_command,
                self.state.pending_policy_confirmation_preview,
            )
        if (
            action_result is not None
            and approved_command is not None
            and self._on_command_result is not None
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
