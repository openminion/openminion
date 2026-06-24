from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

from openminion.modules.brain.runner.lifecycle import run_until_idle
from openminion.modules.brain.schemas import BudgetCounters, StepOutput, WorkingState


def _step_output(*, status: str, ticks: int = 3) -> StepOutput:
    state = WorkingState(
        session_id="s-continue",
        agent_id="router-agent",
        status=status,
        budgets_remaining=BudgetCounters(
            ticks=ticks,
            tool_calls=5,
            a2a_calls=0,
            tokens=5000,
            time_ms=120000,
        ),
        trace_id="trace-continue",
    )
    return StepOutput(
        session_id=state.session_id,
        status=status,
        message=status,
        working_state=state,
        action_result=None,
    )


@dataclass
class _FakeRunner:
    outputs: list[StepOutput]
    options: SimpleNamespace
    profile: SimpleNamespace
    session_api: MagicMock
    _index: int = 0

    def step(self, **kwargs) -> StepOutput:
        del kwargs
        output = self.outputs[self._index]
        self._index += 1
        return output

    def _respond_with_meta(self, *, state, logger, message, status, action_result):
        del logger
        state.status = status
        return StepOutput(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
        )


def test_run_until_idle_re_dispatches_continue_status() -> None:
    runner = _FakeRunner(
        outputs=[
            _step_output(status="continue", ticks=3),
            _step_output(status="done", ticks=2),
        ],
        options=SimpleNamespace(plan_max_iterations=4),
        profile=SimpleNamespace(agent_id="router-agent"),
        session_api=MagicMock(),
    )

    result = run_until_idle(
        runner,
        session_id="s-continue",
        user_input="start coding",
        trace_id="trace-continue",
        forced_tools=None,
        capability_category=None,
    )

    assert result.status == "done"
    assert runner._index == 2


def test_run_until_idle_budget_checks_continue_tick() -> None:
    runner = _FakeRunner(
        outputs=[_step_output(status="continue", ticks=0)],
        options=SimpleNamespace(plan_max_iterations=4),
        profile=SimpleNamespace(agent_id="router-agent"),
        session_api=MagicMock(),
    )

    result = run_until_idle(
        runner,
        session_id="s-continue",
        user_input="start coding",
        trace_id="trace-continue",
        forced_tools=None,
        capability_category=None,
    )

    assert result.status == "waiting_user"
    assert "tick budget is exhausted" in str(result.message or "").lower()
