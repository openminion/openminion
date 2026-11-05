"""Request-boundary guard for the current task's remaining token budget."""

from types import SimpleNamespace
from typing import Any

from .task_context import TaskContextBudgetError, message_token_estimate


def task_request_output_limit(
    state: Any,
    adapter: Any,
    *,
    messages: list[Any],
    tools: list[Any],
    model: str,
    requested_output: int | None,
) -> int | None:
    if state is None:
        return requested_output
    estimated = message_token_estimate(
        SimpleNamespace(llm_api=adapter), messages, model=model, tools=tools
    )
    available = int(state.budgets_remaining.tokens) - estimated
    if available <= 0:
        raise TaskContextBudgetError(
            "The task, messages and tool schemas exceed the remaining token budget. "
            "Split the task or increase its budget before continuing."
        )
    return min(requested_output if requested_output is not None else 2000, available)


def task_budget_execution_result(state: Any, error: Exception) -> Any:
    from openminion.modules.brain.execution.loop_contracts import ExecutionResult

    return ExecutionResult(status="waiting_user", working_state=state, message=str(error))


def task_budget_loop_outcome(owner: Any, error: Exception) -> Any:
    from openminion.modules.brain.loop.tools.contracts import (
        ADAPTIVE_TERM_BUDGET_EXHAUSTED,
        AdaptiveToolLoopOutcome,
    )

    owner.loop_state.termination_reason = ADAPTIVE_TERM_BUDGET_EXHAUSTED
    return AdaptiveToolLoopOutcome(
        profile_name=owner.profile.profile_name,
        mode_name=owner.profile.mode_name,
        termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
        state=owner.loop_state,
        allowed_tools=owner.allowed_tools,
        error_message=str(error),
        mode_result=task_budget_execution_result(owner.loop_ctx.state, error),
    )
