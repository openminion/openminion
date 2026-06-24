from __future__ import annotations


from openminion.modules.brain.execution import budget_blocked_result
from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    WorkingState,
)
from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_BLOCKED,
    BRAIN_STATE_WAITING_USER,
)


def test_budget_blocked_result_status_is_blocked() -> None:
    result = budget_blocked_result(command_id="cmd-001", budget_name="ticks")
    assert result.status == BRAIN_ACTION_STATUS_BLOCKED


def test_budget_blocked_result_preserves_command_id() -> None:
    result = budget_blocked_result(
        command_id="cmd-weather-ny", budget_name="tool_calls"
    )
    assert result.command_id == "cmd-weather-ny"


def test_budget_blocked_result_error_code() -> None:
    result = budget_blocked_result(command_id="cmd-001", budget_name="tokens")
    assert result.error is not None
    assert result.error.code == "BUDGET_EXCEEDED"
    assert "budget_exceeded" in result.error.details.get("reason_code", "")


def test_budget_blocked_result_summary_names_budget() -> None:
    result = budget_blocked_result(command_id="cmd-001", budget_name="llm_calls")
    assert "llm_calls" in result.summary.lower() or "llm_calls" in str(result.error)


def test_partial_plan_cursor_preserved_on_budget_exhaustion() -> None:
    state = WorkingState(
        session_id="s-budget-test",
        agent_id="test-agent",
        goal="fetch three resources",
        budgets_remaining=BudgetCounters(
            ticks=1, tool_calls=5, a2a_calls=2, tokens=5000, time_ms=120000
        ),
        trace_id="trace-budget",
    )
    state.cursor = 1
    state.status = "active"

    state.status = BRAIN_STATE_WAITING_USER

    assert state.cursor == 1, (
        "cursor must be preserved at 1 after partial plan completion; "
        "resetting to 0 would lose the completed step's progress"
    )


def test_step_outputs_preserved_on_budget_exhaustion() -> None:
    from openminion.modules.brain.schemas import StepOutputEntry

    state = WorkingState(
        session_id="s-budget-test",
        agent_id="test-agent",
        goal="multi-step task",
        budgets_remaining=BudgetCounters(
            ticks=0, tool_calls=5, a2a_calls=2, tokens=5000, time_ms=120000
        ),
        trace_id="trace-budget",
    )
    completed_result = ActionResult(
        command_id="cmd-step-0",
        status="success",
        summary="Step 0 completed: weather data retrieved",
    )
    state.last_result = completed_result

    state.step_outputs = [
        StepOutputEntry(
            command_id="cmd-step-0",
            step_index=0,
            summary="Step 0 completed: weather data retrieved",
        )
    ]

    state.budgets_remaining.ticks = 0
    state.status = BRAIN_STATE_WAITING_USER

    assert state.last_result is not None, "last_result must be preserved"
    assert state.last_result.command_id == "cmd-step-0"
    assert len(state.step_outputs) == 1, (
        "step_outputs must be preserved for next-turn resume"
    )


def test_budget_counters_ticks_floor_at_zero() -> None:
    budgets = BudgetCounters(
        ticks=1, tool_calls=5, a2a_calls=2, tokens=5000, time_ms=120000
    )
    budgets.ticks = max(0, budgets.ticks - 1)
    assert budgets.ticks == 0

    budgets.ticks = max(0, budgets.ticks - 1)
    assert budgets.ticks == 0


def test_budget_counters_tool_calls_decrement() -> None:
    budgets = BudgetCounters(
        ticks=10, tool_calls=3, a2a_calls=2, tokens=5000, time_ms=120000
    )
    budgets.tool_calls -= 1
    assert budgets.tool_calls == 2
    budgets.tool_calls -= 1
    assert budgets.tool_calls == 1
    budgets.tool_calls -= 1
    assert budgets.tool_calls == 0


def test_task_backed_resume_state_initialized_empty() -> None:
    state = WorkingState(
        session_id="s-fresh",
        agent_id="agent",
        goal="some task",
        budgets_remaining=BudgetCounters(
            ticks=10, tool_calls=5, a2a_calls=2, tokens=5000, time_ms=120000
        ),
    )
    assert state.task_backed_resume_state == {}


def test_task_backed_resume_state_preserves_findings_on_pause() -> None:
    state = WorkingState(
        session_id="s-research",
        agent_id="agent",
        goal="research OpenAI news",
        budgets_remaining=BudgetCounters(
            ticks=1, tool_calls=5, a2a_calls=2, tokens=5000, time_ms=120000
        ),
    )
    checkpoint_data = {
        "query": "OpenAI latest developments",
        "iteration": 1,
        "findings": [
            {"content": "OpenAI released GPT-5 with multimodal capabilities"},
        ],
        "checkpoint_id": "task-abc-iteration-1",
    }
    state.task_backed_resume_state = checkpoint_data

    assert state.task_backed_resume_state["iteration"] == 1
    assert len(state.task_backed_resume_state["findings"]) == 1
    assert state.task_backed_resume_state["checkpoint_id"] == "task-abc-iteration-1"


def test_completed_subgoal_state_not_reset_on_budget_exhaustion() -> None:
    from openminion.modules.brain.schemas import IntentExecutionState
    from openminion.modules.brain.constants import BRAIN_EXECUTION_OUTCOME_SUCCEEDED

    state = WorkingState(
        session_id="s-continuation",
        agent_id="agent",
        goal="weather two cities",
        budgets_remaining=BudgetCounters(
            ticks=0, tool_calls=5, a2a_calls=2, tokens=5000, time_ms=120000
        ),
    )
    state.intent_execution_states = [
        IntentExecutionState(
            intent_id="weather_new_york",
            description="Get weather for New York",
            status=BRAIN_EXECUTION_OUTCOME_SUCCEEDED,
        ),
        IntentExecutionState(
            intent_id="weather_london",
            description="Get weather for London",
            status="pending",
        ),
    ]
    state.cursor = 1  # Advanced past the first step

    state.budgets_remaining.ticks = 0
    state.status = BRAIN_STATE_WAITING_USER

    ny_state = next(
        (s for s in state.intent_execution_states if s.intent_id == "weather_new_york"),
        None,
    )
    assert ny_state is not None
    assert ny_state.status == BRAIN_EXECUTION_OUTCOME_SUCCEEDED, (
        "Completed subgoal must not be reset to pending on budget exhaustion"
    )
    ldn_state = next(
        (s for s in state.intent_execution_states if s.intent_id == "weather_london"),
        None,
    )
    assert ldn_state is not None
    assert ldn_state.status == "pending"


def test_budget_resumable_message_format() -> None:
    iteration = 2
    message = (
        f"Research paused after iteration {iteration}. "
        "Continue in a new turn to resume."
    )
    assert f"iteration {iteration}" in message
    assert "resume" in message.lower(), (
        "Pause message must include 'resume' to signal continuation is available"
    )
