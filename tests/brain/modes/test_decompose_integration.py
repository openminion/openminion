from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.brain.bootstrap.route_catalog import (
    available_routes,
    get_route_descriptor,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.orchestrate.handler import (
    OrchestrateMode,
)
from openminion.modules.brain.execution.child_tasks import (
    DecomposePayload,
    SubtaskSpec,
)
from openminion.modules.brain.schemas import (
    ActionResult,
    ActDecision,
    AdaptiveRevisionCheckpoint,
    AgentProfile,
    BudgetCounters,
    ExecutionTargetPayload,
    ModeProfileConfig,
    RespondDecision,
    WorkingState,
    build_intent_execution_states,
    build_sub_intent_id,
)
from openminion.modules.brain.schemas.decisions import DecisionAdapter
from openminion.modules.task import TaskManager
from tests.brain.runner_test_support import _profile


def _patch_orchestrate_child_invoke(monkeypatch, fake_invoke) -> None:
    monkeypatch.setattr(
        "openminion.modules.brain.execution.orchestrate.handler.invoke_decision_direct",
        lambda runner, *, state, decision, user_input, logger, depth=0: fake_invoke(
            runner,
            state=state,
            decision=decision,
            user_input=user_input,
            logger=logger,
            depth=depth,
        ),
    )


class _FakeLLMAPI:
    def __init__(self, answer: str = "synthesized summary") -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

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
        return {"answer": self.answer}


class _FakeSessionAPI:
    def has_pending_user_input(self, *args, **kwargs) -> bool:
        del args, kwargs
        return False


@dataclass
class _FakeRunner:
    profile: AgentProfile
    llm_api: _FakeLLMAPI
    decisions: list[Any]
    session_api: _FakeSessionAPI = _FakeSessionAPI()
    task_manager: TaskManager = field(
        default_factory=lambda: TaskManager.for_lifecycle_db(db_path=":memory:")
    )

    def _decide(self, *, state, user_input, logger):
        del state, user_input, logger
        if not self.decisions:
            return ActDecision(
                confidence=0.7,
                reason_code="default_child_act",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["fallback"],
            )
        return self.decisions.pop(0)


@dataclass
class _FakeServices:
    runner: _FakeRunner
    statuses: list[dict[str, Any]]

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
        if action_result is not None:
            state.last_result = action_result
        return SimpleNamespace(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
        )

    def direct_response(self, *, user_input, decision):
        del user_input, decision
        return ""

    def plan(self, *, state, user_input, logger, decision=None):
        del state, user_input, logger, decision
        raise AssertionError("ctx.plan() is not expected in orchestrate handler tests")

    def approve_command(self, *, state, command, logger):
        del state, logger
        return command

    def act_command(self, *, state, command, logger):
        del state, command, logger
        raise AssertionError("act_command not used in orchestrate handler tests")

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

    def create_task(
        self,
        *,
        session_id: str,
        mode_name: str,
        goal: str,
        agent_id: str | None,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ):
        return self.runner.task_manager.create_task(
            session_id=session_id,
            mode_name=mode_name,
            goal=goal,
            agent_id=agent_id,
            metadata=metadata,
            task_id=task_id,
        )

    def get_task(self, *, task_id: str):
        return self.runner.task_manager.get_task(task_id)

    def list_open_tasks_for_session(
        self,
        *,
        session_id: str,
        mode_name: str | None = None,
        limit: int = 100,
    ):
        return self.runner.task_manager.list_open_tasks_for_session(
            session_id,
            mode_name=mode_name,
            limit=limit,
        )

    def save_checkpoint(
        self,
        *,
        task_id: str,
        checkpoint_id: str,
        state: dict[str, Any],
    ) -> None:
        self.runner.task_manager.save_checkpoint(task_id, checkpoint_id, state)

    def get_latest_checkpoint(self, *, task_id: str):
        return self.runner.task_manager.get_latest_checkpoint(task_id)

    def list_checkpoints(self, *, task_id: str):
        return self.runner.task_manager.list_checkpoints(task_id)

    def update_task_progress(self, *, task_id: str, progress: dict[str, Any]) -> None:
        self.runner.task_manager.update_progress(task_id, progress)

    def transition_task(
        self,
        *,
        task_id: str,
        to_state: str,
        failure_reason: str | None = None,
    ):
        return self.runner.task_manager.transition_task(
            task_id=task_id,
            to_state=to_state,
            failure_reason=failure_reason,
        )


def _state() -> WorkingState:
    return WorkingState(
        session_id="s-decompose",
        agent_id="router-agent",
        goal="Compare providers",
        budgets_remaining=BudgetCounters(
            ticks=12,
            tool_calls=6,
            a2a_calls=6,
            tokens=6000,
            time_ms=120000,
        ),
        trace_id="trace-decompose",
    )


def _ctx(*, subtasks: list[dict[str, Any]], decisions: list[Any] | None = None):
    runner = _FakeRunner(
        profile=_profile().model_copy(
            update={
                "mode_config": {
                    OrchestrateMode.mode_name: ModeProfileConfig(
                        max_subtasks=5, max_decompose_depth=1
                    )
                }
            }
        ),
        llm_api=_FakeLLMAPI(),
        decisions=list(decisions or []),
    )
    services = _FakeServices(runner=runner, statuses=[])
    decision = SimpleNamespace(
        mode=OrchestrateMode.mode_name,
        confidence=0.9,
        reason_code="complex_request",
        subtasks=subtasks,
        sub_intents=[],
        rationale="",
        answer="",
        question=None,
    )
    return (
        ExecutionContext(
            state=_state(),
            decision=decision,
            user_input="Compare pricing for AWS, GCP, and Azure",
            logger=SimpleNamespace(emit=lambda *args, **kwargs: None),
            options=SimpleNamespace(decompose_cancel_requested=False),
            llm_adapter=runner.llm_api,
            command_executor=SimpleNamespace(),
            _services=services,
        ),
        runner,
        services,
    )


def _mode_result(
    state: WorkingState, message: str, *, failed: bool = False
) -> ExecutionResult:
    action_result = ActionResult(
        command_id=f"cmd-{message}",
        status="failed" if failed else "success",
        summary=message,
    )
    return ExecutionResult(
        status="error" if failed else "done",
        working_state=state,
        message=message,
        action_result=action_result,
    )


def test_decompose_handler_collects_results_and_synthesizes(monkeypatch) -> None:
    ctx, runner, services = _ctx(
        subtasks=[
            {"goal": "Research AWS pricing", "suggested_mode": "act"},
            {"goal": "Research GCP pricing", "suggested_mode": "act"},
            {"goal": "Summarize differences", "suggested_mode": "respond"},
        ],
        decisions=[
            ActDecision(
                confidence=0.8,
                reason_code="aws",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["aws"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="gcp",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["gcp"],
            ),
            RespondDecision(
                respond_kind="answer",
                confidence=0.8,
                reason_code="summary",
                sub_intents=["summary"],
                answer="summary",
            ),
        ],
    )
    child_states: list[WorkingState] = []

    def _fake_invoke(runner, *, state, decision, user_input, logger, depth=0):
        del runner, user_input, logger, depth
        child_states.append(state)
        label = str(
            getattr(decision, "reason_code", "") or getattr(decision, "mode", "child")
        )
        return _mode_result(state, f"result:{label}")

    _patch_orchestrate_child_invoke(monkeypatch, _fake_invoke)

    mode = OrchestrateMode()
    result = mode.execute(ctx)

    subtask_results = result.action_result.outputs["subtask_results"]
    assert len(subtask_results) == 3
    assert len(runner.llm_api.calls) == 1
    assert result.message == "synthesized summary"
    assert child_states and all(state is not ctx.state for state in child_states)
    assert services.statuses
    assert {"start", "execute_subtask", "synthesis", "done"}.issubset(
        {item.get("mode_state") for item in services.statuses}
    )


def test_orchestrate_validation_does_not_fail_before_execution() -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {"goal": "Research AWS pricing", "suggested_mode": "act"},
            {"goal": "Research GCP pricing", "suggested_mode": "act"},
            {"goal": "Summarize differences", "suggested_mode": "respond"},
        ],
    )
    ctx.state.last_result = ActionResult(
        command_id="prior-general-act",
        status="success",
        summary="Prior act-loop result without orchestrate outputs.",
        outputs={"adaptive.termination_reason": "decompose_requested"},
    )

    validation = OrchestrateMode().validate(ctx)

    assert validation is None


def test_orchestrate_validation_fails_closed_after_orchestrate_result_loss() -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {"goal": "Research AWS pricing", "suggested_mode": "act"},
            {"goal": "Research GCP pricing", "suggested_mode": "act"},
            {"goal": "Summarize differences", "suggested_mode": "respond"},
        ],
    )
    ctx.state.active_mode_name = OrchestrateMode.mode_name
    ctx.state.last_result = ActionResult(
        command_id="orchestrate-result",
        status="success",
        summary="Synthesis without subtask result records.",
        outputs={},
    )

    validation = OrchestrateMode().validate(ctx)

    assert validation is not None
    assert validation.passed is False
    assert validation.code == "missing_subtask_results"
    assert validation.details == {"expected": 3, "actual": 0}


def test_decompose_child_state_does_not_inherit_parent_adaptive_plan_state() -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[{"goal": "Research AWS pricing", "suggested_mode": "act"}]
    )
    intent_id = build_sub_intent_id("parent research", index=1)
    ctx.state.adaptive_satisfied_intent_ids = [intent_id]
    ctx.state.last_adaptive_revision_checkpoint = AdaptiveRevisionCheckpoint(
        action="replan",
        completed_intent_ids=[intent_id],
    )
    ctx.state.decision_sub_intents = ["parent research"]
    ctx.state.decision_sub_intent_refs = [
        {"id": intent_id, "description": "parent research"}
    ]
    ctx.state.intent_execution_states = build_intent_execution_states(
        ctx.state.decision_sub_intent_refs
    )

    mode = OrchestrateMode()
    ctx.decision.subtasks = [SubtaskSpec.model_validate(ctx.decision.subtasks[0])]
    child_state = mode._build_child_state(
        parent_state=ctx.state,
        child_budget=ctx.state.budgets_remaining.model_copy(deep=True),
        subtask=ctx.decision.subtasks[0],
    )

    assert child_state.adaptive_satisfied_intent_ids == []
    assert child_state.last_adaptive_revision_checkpoint is None
    assert child_state.decision_sub_intents == []
    assert child_state.decision_sub_intent_refs == []
    assert child_state.intent_execution_states == []


def test_decompose_child_state_resets_llm_call_usage() -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[{"goal": "Research AWS pricing", "suggested_mode": "act"}]
    )
    ctx.state.llm_calls_used = 3

    mode = OrchestrateMode()
    ctx.decision.subtasks = [SubtaskSpec.model_validate(ctx.decision.subtasks[0])]
    child_state = mode._build_child_state(
        parent_state=ctx.state,
        child_budget=ctx.state.budgets_remaining.model_copy(deep=True),
        subtask=ctx.decision.subtasks[0],
    )

    assert child_state.llm_calls_used == 0


def test_orchestrate_normalizes_child_budget_floor_after_decompose_handoff(
    monkeypatch,
) -> None:
    ctx, runner, _services = _ctx(
        subtasks=[
            {"goal": f"Research slice {index}", "suggested_mode": "act"}
            for index in range(5)
        ]
    )
    ctx.state.budgets_remaining = BudgetCounters(
        ticks=1,
        tool_calls=1,
        a2a_calls=0,
        tokens=5000,
        time_ms=60000,
    )
    seen_budgets: list[tuple[int, int]] = []

    def _record_decide(*, state, user_input, logger):
        del user_input, logger
        seen_budgets.append(
            (state.budgets_remaining.ticks, state.budgets_remaining.tool_calls)
        )
        return ActDecision(
            confidence=0.8,
            reason_code="child",
            act_profile="general",
            execution_target=ExecutionTargetPayload(kind="local"),
            sub_intents=["child"],
        )

    runner._decide = _record_decide

    def _fake_invoke(runner, *, state, decision, user_input, logger, depth=0):
        del runner, decision, user_input, logger, depth
        return _mode_result(state, "child-result")

    _patch_orchestrate_child_invoke(monkeypatch, _fake_invoke)

    result = OrchestrateMode().execute(ctx)

    assert result.status == "done"
    assert len(seen_budgets) == 5
    assert all(ticks >= 1 for ticks, _tool_calls in seen_budgets)
    assert all(tool_calls >= 1 for _ticks, tool_calls in seen_budgets)


def test_decompose_handler_fails_fast_and_preserves_partial_results(
    monkeypatch,
) -> None:
    ctx, runner, _services = _ctx(
        subtasks=[
            {"goal": "Research X", "suggested_mode": "act"},
            {"goal": "Research Y", "suggested_mode": "act"},
            {"goal": "Research Z", "suggested_mode": "act"},
        ],
        decisions=[
            ActDecision(
                confidence=0.8,
                reason_code="x",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["x"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="y",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["y"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="z",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["z"],
            ),
        ],
    )
    invoked: list[str] = []

    def _fake_invoke(runner, *, state, decision, user_input, logger, depth=0):
        del runner, user_input, logger, depth
        label = str(getattr(decision, "reason_code", "") or "child")
        invoked.append(label)
        if label == "y":
            return _mode_result(state, "result:y", failed=True)
        return _mode_result(state, f"result:{label}")

    _patch_orchestrate_child_invoke(monkeypatch, _fake_invoke)

    result = OrchestrateMode().execute(ctx)
    subtask_results = result.action_result.outputs["subtask_results"]

    assert invoked == ["x", "y"]
    assert [item["status"] for item in subtask_results] == ["completed", "failed"]
    assert result.action_result.error is not None


def test_decompose_prepare_rejects_subtask_count_over_limit() -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[{"goal": f"Task {index}"} for index in range(6)]
    )

    preparation = OrchestrateMode().prepare(ctx)

    assert preparation.mode_result is not None
    assert "at most 5 subtasks" in str(preparation.mode_result.message)


def test_decompose_prepare_falls_back_from_decompose_suggested_mode() -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {"goal": "Nested", "suggested_mode": "decompose"},
            {"goal": "Other"},
        ]
    )

    preparation = OrchestrateMode().prepare(ctx)

    assert preparation.mode_result is None
    subtasks = ctx.decision.subtasks
    # "decompose" is an unknown mode — should fall back to "act"
    assert subtasks[0].suggested_mode in ("act", None, "")


def test_decompose_prepare_accepts_legacy_subtask_ids_and_drops_nested_trees() -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {
                "intent_id": "research",
                "description": "Research current travel requirements",
                "kind": "research",
            },
            {
                "id": "1",
                "goal": "Plan Tokyo days",
                "subtasks": [{"id": "1.1", "goal": "Nested detail"}],
            },
        ]
    )

    preparation = OrchestrateMode().prepare(ctx)

    assert preparation.mode_result is None
    assert [item.subtask_id for item in ctx.decision.subtasks] == ["research", "1"]
    assert [item.goal for item in ctx.decision.subtasks] == [
        "Research current travel requirements",
        "Plan Tokyo days",
    ]
    assert all(isinstance(item, SubtaskSpec) for item in ctx.decision.subtasks)


def test_decompose_payload_rejects_empty_or_single_subtask_lists() -> None:
    with pytest.raises(Exception):
        DecomposePayload(subtasks=[])
    with pytest.raises(Exception):
        DecomposePayload(subtasks=[SubtaskSpec(goal="Only one")])


def test_mode_profile_config_serializes_with_decompose_fields() -> None:
    config = ModeProfileConfig(max_subtasks=3, max_decompose_depth=1, enabled=True)
    dumped = config.model_dump(mode="python")
    loaded = ModeProfileConfig.model_validate(dumped)

    assert loaded.max_subtasks == 3
    assert loaded.max_decompose_depth == 1
    assert loaded.enabled is True


def test_orchestrate_mode_applies_shared_budget_fields() -> None:
    mode = OrchestrateMode()
    mode.apply_mode_config(
        config=ModeProfileConfig(
            parallel_enabled=True,
            parallel_writes_enabled=True,
            max_parallel_workers=4,
            max_subtasks=6,
            max_decompose_depth=2,
        ),
        runner=None,
        profile=None,
    )

    assert mode._parallel_enabled is True
    assert mode._parallel_writes_enabled is True
    assert mode._max_parallel_workers == 4
    assert mode._max_subtasks == 6
    assert mode._max_decompose_depth == 2


def test_orchestrate_registration_is_internal_only_and_schema_keeps_subtasks() -> None:
    available = available_routes()
    schema = DecisionAdapter.json_schema()

    assert available == ["act", "respond"]
    assert get_route_descriptor("orchestrate") is None
    assert "subtasks" in schema["properties"]


def test_decision_schema_compat_bridge_rewrites_plan_to_act_orchestrate() -> None:
    decision = DecisionAdapter.validate_python(
        {
            "mode": "plan",
            "confidence": 0.9,
            "reason_code": "compat_test",
            "subtasks": [{"goal": "first"}, {"goal": "second"}],
        }
    )

    assert decision.mode == "act"
    assert decision.act_profile == "orchestrate"
    assert len(decision.subtasks) == 2
