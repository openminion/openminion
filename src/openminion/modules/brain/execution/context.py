from typing import Any

from ..schemas import Decision, WorkingState
from ..tools.executor import RunnerCommandExecutor
from .loop_contracts import ExecutionContext
from .services import RunnerExecutionServices


def build_execution_context(
    runner: Any,
    *,
    state: WorkingState,
    decision: Decision,
    user_input: str | None,
    logger: Any,
    suppress_lifecycle_exit_statuses: bool = False,
) -> ExecutionContext:
    return ExecutionContext(
        state=state,
        decision=decision,
        user_input=user_input,
        logger=logger,
        options=runner.options,
        llm_adapter=runner.llm_api,
        command_executor=RunnerCommandExecutor(runner),
        _services=RunnerExecutionServices(
            runner,
            suppress_lifecycle_exit_statuses=suppress_lifecycle_exit_statuses,
        ),
    )


__all__ = ["RunnerExecutionServices", "build_execution_context"]
