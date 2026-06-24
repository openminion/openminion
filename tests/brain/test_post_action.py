from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.execution.advance import advance_after_action
from openminion.modules.brain.execution.post_action import apply_post_action_judgment
from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    Plan,
    PostActionJudgment,
    ToolCommand,
    WorkingState,
)


def _state(*, status: str = "waiting_user") -> WorkingState:
    return WorkingState(
        session_id="s-post-action",
        agent_id="agent-post-action",
        trace_id="trace-post-action",
        status=status,
        budgets_remaining=BudgetCounters(
            ticks=4,
            tool_calls=4,
            a2a_calls=2,
            tokens=4000,
            time_ms=60000,
        ),
    )


def test_apply_post_action_judgment_consumes_waiting_user_before_advancing() -> None:
    state = _state()
    state.cursor = 0

    outcome = apply_post_action_judgment(
        state=state,
        judgment=PostActionJudgment(
            outcome="advance",
            reason="step completed",
        ),
        step_key="step-1",
        total_steps=3,
        max_retries_per_step=1,
        transition_to_replan=None,
    )

    assert outcome == "advance"
    assert state.status == "active"
    assert state.cursor == 1


def test_apply_post_action_judgment_can_reask_after_waiting_user_reply() -> None:
    state = _state()

    outcome = apply_post_action_judgment(
        state=state,
        judgment=PostActionJudgment(
            outcome="ask_user",
            reason="Need one more confirmation.",
        ),
        step_key="step-1",
        total_steps=2,
        max_retries_per_step=1,
        transition_to_replan=None,
    )

    assert outcome == "ask_user"
    assert state.status == "waiting_user"
    assert state.post_action_user_message == "Need one more confirmation."


def test_advance_after_action_auto_advances_successful_policy_replay_commands() -> None:
    first = ToolCommand(
        title="write-one",
        tool_name="file.write",
        args={"path": "demo/a.txt", "body": "a"},
        inputs={
            "path": "demo/a.txt",
            "body": "a",
            "confirmation_source": "policy_replay",
        },
        success_criteria={"status": "success"},
    )
    second = ToolCommand(
        title="write-two",
        tool_name="file.write",
        args={"path": "demo/b.txt", "body": "b"},
        inputs={"path": "demo/b.txt", "body": "b"},
        success_criteria={"status": "success"},
    )
    state = _state()
    state.status = "active"
    state.plan = Plan(
        objective="replay",
        steps=[first, second],
        stop_conditions=["all files written"],
        assumptions=[],
        risk_summary="replay",
        success_criteria={"status": "success"},
    )
    state.cursor = 0
    runner = SimpleNamespace(
        llm_api=None,
        context_api=None,
        options=SimpleNamespace(
            plan_consecutive_failure_limit=3,
            max_replans=1,
            max_retries_per_step=1,
            adaptive_replan_retained_step_outputs=0,
        ),
    )

    advance_after_action(
        runner,
        state=state,
        action_result=ActionResult(
            command_id=first.command_id,
            status="success",
            summary="first replayed write completed",
        ),
        logger=None,
    )

    assert state.status == "active"
    assert state.cursor == 1
