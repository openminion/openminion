"""Refine-mode unit and integration tests.

Covers RFM-01 (schemas), RFM-02 (characterization), RFM-03 (payload extraction),
RFM-04 (initialize), RFM-05 (execute_step / child dispatch), RFM-06 (judge_step /
quality gate / stall detection), RFM-07 (finalize / full loop), RFM-08 (registry).
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
from openminion.modules.brain.loop.tools.phases.refine import (
    REFINE_MODE,
    RefineMode,
    RefinePayload,
    RefinementRound,
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


# Shared test infrastructure


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
        return Plan(objective="mock plan result.", steps=[])

    def approve_command(self, *, state, command, logger):
        del state, logger
        return command

    def act_command(self, *, state, command, logger):
        del state, command, logger
        raise AssertionError("refine mode should not call ctx.act_command() directly")

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

    def emit_status(self, **kwargs) -> None:
        self.statuses.append(dict(kwargs))


@dataclass
class _StructuredLLM:
    payload: dict[str, Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def estimate_tokens(self, *, model: str, context: dict[str, Any]) -> int:
        del model, context
        return 100

    def call_structured(
        self, *, model: str, purpose: str, context: dict[str, Any], schema
    ):
        self.calls.append(
            {
                "model": model,
                "purpose": purpose,
                "context": context,
                "schema": getattr(schema, "__name__", str(schema)),
            }
        )
        return dict(self.payload)


@dataclass
class _StructuredRunner:
    llm_api: _StructuredLLM
    profile: Any = field(
        default_factory=lambda: SimpleNamespace(
            llm_profiles=SimpleNamespace(
                reflect_model="reflect-default",
                summarize_model="summarize-default",
            )
        )
    )

    def _build_context(self, *, state, purpose, budget, hints, logger, mode_name=None):
        del state, logger, mode_name
        return {
            "purpose": purpose,
            "budget": budget,
            "user_input": hints.get("user_input", ""),
        }

    def _debit_tokens(self, state, raw, logger) -> None:
        del state, raw, logger


def _state(
    *,
    session_id: str = "s-refine",
    ticks: int = 30,
    goal: str = "Improve the handler module",
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
    refine_target: str = "handler.py",
    refine_criteria: list[str] | None = None,
    objective: str | None = None,
    user_input: str | None = None,
    response_queue: list[str] | None = None,
) -> tuple[ExecutionContext, _FakeServices]:
    working_state = state or _state()
    services = _FakeServices(response_queue=list(response_queue or []))
    decision = SimpleNamespace(
        mode=REFINE_MODE,
        confidence=0.9,
        reason_code="refine_request",
        refine_target=refine_target,
        refine_criteria=refine_criteria if refine_criteria is not None else [],
        objective=objective or refine_target,
    )
    logger = SimpleNamespace(events=[], emit=lambda *args, **kwargs: None)
    ctx = ExecutionContext(
        state=working_state,
        decision=decision,
        user_input=user_input if user_input is not None else refine_target,
        logger=logger,
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=services,
    )
    return ctx, services


# Schema tests


def test_refine_payload_valid() -> None:
    p = RefinePayload(refine_target="handler.py", refine_criteria=["error handling"])
    assert p.refine_target == "handler.py"
    assert p.refine_criteria == ["error handling"]


def test_refine_payload_empty_criteria_allowed() -> None:
    p = RefinePayload(refine_target="README.md")
    assert p.refine_criteria == []


def test_refine_payload_rejects_empty_target() -> None:
    with pytest.raises(ValidationError):
        RefinePayload(refine_target="")


def test_refine_payload_rejects_extra_field() -> None:
    with pytest.raises(ValidationError):
        RefinePayload(refine_target="x.py", unexpected_field="oops")


def test_refine_payload_json_round_trip() -> None:
    p = RefinePayload(refine_target="foo.py", refine_criteria=["style", "naming"])
    restored = RefinePayload.model_validate_json(p.model_dump_json())
    assert restored == p


def test_refinement_round_valid() -> None:
    r = RefinementRound(
        iteration=1,
        action_taken="Fixed error handling",
        quality_assessment="Better but not complete",
        remaining_issues=["missing docstrings"],
        passed_gate=False,
    )
    assert r.iteration == 1
    assert r.passed_gate is False


def test_refinement_round_rejects_zero_iteration() -> None:
    with pytest.raises(ValidationError):
        RefinementRound(iteration=0)


def test_refinement_round_rejects_negative_iteration() -> None:
    with pytest.raises(ValidationError):
        RefinementRound(iteration=-1)


def test_refinement_round_rejects_extra_field() -> None:
    with pytest.raises(ValidationError):
        RefinementRound(iteration=1, unexpected="bad")


def test_refinement_round_json_round_trip() -> None:
    r = RefinementRound(
        iteration=2,
        action_taken="Improved naming",
        remaining_issues=["style"],
        passed_gate=True,
    )
    restored = RefinementRound.model_validate_json(r.model_dump_json())
    assert restored == r


def test_refinement_round_defaults() -> None:
    r = RefinementRound(iteration=1)
    assert r.action_taken == ""
    assert r.quality_assessment == ""
    assert r.remaining_issues == []
    assert r.passed_gate is False


# Characterization — mode attributes and registry


def test_refine_mode_name_is_stable() -> None:
    assert RefineMode.mode_name == REFINE_MODE


def test_refine_mode_category_is_workflow() -> None:
    assert RefineMode.mode_category == "workflow"


def test_refine_mode_has_resume_is_false() -> None:
    assert RefineMode.has_resume is True


def test_refine_mode_has_validate_is_true() -> None:
    assert RefineMode.has_validate is True


def test_refine_mode_has_prepare_is_true() -> None:
    assert RefineMode.has_prepare is True


def test_refine_mode_priority_hint() -> None:
    assert RefineMode.priority_hint == 70


def test_refine_mode_implements_workflow_mode() -> None:
    assert issubclass(RefineMode, WorkflowMode)


def test_refine_mode_is_registered_in_global_registry() -> None:
    assert get_route_descriptor(REFINE_MODE) is None


def test_refine_mode_appears_in_available_modes() -> None:
    assert REFINE_MODE not in available_routes()


def test_refine_mode_decision_descriptions_contains_refine() -> None:
    descriptions = decision_route_descriptions()
    assert REFINE_MODE not in descriptions


# Payload extraction and fallback chain


def test_target_from_refine_target_field() -> None:
    ctx, _ = _ctx(refine_target="handler.py")
    mode = RefineMode()
    assert mode._target_from_context(ctx) == "handler.py"


def test_target_falls_back_to_objective() -> None:
    ctx, _ = _ctx(refine_target="", objective="fallback-objective.py")
    mode = RefineMode()
    assert mode._target_from_context(ctx) == "fallback-objective.py"


def test_target_falls_back_to_state_goal() -> None:
    working_state = _state(goal="state-goal-target.py")
    ctx = ExecutionContext(
        state=working_state,
        decision=SimpleNamespace(
            mode=REFINE_MODE,
            refine_target="",
            refine_criteria=[],
            objective="",
        ),
        user_input="",
        logger=SimpleNamespace(emit=lambda *a, **kw: None),
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=_FakeServices(),
    )
    mode = RefineMode()
    assert mode._target_from_context(ctx) == "state-goal-target.py"


def test_target_falls_back_to_user_input() -> None:
    working_state = _state(goal="")
    ctx = ExecutionContext(
        state=working_state,
        decision=SimpleNamespace(
            mode=REFINE_MODE,
            refine_target="",
            refine_criteria=[],
            objective="",
        ),
        user_input="user-input-target.py",
        logger=SimpleNamespace(emit=lambda *a, **kw: None),
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=_FakeServices(),
    )
    mode = RefineMode()
    assert mode._target_from_context(ctx) == "user-input-target.py"


def test_missing_target_fails_validate() -> None:
    working_state = _state(goal="")
    ctx = ExecutionContext(
        state=working_state,
        decision=SimpleNamespace(
            mode=REFINE_MODE,
            refine_target="",
            refine_criteria=[],
            objective="",
        ),
        user_input="",
        logger=SimpleNamespace(emit=lambda *a, **kw: None),
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=_FakeServices(),
    )
    mode = RefineMode()
    result = mode.validate(ctx)
    assert result is not None
    assert result.passed is False
    assert result.code == "missing_refine_target"


def test_criteria_from_list() -> None:
    ctx, _ = _ctx(refine_criteria=["error handling", "naming"])
    mode = RefineMode()
    assert mode._criteria_from_context(ctx) == ["error handling", "naming"]


def test_criteria_empty_when_not_provided() -> None:
    ctx, _ = _ctx(refine_criteria=[])
    mode = RefineMode()
    assert mode._criteria_from_context(ctx) == []


# Initialize


def test_initialize_creates_workflow_plan_with_default_steps() -> None:
    ctx, _ = _ctx()
    mode = RefineMode()
    plan = mode.initialize(ctx)
    assert isinstance(plan, WorkflowPlan)
    assert len(plan.steps) == 3  # default max_refine_iterations


def test_initialize_clears_round_history() -> None:
    ctx, _ = _ctx()
    mode = RefineMode()
    # Simulate prior rounds.
    mode._round_history = [RefinementRound(iteration=1)]
    plan = mode.initialize(ctx)
    assert mode._round_history == []
    assert len(plan.steps) == 3


def test_initialize_apply_mode_config_test_seam() -> None:
    """apply_mode_config is an internal/test seam only."""
    ctx, _ = _ctx()
    mode = RefineMode()
    mode.apply_mode_config(
        config={"max_refine_iterations": 5}, runner=None, profile=None
    )
    plan = mode.initialize(ctx)
    assert len(plan.steps) == 5


def test_iteration_budget_uses_remaining_iterations() -> None:
    ctx, _ = _ctx()
    mode = RefineMode()
    mode.apply_mode_config(
        config={"max_refine_iterations": 5}, runner=None, profile=None
    )
    mode.initialize(ctx)
    mode._round_history = [
        RefinementRound(iteration=1),
        RefinementRound(iteration=2),
    ]
    budget = mode._iteration_budget(ctx)
    assert budget.ticks == 10  # 30 // (5 - 2)
    assert budget.tool_calls == 5  # 15 // 3


# Execute step / child dispatch


def test_execute_step_returns_improvement_text() -> None:
    ctx, services = _ctx(refine_target="handler.py")
    mode = RefineMode()
    mode.initialize(ctx)

    from openminion.modules.brain.execution.workflow import WorkflowStep

    step = WorkflowStep(value="refine_iteration_1", index=0, total=3)
    result = mode.execute_step(ctx, step)
    assert result.metadata.get("improvement")
    assert services.plan_calls  # fell back to ctx.plan()


def test_execute_step_child_state_isolation() -> None:
    ctx, _ = _ctx()
    ctx.state.task_backed_task_id = "parent-task-id"
    mode = RefineMode()
    child_state = build_child_state(
        parent_state=ctx.state,
        child_budget=mode._iteration_budget(ctx),
        goal="improve x.py",
    )
    assert child_state.task_backed_task_id is None
    assert child_state.pending_jobs == []
    assert child_state.step_outputs == []
    assert child_state.goal == "improve x.py"


def test_execute_step_recursive_refine_blocked() -> None:
    # When runner is None, falls back to ctx.plan() — no recursion possible.
    ctx, services = _ctx(refine_target="handler.py")
    mode = RefineMode()
    mode.initialize(ctx)

    step = WorkflowStep(value="refine_iteration_1", index=0, total=3)
    result = mode.execute_step(ctx, step)
    assert result.metadata.get("improvement")
    assert len(services.plan_calls) == 1


# Judge step / quality gate / stall detection


_GATE_PASS_JSON = json.dumps(
    {
        "action_taken": "Fixed error handling",
        "quality_assessment": "All criteria met.",
        "remaining_issues": [],
        "passed_gate": True,
    }
)

_GATE_FAIL_JSON = json.dumps(
    {
        "action_taken": "Improved naming",
        "quality_assessment": "Naming improved but docstrings missing.",
        "remaining_issues": ["missing docstrings"],
        "passed_gate": False,
    }
)


def test_quality_gate_pass_closes() -> None:
    ctx, _ = _ctx(response_queue=[_GATE_PASS_JSON])
    mode = RefineMode()
    mode.initialize(ctx)
    step = WorkflowStep(value="refine_iteration_1", index=0, total=3)
    step_result = StepResult(
        step=step, metadata={"improvement": "Fixed error handling"}
    )
    judgment = mode.judge_step(ctx, step, step_result)
    assert judgment.disposition == "close"
    assert len(mode._round_history) == 1
    assert mode._round_history[0].passed_gate is True


def test_quality_gate_uses_structured_llm_when_runner_available() -> None:
    ctx, services = _ctx(response_queue=[""])
    services.direct_response = lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("legacy direct_response fallback should not run")
    )
    services.runner = _StructuredRunner(
        llm_api=_StructuredLLM(
            payload={
                "action_taken": "Tightened wording",
                "quality_assessment": "All criteria met.",
                "remaining_issues": [],
                "passed_gate": True,
            }
        )
    )
    mode = RefineMode()
    mode.initialize(ctx)
    step = WorkflowStep(value="refine_iteration_1", index=0, total=3)
    step_result = StepResult(step=step, metadata={"improvement": "Fixed it"})
    judgment = mode.judge_step(ctx, step, step_result)
    assert judgment.disposition == "close"
    assert mode._round_history[0].action_taken == "Tightened wording"
    assert ctx.state.llm_calls_used == 1


def test_quality_gate_fail_continues() -> None:
    ctx, _ = _ctx(response_queue=[_GATE_FAIL_JSON])
    mode = RefineMode()
    mode.initialize(ctx)
    step = WorkflowStep(value="refine_iteration_1", index=0, total=3)
    step_result = StepResult(step=step, metadata={"improvement": "Improved naming"})
    judgment = mode.judge_step(ctx, step, step_result)
    assert judgment.disposition == "continue"
    assert mode._round_history[0].passed_gate is False


def test_stall_detection_closes() -> None:
    stall_json = json.dumps(
        {
            "action_taken": "Attempted fix",
            "quality_assessment": "Same issues remain.",
            "remaining_issues": ["missing docstrings"],
            "passed_gate": False,
        }
    )
    ctx, _ = _ctx(response_queue=[stall_json])
    mode = RefineMode()
    mode.initialize(ctx)
    # Seed prior round with identical remaining_issues.
    mode._round_history.append(
        RefinementRound(
            iteration=1,
            action_taken="First attempt",
            remaining_issues=["missing docstrings"],
            passed_gate=False,
        )
    )
    step = WorkflowStep(value="refine_iteration_2", index=1, total=3)
    step_result = StepResult(step=step, metadata={"improvement": "Attempted fix"})
    judgment = mode.judge_step(ctx, step, step_result)
    assert judgment.disposition == "close"
    assert judgment.metadata.get("stall_detected") is True


def test_stall_detection_does_not_fire_on_different_issues() -> None:
    round2_json = json.dumps(
        {
            "action_taken": "Fixed docstrings",
            "quality_assessment": "Better, but tests missing.",
            "remaining_issues": ["missing tests"],
            "passed_gate": False,
        }
    )
    ctx, _ = _ctx(response_queue=[round2_json])
    mode = RefineMode()
    mode.initialize(ctx)
    mode._round_history.append(
        RefinementRound(
            iteration=1,
            action_taken="First attempt",
            remaining_issues=["missing docstrings"],
            passed_gate=False,
        )
    )
    step = WorkflowStep(value="refine_iteration_2", index=1, total=3)
    step_result = StepResult(step=step, metadata={"improvement": "Fixed docstrings"})
    judgment = mode.judge_step(ctx, step, step_result)
    assert judgment.disposition == "continue"
    assert judgment.metadata.get("stall_detected") is None


def test_parse_failure_returns_fallback_round() -> None:
    ctx, _ = _ctx(response_queue=["{not valid json"])
    mode = RefineMode()
    mode.initialize(ctx)
    step = WorkflowStep(value="refine_iteration_1", index=0, total=3)
    step_result = StepResult(step=step, metadata={"improvement": "something"})
    judgment = mode.judge_step(ctx, step, step_result)
    assert judgment.disposition == "continue"
    assert mode._round_history[0].passed_gate is False
    assert "Could not assess" in mode._round_history[0].quality_assessment


def test_empty_response_returns_fallback_round() -> None:
    ctx, _ = _ctx(response_queue=[""])
    mode = RefineMode()
    mode.initialize(ctx)
    step = WorkflowStep(value="refine_iteration_1", index=0, total=3)
    step_result = StepResult(step=step, metadata={"improvement": "something"})
    judgment = mode.judge_step(ctx, step, step_result)
    assert judgment.disposition == "continue"
    assert mode._round_history[0].passed_gate is False


def test_judge_strips_markdown_fences() -> None:
    fenced = "```json\n" + _GATE_PASS_JSON + "\n```"
    ctx, _ = _ctx(response_queue=[fenced])
    mode = RefineMode()
    mode.initialize(ctx)
    step = WorkflowStep(value="refine_iteration_1", index=0, total=3)
    step_result = StepResult(step=step, metadata={"improvement": "Fixed it"})
    judgment = mode.judge_step(ctx, step, step_result)
    assert judgment.disposition == "close"
    assert mode._round_history[0].passed_gate is True


# Finalize / full loop


def test_finalize_produces_summary() -> None:
    ctx, _ = _ctx()
    mode = RefineMode()
    mode._round_history = [
        RefinementRound(
            iteration=1,
            action_taken="Fixed error handling",
            quality_assessment="All good",
            passed_gate=True,
        )
    ]
    result = mode.finalize(ctx)
    assert result.status == "done"
    assert "1 round(s)" in result.message
    assert "Quality gate passed" in result.message


def test_finalize_reports_stall_reason() -> None:
    ctx, _ = _ctx()
    mode = RefineMode()
    mode._termination_reason = "stall"
    mode._round_history = [
        RefinementRound(
            iteration=1,
            action_taken="Attempt 1",
            quality_assessment="Issue remains.",
            remaining_issues=["stubborn bug"],
            passed_gate=False,
        ),
        RefinementRound(
            iteration=2,
            action_taken="Attempt 2",
            quality_assessment="Same issue remains.",
            remaining_issues=["stubborn bug"],
            passed_gate=False,
        ),
    ]
    result = mode.finalize(ctx)
    assert result.status == "done"
    assert "Refinement stalled" in result.message
    assert "Outstanding issues: stubborn bug" in result.message


def test_finalize_empty_rounds() -> None:
    ctx, _ = _ctx()
    mode = RefineMode()
    mode._round_history = []
    result = mode.finalize(ctx)
    assert result.status == "done"
    assert "No refinement rounds" in result.message


def test_full_loop_converges_on_round_2() -> None:
    """Decision → improvements → quality gate passes after round 2 → done."""
    round1_response = json.dumps(
        {
            "action_taken": "Improved error handling",
            "quality_assessment": "Better but naming still off.",
            "remaining_issues": ["naming"],
            "passed_gate": False,
        }
    )
    round2_response = json.dumps(
        {
            "action_taken": "Fixed naming",
            "quality_assessment": "All criteria met.",
            "remaining_issues": [],
            "passed_gate": True,
        }
    )
    ctx, _ = _ctx(
        refine_target="handler.py",
        refine_criteria=["error handling", "naming"],
        response_queue=[round1_response, round2_response],
    )
    mode = RefineMode()
    result = mode.execute(ctx)
    assert result.status == "done"
    assert len(mode._round_history) == 2
    assert mode._round_history[0].passed_gate is False
    assert mode._round_history[1].passed_gate is True
    assert "Quality gate passed" in result.message


def test_full_loop_with_empty_criteria() -> None:
    """Empty criteria → LLM infers; should still complete."""
    gate_pass = json.dumps(
        {
            "action_taken": "General improvement",
            "quality_assessment": "Looks good.",
            "remaining_issues": [],
            "passed_gate": True,
        }
    )
    ctx, _ = _ctx(
        refine_target="README.md",
        refine_criteria=[],
        response_queue=[gate_pass],
    )
    mode = RefineMode()
    result = mode.execute(ctx)
    assert result.status == "done"


def test_full_loop_iteration_cap_reached() -> None:
    """Gate never passes → all 3 iterations run → cap reached."""
    fail_response = json.dumps(
        {
            "action_taken": "Attempted fix",
            "quality_assessment": "Still failing.",
            "remaining_issues": ["issue A"],
            "passed_gate": False,
        }
    )
    fail2 = json.dumps(
        {
            "action_taken": "Second attempt",
            "quality_assessment": "Still issues.",
            "remaining_issues": ["issue B"],
            "passed_gate": False,
        }
    )
    fail3 = json.dumps(
        {
            "action_taken": "Third attempt",
            "quality_assessment": "Ran out of iterations.",
            "remaining_issues": ["issue C"],
            "passed_gate": False,
        }
    )
    ctx, _ = _ctx(
        refine_target="handler.py",
        response_queue=[fail_response, fail2, fail3],
    )
    mode = RefineMode()
    result = mode.execute(ctx)
    assert result.status == "done"
    assert len(mode._round_history) == 3
    assert "Iteration cap reached" in result.message
    assert "issue C" in result.message


def test_full_loop_stall_forces_early_close() -> None:
    """Identical remaining_issues → stall → early close before cap."""
    round1 = json.dumps(
        {
            "action_taken": "Attempt 1",
            "quality_assessment": "Issues remain.",
            "remaining_issues": ["stubborn bug"],
            "passed_gate": False,
        }
    )
    round2 = json.dumps(
        {
            "action_taken": "Attempt 2",
            "quality_assessment": "Same issues remain.",
            "remaining_issues": ["stubborn bug"],
            "passed_gate": False,
        }
    )
    ctx, _ = _ctx(
        refine_target="handler.py",
        response_queue=[round1, round2],
    )
    mode = RefineMode()
    result = mode.execute(ctx)
    assert result.status == "done"
    assert len(mode._round_history) == 2
    assert "Refinement stalled" in result.message
    assert "stubborn bug" in result.message


def test_full_loop_missing_target_validate_rejects() -> None:
    """Empty target after full fallback chain → validate() rejects."""
    working_state = _state(goal="")
    ctx = ExecutionContext(
        state=working_state,
        decision=SimpleNamespace(
            mode=REFINE_MODE,
            refine_target="",
            refine_criteria=[],
            objective="",
        ),
        user_input="",
        logger=SimpleNamespace(emit=lambda *a, **kw: None),
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=_FakeServices(),
    )
    mode = RefineMode()
    validation = mode.validate(ctx)
    assert validation is not None
    assert validation.passed is False


# Registry


def test_refine_mode_in_available_modes() -> None:
    modes = available_routes()
    assert REFINE_MODE not in modes


def test_refine_mode_in_decision_descriptions() -> None:
    descriptions = decision_route_descriptions()
    assert REFINE_MODE not in descriptions
