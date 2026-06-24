from __future__ import annotations

from openminion.modules.brain.cli import _coerce_exit_code, _step_output_exit_code
from openminion.modules.brain.schemas import BudgetCounters, StepOutput, WorkingState


def _step_output(status: str) -> StepOutput:
    state = WorkingState(
        session_id="sess-cli",
        agent_id="agent-cli",
        status=status,
        budgets_remaining=BudgetCounters(
            ticks=1,
            tool_calls=1,
            a2a_calls=0,
            tokens=100,
            time_ms=1000,
        ),
        trace_id="trace-cli",
    )
    return StepOutput(
        session_id=state.session_id,
        status=status,
        message=status,
        working_state=state,
        action_result=None,
    )


def test_step_output_exit_code_maps_terminal_statuses() -> None:
    assert _step_output_exit_code(_step_output("done")) == 0
    assert _step_output_exit_code(_step_output("error")) == 2
    assert _step_output_exit_code(_step_output("stopped")) == 1


def test_coerce_exit_code_handles_generic_values() -> None:
    assert _coerce_exit_code(None) == 0
    assert _coerce_exit_code(7) == 7
    assert _coerce_exit_code("boom") == 1
