from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any


from openminion.modules.brain.loop.adaptive import ActLoopMode
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.execution.intent_state import (
    record_decision_metadata,
    update_intent_execution_states,
)
from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    ClosureJudgment,
    Plan,
    ToolCommand,
    WorkingState,
)
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.brain.constants import (
    BRAIN_EXECUTION_OUTCOME_SUCCEEDED,
    BRAIN_EXECUTION_OUTCOME_PENDING,
)


# Shared test infrastructure for seeded multi-command act-loop coverage


@dataclass
class _FakeCommandExecutor:
    services: Any

    def execute_command(
        self,
        *,
        state: WorkingState,
        command: Any,
        logger: Any,
        preapproved: bool = False,
        approve_only: bool = False,
        include_reflect: bool = True,
    ) -> CommandExecutionOutcome:
        approved = (
            command
            if preapproved
            else self.services.approve_command(
                state=state, command=command, logger=logger
            )
        )
        if approve_only:
            return CommandExecutionOutcome(approved_command=approved)
        action_result, job = self.services.act_command(
            state=state, command=approved, logger=logger
        )
        # plan-era observe/reflect services removed;
        # ``include_reflect`` is retained for interface compatibility only.
        del include_reflect
        return CommandExecutionOutcome(
            approved_command=approved,
            action_result=action_result,
            job=job,
        )

    def advance_after_action(
        self,
        *,
        state: WorkingState,
        action_result: ActionResult,
        force_replan: bool = False,
        logger: Any | None = None,
    ) -> None:
        del force_replan, logger
        state.last_result = action_result
        state.last_command_id = action_result.command_id
        state.cursor += 1
        if state.plan is not None and state.cursor >= len(state.plan.steps):
            state.status = "done"
        else:
            state.status = "active"


@dataclass
class _FakeServices:
    acted: list[str]
    closure_calls: int = 0

    def save_state(self, *, state: WorkingState) -> None:
        del state

    def emit_phase_status(self, *, state: WorkingState, **kwargs) -> None:
        del state, kwargs

    def respond_with_meta(
        self,
        *,
        state: WorkingState,
        logger: Any,
        message: str,
        status: str,
        action_result: ActionResult | None = None,
    ) -> Any:
        del logger
        state.status = status
        return SimpleNamespace(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
        )

    def direct_response(self, *, user_input: str, decision: Any = None) -> str:
        del user_input, decision
        return ""

    def plan(
        self, *, state: Any, user_input: str, logger: Any, decision: Any = None
    ) -> Any:
        del state, logger, decision, user_input
        raise AssertionError("the shared act loop should not call ctx.plan()")

    def approve_command(self, *, state: Any, command: Any, logger: Any) -> Any:
        del state, logger
        return command

    def act_command(
        self, *, state: Any, command: Any, logger: Any
    ) -> tuple[ActionResult, Any]:
        del state, logger
        self.acted.append(str(getattr(command, "title", "") or ""))
        return (
            ActionResult(
                command_id=str(getattr(command, "command_id", "cmd") or "cmd"),
                status="success",
                summary=f"Executed {getattr(command, 'title', '')}",
            ),
            None,
        )

    def assess_plan_feasibility(
        self, *, state: Any, user_input: str, logger: Any
    ) -> None:
        del state, user_input, logger
        return None

    def evaluate_meta(self, **kwargs: Any) -> None:
        del kwargs
        return None

    def apply_meta_directive(self, **kwargs: Any) -> None:
        del kwargs

    def meta_override_response(self, **kwargs: Any) -> None:
        del kwargs
        return None

    def meta_tool_restriction_reason(self, *, command: Any, directive: Any) -> None:
        del command, directive
        return None

    def command_has_side_effects(self, *, command: Any) -> bool:
        del command
        return False

    def resolve_verification_mode(self, *, current: Any, candidate: Any) -> Any:
        return candidate if candidate is not None else current

    def verify(
        self, *, state: Any, command: Any, action_result: Any, mode: Any, logger: Any
    ) -> bool:
        del state, command, action_result, mode, logger
        return True

    def improve(self, *, state: Any, report: Any, logger: Any) -> None:
        del state, report, logger

    def compact(self, *, state: Any, logger: Any, content: str = "") -> None:
        del state, logger, content

    def evaluate_turn_closure(self, **kwargs: Any) -> ClosureJudgment:
        self.closure_calls += 1
        del kwargs
        return ClosureJudgment(satisfied=True, reason="done", next_action="close")

    def apply_closure_judgment(self, *, state: Any, judgment: Any) -> str:
        del judgment
        state.status = "done"
        return "close"

    def extract_success_memories(
        self,
        *,
        state: Any,
        action_result: Any,
        judgment: Any,
        logger: Any,
        outcome_snapshot: Any = None,
    ) -> list[str]:
        del state, action_result, judgment, logger, outcome_snapshot
        return []


def _state(session_id: str = "s-test") -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="test-agent",
        goal="test",
        budgets_remaining=BudgetCounters(
            ticks=20,
            tool_calls=10,
            a2a_calls=2,
            tokens=5000,
            time_ms=120000,
        ),
        trace_id=f"trace-{session_id}",
    )


def _logger() -> Any:
    events: list[dict[str, Any]] = []
    return SimpleNamespace(
        events=events,
        emit=lambda event_type, payload, **kwargs: events.append(
            {"type": event_type, "payload": payload, "kwargs": kwargs}
        ),
    )


def _context(
    commands: list[ToolCommand],
    *,
    sub_intents: list[dict[str, str]] | None = None,
    session_id: str = "s-test",
) -> tuple[ExecutionContext, _FakeServices]:
    state = _state(session_id=session_id)
    services = _FakeServices(acted=[])
    logger = _logger()
    decision = SimpleNamespace(
        mode="act",
        confidence=0.9,
        reason_code="multi_tool_lookup",
        act_profile="general",
        execution_target=SimpleNamespace(kind="local"),
        sub_intents=list(sub_intents or []),
        rationale="",
        question=None,
        answer=None,
        _seeded_commands=list(commands),
    )
    ctx = ExecutionContext(
        state=state,
        decision=decision,
        user_input="test multi-tool request",
        logger=logger,
        options=SimpleNamespace(
            plan_max_iterations=8,
            max_replans=2,
            plan_checkpoint_interval=0,
        ),
        llm_adapter=None,
        command_executor=_FakeCommandExecutor(services=services),
        _services=services,
    )
    return ctx, services


def _series_mode(ctx: ExecutionContext) -> ActLoopMode:
    return ActLoopMode()


# Repeated same-tool calls are first-class


def test_weather_two_cities_executes_both_lanes() -> None:
    commands = [
        ToolCommand(
            title="Weather New York",
            tool_name="weather",
            args={"location": "New York"},
            sub_intent_ids=["weather_new_york"],
            success_criteria={"status": "success"},
        ),
        ToolCommand(
            title="Weather London",
            tool_name="weather",
            args={"location": "London"},
            sub_intent_ids=["weather_london"],
            success_criteria={"status": "success"},
        ),
    ]
    ctx, services = _context(
        commands,
        sub_intents=[
            {"id": "weather_new_york", "description": "weather new york"},
            {"id": "weather_london", "description": "weather london"},
        ],
    )
    mode = _series_mode(ctx)

    # Disable compiled-workflow dispatch for this test (no registered workflow)
    ctx.state.active_workflow_name = None
    result = mode.execute(ctx)

    # Both weather calls should have executed
    assert services.acted == ["Weather New York", "Weather London"], (
        f"Expected both cities to be acted on, got: {services.acted}"
    )
    assert result.status in {"active", "done"}


def test_search_and_time_executes_both_different_tools() -> None:
    commands = [
        ToolCommand(
            title="Search OpenAI news",
            tool_name="web.search",
            args={"query": "latest OpenAI news"},
            sub_intent_ids=["search_news"],
            success_criteria={"status": "success"},
        ),
        ToolCommand(
            title="Get UTC time",
            tool_name="time",
            args={},
            sub_intent_ids=["get_time"],
            success_criteria={"status": "success"},
        ),
    ]
    ctx, services = _context(
        commands,
        sub_intents=[
            {"id": "search_news", "description": "search news"},
            {"id": "get_time", "description": "get time"},
        ],
    )
    mode = _series_mode(ctx)
    result = mode.execute(ctx)

    assert services.acted == ["Search OpenAI news", "Get UTC time"], (
        f"Expected both tools to execute, got: {services.acted}"
    )
    assert result.status in {"active", "done"}


def test_three_tool_calls_same_tool_all_execute() -> None:
    commands = [
        ToolCommand(
            title=f"Weather city {i}",
            tool_name="weather",
            args={"location": f"City {i}"},
            sub_intent_ids=[f"weather_{i}"],
            success_criteria={"status": "success"},
        )
        for i in range(3)
    ]
    ctx, services = _context(
        commands,
        sub_intents=[
            {"id": f"weather_{i}", "description": f"weather city {i}"} for i in range(3)
        ],
    )
    mode = _series_mode(ctx)
    result = mode.execute(ctx)

    assert len(services.acted) == 3
    assert result.status in {"active", "done"}


# Intent execution state tracks subgoal progress


def test_intent_execution_states_track_each_subgoal() -> None:
    state = _state()
    decision = SimpleNamespace(
        mode="plan",
        sub_intents=[
            {"id": "weather_new_york", "description": "weather new york"},
            {"id": "weather_london", "description": "weather london"},
        ],
        rationale="",
        reason_code="weather_two_cities",
    )
    plan = Plan(
        objective="Get weather for two cities",
        steps=[
            ToolCommand(
                title="Weather NY",
                tool_name="weather",
                args={"location": "New York"},
                sub_intent_ids=["weather_new_york"],
                success_criteria={"status": "success"},
            ),
            ToolCommand(
                title="Weather London",
                tool_name="weather",
                args={"location": "London"},
                sub_intent_ids=["weather_london"],
                success_criteria={"status": "success"},
            ),
        ],
        stop_conditions=["all cities fetched"],
        assumptions=[],
        risk_summary="low",
        success_criteria={"status": "success"},
    )
    record_decision_metadata(state=state, decision=decision, plan=plan)

    # Should have two tracked sub-intents
    assert len(state.intent_execution_states) == 2
    ids = {item.intent_id for item in state.intent_execution_states}
    assert ids == {"weather_new_york", "weather_london"}


def test_update_intent_execution_states_marks_completed_subgoal() -> None:
    state = _state()
    decision = SimpleNamespace(
        mode="plan",
        sub_intents=[
            {"id": "weather_new_york", "description": "weather new york"},
            {"id": "weather_london", "description": "weather london"},
        ],
        rationale="",
        reason_code="weather_two_cities",
    )
    command_ny = ToolCommand(
        title="Weather NY",
        tool_name="weather",
        args={"location": "New York"},
        sub_intent_ids=["weather_new_york"],
        success_criteria={"status": "success"},
    )
    command_ldn = ToolCommand(
        title="Weather London",
        tool_name="weather",
        args={"location": "London"},
        sub_intent_ids=["weather_london"],
        success_criteria={"status": "success"},
    )
    plan = Plan(
        objective="Get weather",
        steps=[command_ny, command_ldn],
        stop_conditions=[],
        assumptions=[],
        risk_summary="low",
        success_criteria={},
    )
    state.plan = plan
    record_decision_metadata(state=state, decision=decision, plan=plan)

    # Simulate completing the first step
    action_result = ActionResult(
        command_id=str(command_ny.command_id or "cmd-ny"),
        status="success",
        summary="New York weather: sunny 25C",
    )
    runner_stub = SimpleNamespace(options=SimpleNamespace(failure_strategy="replan"))
    update_intent_execution_states(
        runner_stub,
        state=state,
        command=command_ny,
        action_result=action_result,
        current_step_index=0,
    )

    ny_state = next(
        (
            item
            for item in state.intent_execution_states
            if item.intent_id == "weather_new_york"
        ),
        None,
    )
    ldn_state = next(
        (
            item
            for item in state.intent_execution_states
            if item.intent_id == "weather_london"
        ),
        None,
    )

    assert ny_state is not None
    assert ny_state.status == BRAIN_EXECUTION_OUTCOME_SUCCEEDED
    # London intent is still pending
    assert ldn_state is not None
    assert ldn_state.status == BRAIN_EXECUTION_OUTCOME_PENDING


def test_repeated_tool_second_call_is_tracked_separately() -> None:
    state = _state()
    decision = SimpleNamespace(
        mode="act",
        sub_intents=[
            {"id": "weather_new_york", "description": "weather new york"},
            {"id": "weather_london", "description": "weather london"},
        ],
        rationale="",
        reason_code="weather_two_cities",
    )
    plan = Plan(
        objective="Get weather for two cities",
        steps=[
            ToolCommand(
                title="Weather NY",
                tool_name="weather",
                args={"location": "New York"},
                sub_intent_ids=["weather_new_york"],
                success_criteria={"status": "success"},
            ),
            ToolCommand(
                title="Weather London",
                tool_name="weather",
                args={"location": "London"},
                sub_intent_ids=["weather_london"],
                success_criteria={"status": "success"},
            ),
        ],
        stop_conditions=[],
        assumptions=[],
        risk_summary="low",
        success_criteria={},
    )
    record_decision_metadata(state=state, decision=decision, plan=plan)

    # Assert: both sub-intents are tracked — neither is silently dropped
    ids = [item.intent_id for item in state.intent_execution_states]
    assert "weather_new_york" in ids, (
        "weather_new_york sub-intent must be tracked in intent_execution_states"
    )
    assert "weather_london" in ids, (
        "weather_london sub-intent must be tracked in intent_execution_states"
    )
    # They should be separate entries, not collapsed
    assert ids.count("weather_new_york") == 1
    assert ids.count("weather_london") == 1
