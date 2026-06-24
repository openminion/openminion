from __future__ import annotations

from unittest.mock import MagicMock

from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    ToolCommand,
    WorkingState,
)
from openminion.modules.brain.tools.executor import RunnerCommandExecutor


def _state() -> WorkingState:
    return WorkingState(
        session_id="s-command-executor",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=5,
            a2a_calls=5,
            tokens=1000,
            time_ms=10_000,
        ),
    )


def test_runner_command_executor_executes_approve_act_in_order() -> None:
    # Plan-phase observe and reflect were removed with the plan mode deletion (PTO).
    # The executor now runs: approve → act only.
    state = _state()
    logger = MagicMock()
    command = ToolCommand(
        title="weather",
        tool_name="weather",
        args={"location": "San Francisco"},
        success_criteria={"status": "success"},
        idempotency_key="weather-1",
    )

    call_order: list[str] = []
    approved = command.model_copy(deep=True)
    action_result = ActionResult(
        command_id=command.command_id,
        status="success",
        summary="ok",
    )

    runner = MagicMock()
    runner._approve.side_effect = lambda **kwargs: (
        call_order.append("approve") or approved
    )
    runner._act.side_effect = lambda **kwargs: (
        call_order.append("act") or (action_result, None)
    )

    outcome = RunnerCommandExecutor(runner).execute_command(
        state=state,
        command=command,
        logger=logger,
    )

    assert call_order == ["approve", "act"]
    assert outcome.approved_command == approved
    assert outcome.action_result == action_result
    assert outcome.job is None
    assert outcome.reflect_report is None


def test_runner_command_executor_advance_after_action_delegates() -> None:
    state = _state()
    action_result = ActionResult(
        command_id="cmd-1",
        status="success",
        summary="ok",
    )
    runner = MagicMock()

    RunnerCommandExecutor(runner).advance_after_action(
        state=state,
        action_result=action_result,
        force_replan=True,
        logger=None,
    )

    runner._advance_after_action.assert_called_once_with(
        state=state,
        action_result=action_result,
        force_replan=True,
        logger=None,
    )
