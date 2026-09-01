from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import MagicMock

from openminion.modules.brain.runner.lifecycle import run_until_idle
from openminion.modules.brain.runner.tick.orchestrator import (
    _refresh_budget_for_new_trigger,
)
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
    llm_api: MagicMock
    _index: int = 0
    step_calls: list[dict[str, object]] = field(default_factory=list)

    def step(self, **kwargs) -> StepOutput:
        self.step_calls.append(dict(kwargs))
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
        llm_api=MagicMock(),
    )

    capture_identity = object()
    result = run_until_idle(
        runner,
        session_id="s-continue",
        user_input="start coding",
        trace_id="trace-continue",
        forced_tools=None,
        capability_category=None,
        capture_identity=capture_identity,
    )

    assert result.status == "done"
    assert runner._index == 2
    assert [call["capture_identity"] for call in runner.step_calls] == [
        capture_identity,
        capture_identity,
    ]


def test_run_until_idle_budget_checks_continue_tick() -> None:
    runner = _FakeRunner(
        outputs=[_step_output(status="continue", ticks=0)],
        options=SimpleNamespace(plan_max_iterations=4),
        profile=SimpleNamespace(agent_id="router-agent"),
        session_api=MagicMock(),
        llm_api=MagicMock(),
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


def test_plan_continuation_refreshes_per_turn_budget_without_resetting_plan() -> None:
    plan = {"plan_id": "plan-1", "steps": [{"step_id": "step-1"}]}
    state = _step_output(status="waiting_user", ticks=0).working_state
    state.plan = plan
    state.goal = "finish all three steps"
    state.llm_calls_used = 16
    state.llm_calls_max = 16
    state.budgets_remaining.tool_calls = 0
    state.budgets_remaining.tokens = 0
    state.budgets_remaining.time_ms = 0
    runner = SimpleNamespace(
        profile=SimpleNamespace(
            budgets=SimpleNamespace(
                max_ticks_per_user_turn=20,
                max_tool_calls=12,
                max_a2a_calls=4,
                max_total_llm_tokens=50000,
                max_elapsed_ms=480000,
            )
        )
    )

    _refresh_budget_for_new_trigger(runner, state, "plan_continuation")

    assert state.goal == "finish all three steps"
    assert state.plan == plan
    assert state.llm_calls_used == 0
    assert state.llm_calls_max == 20
    assert state.budgets_remaining == BudgetCounters(
        ticks=20,
        tool_calls=12,
        a2a_calls=4,
        tokens=50000,
        time_ms=480000,
    )
