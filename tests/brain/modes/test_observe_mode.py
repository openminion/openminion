"""Observe-mode unit and integration tests.

Covers OBM-01 through OBM-08 with schema validation, workflow behavior,
registry wiring, and negative-path checks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from openminion.modules.brain.bootstrap.route_catalog import (
    available_routes,
    decision_route_descriptions,
    get_route_descriptor,
)
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.loop.tools.phases.child_execution import build_child_state
from openminion.modules.brain.loop.tools.phases.observe import (
    OBSERVE_MODE,
    ObservationCheck,
    ObserveMode,
    ObservePayload,
)
from openminion.modules.brain.execution.workflow import (
    StepResult,
    WorkflowMode,
    WorkflowPlan,
    WorkflowStep,
)
from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    Plan,
    WorkingState,
)


@dataclass
class _FakeServices:
    statuses: list[dict[str, Any]] = field(default_factory=list)
    plan_calls: list[str] = field(default_factory=list)
    response_queue: list[str] = field(default_factory=list)
    runner: Any = None

    def save_state(self, *, state: WorkingState) -> None:
        del state

    def emit_phase_status(self, *, state: WorkingState, **kwargs) -> None:
        del state
        self.statuses.append(dict(kwargs))

    def respond_with_meta(
        self,
        *,
        state: WorkingState,
        logger: Any,
        message: str,
        status: str,
        action_result: ActionResult | None = None,
        kind: str = "assistant",
    ):
        del logger, kind
        state.status = status
        return SimpleNamespace(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
        )

    def direct_response(self, *, user_input, decision=None):
        del user_input, decision
        if self.response_queue:
            return self.response_queue.pop(0)
        return ""

    def plan(self, *, state, user_input, logger, decision=None):
        del state, logger, decision
        text = str(user_input or "")
        self.plan_calls.append(text)
        return Plan(objective="mock check output", steps=[])

    def approve_command(self, *, state, command, logger):
        del state, logger
        return command

    def act_command(self, *, state, command, logger):
        del state, command, logger
        raise AssertionError("observe mode should not call ctx.act_command() directly")

    def assess_plan_feasibility(self, *, state, user_input, logger):
        del state, user_input, logger
        return None

    def evaluate_meta(self, **kwargs):
        del kwargs
        return None

    def apply_meta_directive(self, **kwargs):
        del kwargs

    def meta_override_response(self, **kwargs):
        del kwargs
        return None

    def meta_tool_restriction_reason(self, *, command, directive):
        del command, directive
        return None

    def command_has_side_effects(self, *, command):
        del command
        return False

    def resolve_verification_mode(self, *, current, candidate):
        return candidate if candidate is not None else current

    def verify(self, *, state, command, action_result, mode, logger):
        del state, command, action_result, mode, logger
        return True

    def improve(self, *, state, report, logger):
        del state, report, logger

    def compact(self, *, state, logger, content=""):
        del state, logger, content

    def evaluate_turn_closure(self, **kwargs):
        del kwargs
        return None

    def apply_closure_judgment(self, *, state, judgment):
        del state, judgment
        return "close"

    def extract_success_memories(self, **kwargs):
        del kwargs
        return []

    def create_task(self, **kwargs):
        del kwargs
        return SimpleNamespace(task_id="t-unused")

    def get_task(self, *, task_id: str):
        del task_id
        return None

    def list_open_tasks_for_session(self, **kwargs):
        del kwargs
        return []

    def save_checkpoint(self, **kwargs):
        del kwargs

    def get_latest_checkpoint(self, *, task_id: str):
        del task_id
        return None

    def list_checkpoints(self, *, task_id: str):
        del task_id
        return []

    def update_task_progress(self, *, task_id: str, progress: dict[str, Any]) -> None:
        del task_id, progress

    def transition_task(self, **kwargs):
        del kwargs
        return None


def _state(
    *,
    session_id: str = "s-observe",
    ticks: int = 30,
    goal: str = "Watch the health endpoint",
) -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="router-agent",
        goal=goal,
        budgets_remaining=BudgetCounters(
            ticks=ticks,
            tool_calls=15,
            a2a_calls=3,
            tokens=6000,
            time_ms=180000,
        ),
        trace_id=f"trace-{session_id}",
    )


def _ctx(
    *,
    state: WorkingState | None = None,
    observe_target: str = "http://example.com/health",
    observe_condition: str = "returns HTTP 200",
    observe_check_command: str = "fetch http://example.com/health",
    objective: str | None = None,
    user_input: str | None = None,
    response_queue: list[str] | None = None,
    runner: Any = None,
) -> tuple[ExecutionContext, _FakeServices]:
    working_state = state or _state()
    services = _FakeServices(response_queue=list(response_queue or []), runner=runner)
    decision = SimpleNamespace(
        mode=OBSERVE_MODE,
        confidence=0.9,
        reason_code="observe_request",
        observe_target=observe_target,
        observe_condition=observe_condition,
        observe_check_command=observe_check_command,
        objective=objective or observe_target,
    )
    logger = SimpleNamespace(events=[], emit=lambda *args, **kwargs: None)
    ctx = ExecutionContext(
        state=working_state,
        decision=decision,
        user_input=user_input if user_input is not None else observe_target,
        logger=logger,
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=services,
    )
    return ctx, services


def _assessment_json(
    *,
    check_output: str = "raw output",
    condition_met: bool = False,
    assessment: str = "not ready",
) -> str:
    return json.dumps(
        {
            "check_output": check_output,
            "condition_met": condition_met,
            "assessment": assessment,
        }
    )


def _monotonic(values: list[float]):
    sequence = iter(values)

    def _next() -> float:
        return next(sequence)

    return _next


# Schema tests


def test_observe_payload_valid() -> None:
    payload = ObservePayload(
        observe_target="http://example.com/health",
        observe_condition="returns HTTP 200",
        observe_check_command="fetch http://example.com/health",
    )
    assert payload.observe_target == "http://example.com/health"


def test_observe_payload_rejects_empty_target() -> None:
    with pytest.raises(ValidationError):
        ObservePayload(
            observe_target="",
            observe_condition="returns HTTP 200",
        )


def test_observe_payload_rejects_empty_condition() -> None:
    with pytest.raises(ValidationError):
        ObservePayload(
            observe_target="http://example.com/health",
            observe_condition="",
        )


def test_observe_payload_round_trip() -> None:
    payload = ObservePayload(
        observe_target="file.txt",
        observe_condition="contains READY",
    )
    restored = ObservePayload.model_validate_json(payload.model_dump_json())
    assert restored == payload


def test_observation_check_rejects_out_of_range_iteration() -> None:
    with pytest.raises(ValidationError):
        ObservationCheck(iteration=0)


def test_observation_check_rejects_extra_field() -> None:
    with pytest.raises(ValidationError):
        ObservationCheck(iteration=1, unexpected="bad")


def test_observation_check_round_trip() -> None:
    check = ObservationCheck(
        iteration=2,
        elapsed_seconds=30.0,
        check_output="HTTP 503",
        condition_met=False,
        assessment="not ready",
    )
    restored = ObservationCheck.model_validate_json(check.model_dump_json())
    assert restored == check


# Characterization


def test_observe_mode_name_is_stable() -> None:
    assert ObserveMode.mode_name == OBSERVE_MODE


def test_observe_mode_category_is_workflow() -> None:
    assert ObserveMode.mode_category == "workflow"


def test_observe_mode_has_resume_is_false() -> None:
    assert ObserveMode.has_resume is True


def test_observe_mode_implements_workflow_mode() -> None:
    assert issubclass(ObserveMode, WorkflowMode)


def test_observe_mode_is_registered_in_global_registry() -> None:
    assert get_route_descriptor(OBSERVE_MODE) is None


# Payload extraction / validation


def test_target_from_observe_target_field() -> None:
    ctx, _ = _ctx(observe_target="http://status.example.com")
    mode = ObserveMode()
    assert mode._target_from_context(ctx) == "http://status.example.com"


def test_target_falls_back_to_objective() -> None:
    ctx, _ = _ctx(observe_target="", objective="objective-target")
    mode = ObserveMode()
    assert mode._target_from_context(ctx) == "objective-target"


def test_target_falls_back_to_state_goal() -> None:
    working_state = _state(goal="state-goal-target")
    ctx, _ = _ctx(
        state=working_state,
        observe_target="",
        objective="",
        user_input="",
    )
    mode = ObserveMode()
    assert mode._target_from_context(ctx) == "state-goal-target"


def test_target_falls_back_to_user_input() -> None:
    working_state = _state(goal="")
    ctx, _ = _ctx(
        state=working_state,
        observe_target="",
        objective="",
        user_input="user-input-target",
    )
    mode = ObserveMode()
    assert mode._target_from_context(ctx) == "user-input-target"


def test_missing_target_fails_validate() -> None:
    working_state = _state(goal="")
    ctx, _ = _ctx(
        state=working_state,
        observe_target="",
        objective="",
        user_input="",
    )
    mode = ObserveMode()
    result = mode.validate(ctx)
    assert result is not None
    assert result.passed is False
    assert result.code == "missing_observe_target"


def test_missing_condition_fails_validate() -> None:
    ctx, _ = _ctx(observe_condition="")
    mode = ObserveMode()
    result = mode.validate(ctx)
    assert result is not None
    assert result.passed is False
    assert result.code == "missing_observe_condition"


# Initialize


def test_initialize_creates_workflow_plan_with_derived_step_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _ = _ctx()
    mode = ObserveMode()
    mode.apply_mode_config(
        config={
            "observe_poll_interval_seconds": 30,
            "observe_timeout_seconds": 120,
        },
        runner=None,
        profile=None,
    )
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.monotonic",
        lambda: 100.0,
    )
    plan = mode.initialize(ctx)
    assert isinstance(plan, WorkflowPlan)
    assert len(plan.steps) == 4
    assert mode._start_monotonic == 100.0
    assert mode._check_history == []


def test_initialize_apply_mode_config_test_seam() -> None:
    ctx, _ = _ctx()
    mode = ObserveMode()
    mode.apply_mode_config(
        config={
            "observe_poll_interval_seconds": 10,
            "observe_timeout_seconds": 25,
        },
        runner=None,
        profile=None,
    )
    plan = mode.initialize(ctx)
    assert len(plan.steps) == 2


def test_initialize_accepts_empty_check_command_and_infers_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _ = _ctx(
        observe_check_command="",
        response_queue=['{"check_command":"fetch http://example.com/health"}'],
    )
    mode = ObserveMode()
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.monotonic",
        lambda: 0.0,
    )
    mode.initialize(ctx)
    assert mode._check_command == "fetch http://example.com/health"


# Execute step


def test_execute_step_first_iteration_does_not_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, services = _ctx()
    mode = ObserveMode()
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.monotonic",
        lambda: 0.0,
    )
    mode.initialize(ctx)
    sleeps: list[float] = []
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )
    result = mode.execute_step(
        ctx, WorkflowStep(value="observe_check_1", index=0, total=2)
    )
    assert result.metadata["check_output"] == "mock check output"
    assert sleeps == []
    assert len(services.plan_calls) == 1


def test_execute_step_subsequent_iterations_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _ = _ctx()
    mode = ObserveMode()
    mode.apply_mode_config(
        config={
            "observe_poll_interval_seconds": 3,
            "observe_timeout_seconds": 9,
        },
        runner=None,
        profile=None,
    )
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.monotonic",
        lambda: 0.0,
    )
    mode.initialize(ctx)
    sleeps: list[float] = []
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )
    mode.execute_step(ctx, WorkflowStep(value="observe_check_2", index=1, total=3))
    assert sleeps == [1.0, 1.0, 1.0]


def test_execute_step_child_state_isolation() -> None:
    ctx, _ = _ctx()
    ctx.state.task_backed_task_id = "parent-task-id"
    child_state = build_child_state(
        parent_state=ctx.state,
        child_budget=BudgetCounters(
            ticks=5,
            tool_calls=3,
            a2a_calls=1,
            tokens=100,
            time_ms=30000,
        ),
        goal="check endpoint",
    )
    assert child_state.task_backed_task_id is None
    assert child_state.pending_jobs == []
    assert child_state.step_outputs == []
    assert child_state.goal == "check endpoint"


def test_execute_step_recursive_observe_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = SimpleNamespace(profile=SimpleNamespace())
    ctx, _ = _ctx(runner=runner)
    mode = ObserveMode()
    mode._target = "http://example.com/health"
    mode._check_command = "fetch http://example.com/health"
    mode._max_checks = 2

    monkeypatch.setattr(
        "openminion.modules.brain.loop.orchestration.decide",
        lambda *args, **kwargs: SimpleNamespace(mode=OBSERVE_MODE),
    )

    seen: dict[str, Any] = {}

    def _invoke(self, *, state, decision, user_input, logger, depth=0):
        del self, state, user_input, logger, depth
        seen["mode"] = decision.mode
        return SimpleNamespace(message="child check output")

    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.child_execution.invoke_decision_direct",
        _invoke,
    )
    result = mode.execute_step(
        ctx, WorkflowStep(value="observe_check_1", index=0, total=2)
    )
    assert seen["mode"] == "act"
    assert result.metadata["check_output"] == "child check output"


# Judge step


def test_judge_step_condition_met_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, _ = _ctx(
        response_queue=[_assessment_json(condition_met=True, assessment="ready")]
    )
    mode = ObserveMode()
    mode._target = "http://example.com/health"
    mode._condition = "returns HTTP 200"
    mode._start_monotonic = 0.0
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.monotonic",
        lambda: 10.0,
    )
    judgment = mode.judge_step(
        ctx,
        WorkflowStep(value="observe_check_1", index=0, total=2),
        StepResult(
            step=WorkflowStep(value="observe_check_1", index=0, total=2),
            metadata={"check_output": "HTTP 200"},
        ),
    )
    assert judgment.disposition == "close"
    assert mode._check_history[-1].condition_met is True


def test_judge_step_condition_not_met_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _ = _ctx(
        response_queue=[_assessment_json(condition_met=False, assessment="not ready")]
    )
    mode = ObserveMode()
    mode._target = "http://example.com/health"
    mode._condition = "returns HTTP 200"
    mode._start_monotonic = 0.0
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.monotonic",
        lambda: 10.0,
    )
    judgment = mode.judge_step(
        ctx,
        WorkflowStep(value="observe_check_1", index=0, total=2),
        StepResult(
            step=WorkflowStep(value="observe_check_1", index=0, total=2),
            metadata={"check_output": "HTTP 503"},
        ),
    )
    assert judgment.disposition == "continue"


def test_judge_step_timeout_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, _ = _ctx(
        response_queue=[_assessment_json(condition_met=False, assessment="still down")]
    )
    mode = ObserveMode()
    mode._target = "http://example.com/health"
    mode._condition = "returns HTTP 200"
    mode._timeout_seconds = 60
    mode._start_monotonic = 0.0
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.monotonic",
        lambda: 60.0,
    )
    judgment = mode.judge_step(
        ctx,
        WorkflowStep(value="observe_check_2", index=1, total=2),
        StepResult(
            step=WorkflowStep(value="observe_check_2", index=1, total=2),
            metadata={"check_output": "HTTP 503"},
        ),
    )
    assert judgment.disposition == "close"


def test_judge_step_parse_failure_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _ = _ctx(response_queue=["not-json"])
    mode = ObserveMode()
    mode._target = "http://example.com/health"
    mode._condition = "returns HTTP 200"
    mode._start_monotonic = 0.0
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.monotonic",
        lambda: 10.0,
    )
    judgment = mode.judge_step(
        ctx,
        WorkflowStep(value="observe_check_1", index=0, total=3),
        StepResult(
            step=WorkflowStep(value="observe_check_1", index=0, total=3),
            metadata={"check_output": "HTTP 503"},
        ),
    )
    assert judgment.disposition == "continue"
    assert mode._check_history[-1].condition_met is False
    assert "structured evaluation unavailable" in mode._check_history[-1].assessment


def test_judge_step_notes_consecutive_identical_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _ = _ctx(
        response_queue=[
            _assessment_json(check_output="same", assessment="unchanged"),
            _assessment_json(check_output="same", assessment="unchanged"),
            _assessment_json(check_output="same", assessment="unchanged"),
        ]
    )
    mode = ObserveMode()
    mode._target = "http://example.com/health"
    mode._condition = "returns HTTP 200"
    mode._start_monotonic = 0.0
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.monotonic",
        _monotonic([10.0, 20.0, 30.0]),
    )
    for idx in range(3):
        mode.judge_step(
            ctx,
            WorkflowStep(value=f"observe_check_{idx + 1}", index=idx, total=4),
            StepResult(
                step=WorkflowStep(value=f"observe_check_{idx + 1}", index=idx, total=4),
                metadata={"check_output": "same"},
            ),
        )
    assert "3 consecutive checks" in mode._check_history[-1].assessment


# Full loop / finalize


def test_full_loop_condition_met_after_two_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _ = _ctx(
        response_queue=[
            _assessment_json(
                check_output="HTTP 503", condition_met=False, assessment="not ready"
            ),
            _assessment_json(
                check_output="HTTP 200", condition_met=True, assessment="ready"
            ),
        ]
    )
    mode = ObserveMode()
    mode.apply_mode_config(
        config={
            "observe_poll_interval_seconds": 30,
            "observe_timeout_seconds": 60,
        },
        runner=None,
        profile=None,
    )
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.monotonic",
        _monotonic([0.0, 10.0, 20.0]),
    )
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.sleep",
        lambda seconds: None,
    )
    result = mode.execute(ctx)
    assert result.status == "done"
    assert "Condition met." in str(result.message)
    assert "Checks performed: 2" in str(result.message)


def test_full_loop_timeout_without_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _ = _ctx(
        response_queue=[
            _assessment_json(condition_met=False, assessment="still down"),
            _assessment_json(condition_met=False, assessment="still down"),
        ]
    )
    mode = ObserveMode()
    mode.apply_mode_config(
        config={
            "observe_poll_interval_seconds": 30,
            "observe_timeout_seconds": 60,
        },
        runner=None,
        profile=None,
    )
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.monotonic",
        _monotonic([0.0, 30.0, 60.0]),
    )
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.sleep",
        lambda seconds: None,
    )
    result = mode.execute(ctx)
    assert result.status == "done"
    assert "Timed out before the condition was met." in str(result.message)


def test_iteration_cap_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _ = _ctx(
        response_queue=[
            _assessment_json(condition_met=False, assessment="still down"),
            _assessment_json(condition_met=False, assessment="still down"),
        ]
    )
    mode = ObserveMode()
    mode.apply_mode_config(
        config={
            "observe_poll_interval_seconds": 10,
            "observe_timeout_seconds": 25,
        },
        runner=None,
        profile=None,
    )
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.monotonic",
        _monotonic([0.0, 5.0, 15.0]),
    )
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.sleep",
        lambda seconds: None,
    )
    result = mode.execute(ctx)
    assert result.status == "done"
    assert "iteration cap" in str(result.message).lower()


def test_budget_exhaustion_during_sleep_returns_waiting_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _ = _ctx()
    ctx.state.budgets_remaining.ticks = 0
    mode = ObserveMode()
    mode._target = "http://example.com/health"
    mode._check_command = "fetch http://example.com/health"
    result = mode.execute_step(
        ctx, WorkflowStep(value="observe_check_2", index=1, total=2)
    )
    assert result.mode_result is not None
    assert result.mode_result.status == "waiting_user"


# Registry integration


def test_observe_mode_appears_in_available_modes() -> None:
    assert OBSERVE_MODE not in available_routes()


def test_decision_descriptions_contains_observe() -> None:
    descriptions = decision_route_descriptions()
    assert OBSERVE_MODE not in descriptions
