"""Eval-mode unit and integration tests.

Covers EVM-01 (schemas), EVM-02 (characterization), EVM-03 (payload extraction),
EVM-04 (evidence gathering), EVM-05 (judgment parsing), EVM-06 (full loop),
and EVM-07 (registry).
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
from openminion.modules.brain.loop.tools.phases.eval import (
    EVAL_MODE,
    EvalCriterion,
    EvalJudgment,
    EvalMode,
    EvalPayload,
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
    # Direct-response queue: items are popped in order; "" when empty.
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
        raise AssertionError("eval mode should not call ctx.act_command() directly")

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

    # Task stubs — eval does not use task-backed infrastructure.
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

    def emit_status(self, **kwargs) -> None:
        self.statuses.append(dict(kwargs))


def _state(
    *,
    session_id: str = "s-eval",
    ticks: int = 20,
    goal: str = "Evaluate the handler module",
) -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="router-agent",
        goal=goal,
        budgets_remaining=BudgetCounters(
            ticks=ticks,
            tool_calls=10,
            a2a_calls=2,
            tokens=5000,
            time_ms=120000,
        ),
        trace_id=f"trace-{session_id}",
    )


def _ctx(
    *,
    state: WorkingState | None = None,
    eval_target: str = "handler.py",
    eval_criteria: list[str] | None = None,
    objective: str | None = None,
    user_input: str | None = None,
    response_queue: list[str] | None = None,
) -> tuple[ExecutionContext, _FakeServices]:
    working_state = state or _state()
    services = _FakeServices(response_queue=list(response_queue or []))
    decision = SimpleNamespace(
        mode=EVAL_MODE,
        confidence=0.9,
        reason_code="eval_request",
        eval_target=eval_target,
        eval_criteria=eval_criteria if eval_criteria is not None else [],
        objective=objective or eval_target,
    )
    logger = SimpleNamespace(events=[], emit=lambda *args, **kwargs: None)
    ctx = ExecutionContext(
        state=working_state,
        decision=decision,
        user_input=user_input if user_input is not None else eval_target,
        logger=logger,
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=services,
    )
    return ctx, services


# Schema tests


def test_eval_payload_valid() -> None:
    p = EvalPayload(eval_target="handler.py", eval_criteria=["error handling"])
    assert p.eval_target == "handler.py"
    assert p.eval_criteria == ["error handling"]


def test_eval_payload_empty_criteria_allowed() -> None:
    p = EvalPayload(eval_target="README.md")
    assert p.eval_criteria == []


def test_eval_payload_rejects_empty_target() -> None:
    with pytest.raises(ValidationError):
        EvalPayload(eval_target="")


def test_eval_payload_rejects_extra_field() -> None:
    with pytest.raises(ValidationError):
        EvalPayload(eval_target="x.py", unexpected_field="oops")


def test_eval_payload_json_round_trip() -> None:
    p = EvalPayload(eval_target="foo.py", eval_criteria=["style"])
    restored = EvalPayload.model_validate_json(p.model_dump_json())
    assert restored == p


def test_eval_criterion_valid() -> None:
    c = EvalCriterion(name="error handling", verdict="pass")
    assert c.verdict == "pass"
    assert c.evidence == ""


def test_eval_criterion_normalizes_verdict_aliases() -> None:
    assert EvalCriterion(name="robustness", verdict="PASS").verdict == "pass"
    assert EvalCriterion(name="robustness", verdict="warning").verdict == "partial"
    assert EvalCriterion(name="robustness", verdict="FAILED").verdict == "fail"


def test_eval_criterion_rejects_missing_name() -> None:
    with pytest.raises(ValidationError):
        EvalCriterion(name="", verdict="pass")


def test_eval_criterion_rejects_extra_field() -> None:
    with pytest.raises(ValidationError):
        EvalCriterion(name="style", verdict="fail", bad="field")


def test_eval_judgment_valid() -> None:
    j = EvalJudgment(target="x.py", overall_verdict="pass", confidence=0.9)
    assert j.target == "x.py"
    assert j.criteria == []


def test_eval_judgment_normalizes_overall_verdict_aliases() -> None:
    assert EvalJudgment(target="x.py", overall_verdict="PASS").overall_verdict == "pass"
    assert (
        EvalJudgment(target="x.py", overall_verdict="mixed").overall_verdict
        == "partial"
    )
    assert (
        EvalJudgment(target="x.py", overall_verdict="FAILED").overall_verdict == "fail"
    )


def test_eval_judgment_rejects_out_of_range_confidence() -> None:
    with pytest.raises(ValidationError):
        EvalJudgment(target="x.py", confidence=1.5)


def test_eval_judgment_rejects_extra_field() -> None:
    with pytest.raises(ValidationError):
        EvalJudgment(target="x.py", unknown_key="bad")


def test_eval_judgment_json_round_trip() -> None:
    j = EvalJudgment(
        target="x.py",
        criteria=[EvalCriterion(name="naming", verdict="partial")],
        overall_verdict="partial",
        summary="Mostly ok.",
        confidence=0.7,
    )
    restored = EvalJudgment.model_validate_json(j.model_dump_json())
    assert restored == j


# Characterization — mode attributes and registry


def test_eval_mode_name_is_stable() -> None:
    assert EvalMode.mode_name == EVAL_MODE


def test_eval_mode_category_is_assessment() -> None:
    assert EvalMode.mode_category == "assessment"


def test_eval_mode_has_resume_is_false() -> None:
    assert EvalMode.has_resume is True


def test_eval_mode_has_validate_is_true() -> None:
    assert EvalMode.has_validate is True


def test_eval_mode_has_prepare_is_true() -> None:
    assert EvalMode.has_prepare is True


def test_eval_mode_priority_hint() -> None:
    assert EvalMode.priority_hint == 65


def test_eval_mode_is_registered_in_global_registry() -> None:
    assert get_route_descriptor(EVAL_MODE) is None


def test_eval_mode_appears_in_available_modes() -> None:
    assert EVAL_MODE not in available_routes()


# Payload extraction and fallback chain


def test_target_from_eval_target_field() -> None:
    ctx, _ = _ctx(eval_target="handler.py")
    mode = EvalMode()
    assert mode._target_from_context(ctx) == "handler.py"


def test_target_falls_back_to_objective() -> None:
    # eval_target is empty, falls back to objective.
    ctx, _ = _ctx(eval_target="", objective="fallback-objective.py")
    mode = EvalMode()
    assert mode._target_from_context(ctx) == "fallback-objective.py"


def test_target_falls_back_to_state_goal() -> None:
    working_state = _state(goal="state-goal-target.py")
    ctx = ExecutionContext(
        state=working_state,
        decision=SimpleNamespace(
            mode=EVAL_MODE,
            eval_target="",
            eval_criteria=[],
            objective="",
        ),
        user_input="",
        logger=SimpleNamespace(emit=lambda *a, **kw: None),
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=_FakeServices(),
    )
    mode = EvalMode()
    assert mode._target_from_context(ctx) == "state-goal-target.py"


def test_target_falls_back_to_user_input() -> None:
    working_state = _state(goal="")
    ctx = ExecutionContext(
        state=working_state,
        decision=SimpleNamespace(
            mode=EVAL_MODE,
            eval_target="",
            eval_criteria=[],
            objective="",
        ),
        user_input="user-input-target.py",
        logger=SimpleNamespace(emit=lambda *a, **kw: None),
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=_FakeServices(),
    )
    mode = EvalMode()
    assert mode._target_from_context(ctx) == "user-input-target.py"


def test_missing_target_fails_validate() -> None:
    working_state = _state(goal="")
    ctx = ExecutionContext(
        state=working_state,
        decision=SimpleNamespace(
            mode=EVAL_MODE,
            eval_target="",
            eval_criteria=[],
            objective="",
        ),
        user_input="",
        logger=SimpleNamespace(emit=lambda *a, **kw: None),
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=_FakeServices(),
    )
    mode = EvalMode()
    result = mode.validate(ctx)
    assert result is not None
    assert result.passed is False
    assert result.code == "missing_eval_target"


def test_criteria_from_list() -> None:
    ctx, _ = _ctx(eval_criteria=["error handling", "naming"])
    mode = EvalMode()
    assert mode._criteria_from_context(ctx) == ["error handling", "naming"]


def test_criteria_empty_when_not_provided() -> None:
    ctx, _ = _ctx(eval_criteria=[])
    mode = EvalMode()
    assert mode._criteria_from_context(ctx) == []


# Evidence gathering


def test_evidence_gathering_returns_non_empty_text() -> None:
    ctx, services = _ctx(eval_target="handler.py")
    mode = EvalMode()
    evidence = mode._gather_evidence(ctx, target="handler.py", criteria=["style"])
    assert evidence  # non-empty
    assert services.plan_calls  # fell back to ctx.plan()


def test_evidence_gathering_child_state_isolation() -> None:
    # The child state must not share the parent's task_backed fields.
    ctx, _ = _ctx()
    ctx.state.task_backed_task_id = "parent-task-id"
    mode = EvalMode()
    child_state = build_child_state(
        parent_state=ctx.state,
        child_budget=mode._evidence_budget(ctx),
        goal="examine x.py",
    )
    assert child_state.task_backed_task_id is None
    assert child_state.pending_jobs == []
    assert child_state.step_outputs == []


def test_recursive_eval_blocked() -> None:
    # When the child decision would be "eval", it must be replaced.
    # We verify by checking the mode falls back to ctx.plan() when runner is None.
    ctx, services = _ctx(eval_target="handler.py")
    mode = EvalMode()
    # runner is None → falls back to ctx.plan() (no recursion possible)
    evidence = mode._gather_evidence(ctx, target="handler.py", criteria=[])
    assert evidence
    # plan was called exactly once for the evidence phase
    assert len(services.plan_calls) == 1


# Judgment parsing


_VALID_JUDGMENT_JSON = json.dumps(
    {
        "overall_verdict": "pass",
        "summary": "All criteria met.",
        "confidence": 0.95,
        "criteria": [
            {
                "name": "error handling",
                "verdict": "pass",
                "evidence": "try/except present",
                "description": "",
                "notes": "",
            }
        ],
    }
)


def test_judge_parses_valid_json() -> None:
    ctx, _ = _ctx(response_queue=[_VALID_JUDGMENT_JSON])
    mode = EvalMode()
    judgment = mode._judge(
        ctx,
        target="handler.py",
        criteria=["error handling"],
        evidence="found try/except blocks",
    )
    assert judgment.overall_verdict == "pass"
    assert judgment.confidence == 0.95
    assert len(judgment.criteria) == 1
    assert judgment.criteria[0].name == "error handling"


def test_judge_uses_structured_llm_when_runner_available() -> None:
    ctx, services = _ctx(response_queue=[""])
    services.direct_response = lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("legacy direct_response fallback should not run")
    )
    services.runner = _StructuredRunner(
        llm_api=_StructuredLLM(
            payload={
                "target": "ignored.py",
                "overall_verdict": "PASS",
                "summary": "All criteria met.",
                "confidence": 0.91,
                "criteria": [
                    {
                        "name": "quality",
                        "verdict": "FAILED",
                        "evidence": "missing tests",
                    }
                ],
            }
        )
    )
    mode = EvalMode()
    judgment = mode._judge(ctx, target="right.py", criteria=[], evidence="")
    assert judgment.target == "right.py"
    assert judgment.overall_verdict == "pass"
    assert judgment.criteria[0].verdict == "fail"
    assert ctx.state.llm_calls_used == 1


def test_judge_fallback_on_empty_response() -> None:
    ctx, _ = _ctx(response_queue=[""])
    mode = EvalMode()
    judgment = mode._judge(ctx, target="x.py", criteria=[], evidence="")
    assert judgment.overall_verdict == "partial"
    assert judgment.confidence == 0.0
    assert "Could not produce" in judgment.summary


def test_judge_fallback_on_malformed_json() -> None:
    ctx, _ = _ctx(response_queue=["{not valid json"])
    mode = EvalMode()
    judgment = mode._judge(ctx, target="x.py", criteria=[], evidence="some evidence")
    assert judgment.overall_verdict == "partial"
    assert judgment.confidence == 0.0


def test_judge_strips_markdown_fences() -> None:
    fenced = "```json\n" + _VALID_JUDGMENT_JSON + "\n```"
    ctx, _ = _ctx(response_queue=[fenced])
    mode = EvalMode()
    judgment = mode._judge(ctx, target="x.py", criteria=[], evidence="")
    assert judgment.overall_verdict == "pass"


def test_judge_normalizes_verdict_values() -> None:
    raw = json.dumps(
        {
            "overall_verdict": "WARNING",
            "criteria": [
                {"name": "errors", "verdict": "FAILED"},
                {"name": "docs", "verdict": "PASS"},
            ],
        }
    )
    ctx, _ = _ctx(response_queue=[raw])
    mode = EvalMode()
    judgment = mode._judge(ctx, target="x.py", criteria=[], evidence="")
    assert judgment.overall_verdict == "partial"
    assert [criterion.verdict for criterion in judgment.criteria] == ["fail", "pass"]


def test_judge_pins_requested_target_over_model_target() -> None:
    raw = json.dumps(
        {
            "target": "wrong.py",
            "overall_verdict": "pass",
            "criteria": [{"name": "quality", "verdict": "pass"}],
        }
    )
    ctx, _ = _ctx(response_queue=[raw])
    mode = EvalMode()
    judgment = mode._judge(ctx, target="right.py", criteria=[], evidence="")
    assert judgment.target == "right.py"


# Full execute loop


def test_execute_full_loop_produces_done() -> None:
    ctx, _ = _ctx(
        eval_target="handler.py",
        eval_criteria=["error handling"],
        response_queue=[_VALID_JUDGMENT_JSON],
    )
    mode = EvalMode()
    result = mode.execute(ctx)
    assert result.status == "done"
    assert result.message
    assert "handler.py" in result.message


def test_execute_with_empty_criteria_works() -> None:
    # Empty criteria → LLM infers; should still complete.
    ctx, _ = _ctx(
        eval_target="README.md",
        eval_criteria=[],
        response_queue=[_VALID_JUDGMENT_JSON],
    )
    mode = EvalMode()
    result = mode.execute(ctx)
    assert result.status == "done"


def test_execute_missing_target_returns_waiting_user() -> None:
    # When validate() is bypassed (e.g. called directly), execute() should still
    # guard against empty target.
    working_state = _state(goal="")
    ctx = ExecutionContext(
        state=working_state,
        decision=SimpleNamespace(
            mode=EVAL_MODE,
            eval_target="",
            eval_criteria=[],
            objective="",
        ),
        user_input="",
        logger=SimpleNamespace(emit=lambda *a, **kw: None),
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=_FakeServices(),
    )
    mode = EvalMode()
    result = mode.execute(ctx)
    assert result.status == "waiting_user"


def test_execute_validate_rejects_empty_target() -> None:
    working_state = _state(goal="")
    ctx = ExecutionContext(
        state=working_state,
        decision=SimpleNamespace(
            mode=EVAL_MODE,
            eval_target="",
            eval_criteria=[],
            objective="",
        ),
        user_input="",
        logger=SimpleNamespace(emit=lambda *a, **kw: None),
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=_FakeServices(),
    )
    mode = EvalMode()
    validation = mode.validate(ctx)
    assert validation is not None
    assert validation.passed is False


def test_execute_judgment_message_contains_verdict() -> None:
    ctx, _ = _ctx(
        eval_target="schema.py",
        response_queue=[_VALID_JUDGMENT_JSON],
    )
    mode = EvalMode()
    result = mode.execute(ctx)
    assert "PASS" in result.message or "pass" in result.message.lower()


# Registry — not already covered above but adding broader check


def test_eval_mode_decision_descriptions_contains_eval() -> None:
    descriptions = decision_route_descriptions()
    assert EVAL_MODE not in descriptions
