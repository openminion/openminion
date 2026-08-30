from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
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
    ORCHESTRATE_MODE,
    OrchestrateMode,
)
from openminion.modules.brain.execution.orchestrate.strategies import LLMSynthesizer
from openminion.modules.brain.execution.orchestrate.strategies import build_child_state
from openminion.modules.brain.execution.worktree_children import (
    accept_child_worktree_artifact,
    allocate_child_worktree,
    finalize_child_worktree,
    reject_child_worktree_artifact,
)
from openminion.modules.brain.execution.child_tasks import (
    DecomposePayload,
    SubtaskResult,
    SubtaskSpec,
)
from openminion.modules.brain.schemas import (
    ActionMetrics,
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
from tests.artifact.utils import artifact_ctl


def _run_git(repo, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "maer@example.invalid")
    _run_git(repo, "config", "user.name", "MAER Test")
    (repo / "seed.py").write_text("VALUE = 0\n", encoding="utf-8")
    (repo / "delete_me.txt").write_text("delete me\n", encoding="utf-8")
    (repo / "rename_me.txt").write_text("rename me\n", encoding="utf-8")
    _run_git(repo, "add", "seed.py")
    _run_git(repo, "add", "delete_me.txt")
    _run_git(repo, "add", "rename_me.txt")
    _run_git(repo, "commit", "-m", "seed")
    return repo


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
    def __init__(
        self,
        answer: str = "synthesized summary",
        failure_decisions: list[dict[str, str]] | None = None,
    ) -> None:
        self.answer = answer
        self.failure_decisions = list(failure_decisions or [])
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
        if getattr(schema, "__name__", "") == "ChildFailureDecision":
            if self.failure_decisions:
                return self.failure_decisions.pop(0)
            return {"disposition": "stop"}
        return {"answer": self.answer}


class _FakeSessionAPI:
    def has_pending_user_input(self, *args, **kwargs) -> bool:
        del args, kwargs
        return False


class _WorkspaceWritingToolAPI:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.policy = SimpleNamespace(
            raw={
                "workspace_root": str(workspace_root),
                "context_metadata": {
                    "workspace_root": str(workspace_root),
                    "cwd": str(workspace_root),
                },
            }
        )
        self.calls: list[dict[str, Any]] = []

    def execute(self, *, command, session_id: str, trace_id: str) -> dict[str, Any]:
        del session_id, trace_id
        workspace = Path(self.workspace_root)
        policy_workspace = self.policy.raw.get("workspace_root")
        metadata = self.policy.raw.get("context_metadata", {})
        value = command.get("args", {}).get("value", "tool")
        (workspace / "seed.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
        call = {
            "workspace_root": str(workspace),
            "policy_workspace_root": str(policy_workspace),
            "metadata_workspace_root": str(metadata.get("workspace_root", "")),
            "metadata_cwd": str(metadata.get("cwd", "")),
        }
        self.calls.append(call)
        return {"status": "success", "summary": f"patched:{value}", "outputs": call}


@dataclass
class _FakeRunner:
    profile: AgentProfile
    llm_api: _FakeLLMAPI
    decisions: list[Any]
    session_api: _FakeSessionAPI = _FakeSessionAPI()
    agent_registry: dict[str, dict[str, Any]] = field(default_factory=dict)
    options: Any = field(
        default_factory=lambda: SimpleNamespace(decompose_cancel_requested=False)
    )
    command_calls: list[Any] = field(default_factory=list)
    status_events: list[dict[str, Any]] = field(default_factory=list)
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

    def _emit_phase_status(self, **kwargs) -> None:
        self.status_events.append(dict(kwargs))

    def _direct_response(self, *, user_input, decision) -> str:
        del user_input
        return str(getattr(decision, "answer", "") or "").strip()

    def _act(self, *, state, command, logger):
        del state, logger
        self.command_calls.append(command)
        return (
            ActionResult(
                command_id="cmd-team-delegate",
                status="success",
                summary="team delegate marker",
                outputs={"answer": "team delegate marker"},
            ),
            None,
        )


@dataclass
class _FakeServices:
    runner: _FakeRunner
    statuses: list[dict[str, Any]]
    command_calls: list[Any] = field(default_factory=list)
    action_result: ActionResult = field(
        default_factory=lambda: ActionResult(
            command_id="cmd-team-delegate",
            status="success",
            summary="team delegate marker",
            outputs={"answer": "team delegate marker"},
        )
    )

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
        del state, logger
        self.command_calls.append(command)
        return self.action_result, None

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


def _ctx(
    *,
    subtasks: list[dict[str, Any]],
    decisions: list[Any] | None = None,
    failure_decisions: list[dict[str, str]] | None = None,
):
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
        llm_api=_FakeLLMAPI(failure_decisions=failure_decisions),
        decisions=list(decisions or []),
    )
    services = _FakeServices(runner=runner, statuses=[])
    runner.command_calls = services.command_calls
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
    state: WorkingState,
    message: str,
    *,
    failed: bool = False,
    tokens_used: int = 0,
) -> ExecutionResult:
    action_result = ActionResult(
        command_id=f"cmd-{message}",
        status="failed" if failed else "success",
        summary=message,
        metrics=ActionMetrics(tokens_used=tokens_used),
    )
    return ExecutionResult(
        status="error" if failed else "done",
        working_state=state,
        message=message,
        action_result=action_result,
    )


def test_decompose_debits_child_a2a_and_token_usage(monkeypatch) -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {"subtask_id": "a", "goal": "A", "suggested_mode": "act"},
            {"subtask_id": "b", "goal": "B", "suggested_mode": "act"},
        ],
        decisions=[
            ActDecision(
                confidence=0.8,
                reason_code=label.lower(),
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=[label.lower()],
            )
            for label in ("A", "B")
        ],
    )

    def _fake_invoke(runner, *, state, decision, user_input, logger, depth=0):
        del runner, decision, user_input, logger, depth
        state.budgets_remaining.a2a_calls -= 1
        return _mode_result(state, "child", tokens_used=781)

    _patch_orchestrate_child_invoke(monkeypatch, _fake_invoke)

    result = OrchestrateMode().execute(ctx)

    assert ctx.state.budgets_remaining.a2a_calls == 4
    assert ctx.state.budgets_remaining.tokens == 4_438
    assert result.action_result.metrics.tokens_used == 1_562


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
    policy = ctx.state.module_state["delegation_policy"]
    assert [item["flow"] for item in policy["projections"]] == [
        "orchestrate_inline",
        "orchestrate_inline",
        "orchestrate_inline",
    ]
    assert [
        item["decision"]["projected_budget"]["source_policy"]
        for item in policy["projections"]
    ] == [
        "split_fixed",
        "split_fixed",
        "split_fixed",
    ]
    assert policy["projections"][0]["decision"]["projected_budget"]["tokens"] == 2000
    aggregation = policy["aggregations"][0]["aggregation"]
    assert aggregation["source_policy"] == "structural_merge"
    assert aggregation["total_children"] == 3
    assert aggregation["success_count"] == 3
    assert aggregation["completed_required"] is True


def test_decompose_synthesis_receives_child_artifact() -> None:
    ctx, runner, _services = _ctx(subtasks=[])

    LLMSynthesizer().synthesize(
        ctx=ctx,
        results=[
            SubtaskResult(
                subtask_id="implement",
                goal="Implement the approved change",
                status="completed",
                mode_used="act",
                output="Implementation ready",
                child_artifact={
                    "status": "completed",
                    "integration_status": "pending_parent_review",
                    "workspace": "/private/child-worktree",
                    "diff": "secret diff" * 1_000,
                    "touched_paths": ["src/change.py"],
                    "artifact": {
                        "status": "stored",
                        "manifest_ref": "artifact://manifest-781",
                        "bundle_sha256": "abc781",
                    },
                },
            )
        ],
    )

    synthesized_child = runner.llm_api.calls[-1]["context"]["subtasks"][0]
    assert synthesized_child["child_artifact"] == {
        "status": "completed",
        "integration_status": "pending_parent_review",
        "touched_paths": ["src/change.py"],
        "artifact": {
            "status": "stored",
            "manifest_ref": "artifact://manifest-781",
            "bundle_sha256": "abc781",
        },
    }


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
    child_state = build_child_state(
        parent_state=ctx.state,
        child_budget=ctx.state.budgets_remaining.model_copy(deep=True),
        child_context=mode._inheritance.build_child_context(
            parent_state=ctx.state,
            subtask=ctx.decision.subtasks[0],
        ),
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
    child_state = build_child_state(
        parent_state=ctx.state,
        child_budget=ctx.state.budgets_remaining.model_copy(deep=True),
        child_context=mode._inheritance.build_child_context(
            parent_state=ctx.state,
            subtask=ctx.decision.subtasks[0],
        ),
    )

    assert child_state.llm_calls_used == 0


def test_decompose_children_receive_distinct_turn_scopes() -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[{"goal": "Research AWS pricing", "suggested_mode": "act"}]
    )
    parent_trace_id = ctx.state.trace_id
    mode = OrchestrateMode()
    subtask = SubtaskSpec.model_validate(ctx.decision.subtasks[0])
    child_context = mode._inheritance.build_child_context(
        parent_state=ctx.state,
        subtask=subtask,
    )

    first = build_child_state(
        parent_state=ctx.state,
        child_budget=ctx.state.budgets_remaining.model_copy(deep=True),
        child_context=child_context,
    )
    second = build_child_state(
        parent_state=ctx.state,
        child_budget=ctx.state.budgets_remaining.model_copy(deep=True),
        child_context=child_context,
    )

    assert first.trace_id != parent_trace_id
    assert second.trace_id != parent_trace_id
    assert first.trace_id != second.trace_id
    assert ctx.state.trace_id == parent_trace_id


def test_orchestrate_rejects_recursive_child_decision() -> None:
    ctx, runner, _services = _ctx(
        subtasks=[
            {"goal": "Research AWS pricing", "suggested_mode": "act"},
            {"goal": "Research GCP pricing", "suggested_mode": "act"},
        ]
    )
    runner._decide = lambda **_kwargs: SimpleNamespace(mode=ORCHESTRATE_MODE)

    with pytest.raises(ValueError, match="cannot recursively select orchestrate"):
        OrchestrateMode().execute(ctx)


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
    aggregation = ctx.state.module_state["delegation_policy"]["aggregations"][0][
        "aggregation"
    ]
    assert aggregation["total_children"] == 2
    assert aggregation["success_count"] == 1
    assert aggregation["failure_count"] == 1
    assert aggregation["completed_required"] is False


def test_orchestrate_final_child_cancellation_is_not_success(monkeypatch) -> None:
    ctx, _runner, services = _ctx(
        subtasks=[
            {"subtask_id": "first", "goal": "First", "suggested_mode": "act"},
            {"subtask_id": "second", "goal": "Second", "suggested_mode": "act"},
        ],
        decisions=[
            ActDecision(
                confidence=0.8,
                reason_code="first",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["first"],
            )
        ],
    )

    def _fake_invoke(runner, *, state, decision, user_input, logger, depth=0):
        del runner, decision, user_input, logger, depth
        return _mode_result(state, "first complete")

    _patch_orchestrate_child_invoke(monkeypatch, _fake_invoke)
    mode = OrchestrateMode(
        cancellation_policy=SimpleNamespace(
            should_cancel=lambda *, ctx, results, attempts: attempts == 2
        )
    )

    result = mode.execute(ctx)
    validation = mode.validate(ctx)

    assert result.status == "failed"
    assert result.action_result.status == "failed"
    assert [
        item["status"] for item in result.action_result.outputs["subtask_results"]
    ] == ["completed", "cancelled"]
    assert validation is not None
    assert validation.passed is False
    assert validation.code == "orchestrate_subtask_failed"
    assert services.statuses[-1]["mode_state"] == "failed"
    assert "failed: 1/2" in services.statuses[-1]["mode_label"]


@pytest.mark.parametrize(
    ("disposition", "expected_invocations", "expected_statuses"),
    [
        ("continue", ["x", "y"], ["failed", "completed"]),
        ("retry_once", ["x", "retry", "y"], ["completed", "completed"]),
        ("stop", ["x"], ["failed"]),
    ],
)
def test_orchestrate_child_failure_dispositions_are_bounded(
    monkeypatch,
    disposition: str,
    expected_invocations: list[str],
    expected_statuses: list[str],
) -> None:
    reasons = {
        "continue": ["x", "y"],
        "retry_once": ["x", "retry", "y"],
        "stop": ["x"],
    }[disposition]
    decisions = [
        ActDecision(
            confidence=0.8,
            reason_code=reason,
            act_profile="general",
            execution_target=ExecutionTargetPayload(kind="local"),
            sub_intents=[reason],
        )
        for reason in reasons
    ]
    ctx, runner, _services = _ctx(
        subtasks=[
            {"subtask_id": "x", "goal": "Research X", "suggested_mode": "act"},
            {"subtask_id": "y", "goal": "Research Y", "suggested_mode": "act"},
        ],
        decisions=decisions,
        failure_decisions=[{"disposition": disposition}],
    )
    invoked: list[str] = []

    def _fake_invoke(runner, *, state, decision, user_input, logger, depth=0):
        del runner, user_input, logger, depth
        label = str(getattr(decision, "reason_code", "") or "child")
        invoked.append(label)
        return _mode_result(state, label, failed=label == "x")

    _patch_orchestrate_child_invoke(monkeypatch, _fake_invoke)

    result = OrchestrateMode().execute(ctx)

    assert invoked == expected_invocations
    assert [
        item["status"] for item in result.action_result.outputs["subtask_results"]
    ] == expected_statuses
    recovery = result.action_result.outputs["child_recovery"]
    assert recovery["disposition"] == disposition
    assert (
        sum(call["schema"] == "ChildFailureDecision" for call in runner.llm_api.calls)
        == 1
    )


def test_orchestrate_reassigns_failed_child_to_one_exact_target(monkeypatch) -> None:
    ctx, runner, _services = _ctx(
        subtasks=[
            {"subtask_id": "x", "goal": "Research X", "suggested_mode": "act"},
            {"subtask_id": "y", "goal": "Research Y", "suggested_mode": "act"},
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
        ],
        failure_decisions=[
            {"disposition": "reassign_exact", "target_agent_id": "agent.research"}
        ],
    )
    runner.agent_registry = {"agent.research": {"state": "healthy"}}
    invoked: list[str] = []

    def _fake_invoke(runner, *, state, decision, user_input, logger, depth=0):
        del runner, user_input, logger, depth
        target = str(getattr(decision, "target_agent_id", "") or "")
        label = target or str(getattr(decision, "reason_code", "") or "child")
        invoked.append(label)
        return _mode_result(state, label, failed=label == "x")

    _patch_orchestrate_child_invoke(monkeypatch, _fake_invoke)

    result = OrchestrateMode().execute(ctx)

    assert invoked == ["x", "agent.research", "y"]
    assert result.action_result.outputs["child_recovery"] == {
        "disposition": "reassign_exact",
        "failed_subtask_id": "x",
        "target_agent_id": "agent.research",
        "outcome": "completed",
    }
    recovery_call = next(
        call
        for call in runner.llm_api.calls
        if call["schema"] == "ChildFailureDecision"
    )
    recovery_facts = json.loads(recovery_call["context"]["messages"][1]["content"])
    assert recovery_facts["available_agent_ids"] == ["agent.research"]
    assert recovery_facts["failed_subtask"]["subtask_id"] == "x"


def test_orchestrate_exact_delegate_assignment_runs_existing_delegate_path() -> None:
    ctx, runner, services = _ctx(
        subtasks=[
            {
                "subtask_id": "weather-child",
                "goal": "Ask weather specialist",
                "suggested_mode": "execution_target_delegated",
                "inputs": {
                    "target_agent_id": "agent.weather",
                    "goal": "Return a target marker.",
                    "constraints": "Use the exact marker.",
                },
            },
            {"subtask_id": "summary", "goal": "Summarize", "suggested_mode": "respond"},
        ],
        decisions=[
            RespondDecision(
                respond_kind="answer",
                confidence=0.8,
                reason_code="summary",
                sub_intents=["summary"],
                answer="summary",
            ),
        ],
    )
    runner.agent_registry = {"agent.weather": {"state": "healthy"}}

    result = OrchestrateMode().execute(ctx)

    assert result.status == "done"
    assert len(services.command_calls) == 1
    command = services.command_calls[0]
    assert command.target_agent_id == "agent.weather"
    assert command.params["goal"] == "Return a target marker."
    assert command.params["constraints"] == ["Use the exact marker."]
    subtask_results = result.action_result.outputs["subtask_results"]
    assert subtask_results[0]["status"] == "completed"
    assert subtask_results[0]["output"] == "team delegate marker"
    policy = ctx.state.module_state["delegation_policy"]
    assert [item["flow"] for item in policy["projections"][:2]] == [
        "orchestrate_inline",
        "a2a_sync",
    ]
    assert policy["aggregations"][-1]["aggregation"]["success_count"] == 2


def test_orchestrate_unknown_delegate_assignment_fails_structurally() -> None:
    ctx, _runner, services = _ctx(
        subtasks=[
            {
                "subtask_id": "missing-child",
                "goal": "Ask missing specialist",
                "suggested_mode": "execution_target_delegated",
                "inputs": {"target_agent_id": "agent.missing"},
            },
            {"subtask_id": "summary", "goal": "Summarize", "suggested_mode": "respond"},
        ]
    )

    result = OrchestrateMode().execute(ctx)

    assert result.status == "failed"
    assert result.action_result.status == "failed"
    assert services.command_calls == []
    subtask_results = result.action_result.outputs["subtask_results"]
    assert subtask_results == [
        {
            "subtask_id": "missing-child",
            "goal": "Ask missing specialist",
            "status": "failed",
            "mode_used": "execution_target_delegated",
            "output": "Unknown delegate target agent: agent.missing",
            "error": "Unknown delegate target agent: agent.missing",
            "tokens_used": 0,
        }
    ]
    validation = OrchestrateMode().validate(ctx)
    assert validation is not None
    assert validation.passed is False


def test_orchestrate_code_children_use_isolated_worktrees_and_report_conflict(
    tmp_path,
    monkeypatch,
) -> None:
    repo = _git_repo(tmp_path)
    ctx, runner, _services = _ctx(
        subtasks=[
            {
                "subtask_id": "patch-a",
                "goal": "Patch seed A",
                "suggested_mode": "act",
                "inputs": {"code_bearing": True, "workspace_root": str(repo)},
            },
            {
                "subtask_id": "patch-b",
                "goal": "Patch seed B",
                "suggested_mode": "act",
                "inputs": {"code_bearing": True, "workspace_root": str(repo)},
            },
        ],
        decisions=[
            ActDecision(
                confidence=0.8,
                reason_code="patch_a",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["patch-a"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="patch_b",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["patch-b"],
            ),
        ],
    )
    tool_api = _WorkspaceWritingToolAPI(repo)
    runner.tool_api = tool_api

    def _fake_invoke(runner, *, state, decision, user_input, logger, depth=0):
        del user_input, logger, depth
        value = 1 if getattr(decision, "reason_code", "") == "patch_a" else 2
        runner.tool_api.execute(
            command={"tool_name": "file.write", "args": {"value": value}},
            session_id="s-decompose",
            trace_id="trace-decompose",
        )
        return _mode_result(state, f"patched:{value}")

    _patch_orchestrate_child_invoke(monkeypatch, _fake_invoke)

    with artifact_ctl(tmp_path / ".openminion") as ctl:
        runner.artifactctl = ctl
        result = OrchestrateMode().execute(ctx)

    assert result.status == "done"
    seen_worktrees = [call["workspace_root"] for call in tool_api.calls]
    assert len(set(seen_worktrees)) == 2
    assert all(
        call["workspace_root"] == call["policy_workspace_root"]
        for call in tool_api.calls
    )
    assert all(
        call["workspace_root"] == call["metadata_workspace_root"]
        for call in tool_api.calls
    )
    assert all(
        call["workspace_root"] == call["metadata_cwd"] for call in tool_api.calls
    )
    assert tool_api.workspace_root == repo
    assert tool_api.policy.raw["workspace_root"] == str(repo)
    assert (repo / "seed.py").read_text(encoding="utf-8") == "VALUE = 0\n"
    bucket = ctx.state.module_state["worktree_children"]
    assert [child["subtask_id"] for child in bucket["children"]] == [
        "patch-a",
        "patch-b",
    ]
    assert all(child["touched_paths"] == ["seed.py"] for child in bucket["children"])
    assert all(child["cleaned_up"] is True for child in bucket["children"])
    assert all(not Path(path).exists() for path in seen_worktrees)
    assert bucket["conflicts"] == [
        {"path": "seed.py", "subtask_ids": ["patch-a", "patch-b"]}
    ]
    public_results = result.action_result.outputs["subtask_results"]
    assert [item["child_artifact"]["subtask_id"] for item in public_results] == [
        "patch-a",
        "patch-b",
    ]
    assert all(
        item["child_artifact"]["artifact"]["status"] == "stored"
        for item in public_results
    )


def test_child_worktree_artifact_accept_applies_complete_change_set(tmp_path) -> None:
    repo = _git_repo(tmp_path)
    ctx, runner, _services = _ctx(
        subtasks=[
            {
                "subtask_id": "artifact-child",
                "goal": "Create durable artifact",
                "suggested_mode": "act",
                "inputs": {"code_bearing": True, "workspace_root": str(repo)},
            }
        ]
    )
    subtask = SubtaskSpec.model_validate(ctx.decision.subtasks[0])
    child_state = _state()
    with artifact_ctl(tmp_path / ".openminion") as ctl:
        runner.artifactctl = ctl
        lease = allocate_child_worktree(subtask=subtask, child_state=child_state)
        assert lease is not None
        (lease.worktree / "seed.py").write_text("VALUE = 9\n", encoding="utf-8")
        (lease.worktree / "new.txt").write_text("new file\n", encoding="utf-8")
        (lease.worktree / "image.bin").write_bytes(b"\x00\x01openminion")
        (lease.worktree / "delete_me.txt").unlink()
        (lease.worktree / "rename_me.txt").rename(lease.worktree / "renamed.txt")

        finalize_child_worktree(ctx, lease=lease, status="done")

        record = ctx.state.module_state["worktree_children"]["children"][0]
        artifact = record["artifact"]
        assert artifact["status"] == "stored"
        assert artifact["bundle_ref"].startswith("artifact://sha256/")
        assert artifact["manifest_ref"].startswith("artifact://sha256/")
        assert set(record["touched_paths"]) == {
            "delete_me.txt",
            "image.bin",
            "new.txt",
            "renamed.txt",
            "rename_me.txt",
            "seed.py",
        }
        assert not Path(record["workspace"]).exists()
        assert (repo / "seed.py").read_text(encoding="utf-8") == "VALUE = 0\n"

        accepted = accept_child_worktree_artifact(
            repo_root=repo, record=record, artifactctl=ctl
        )

        assert accepted == {
            "ok": True,
            "status": "accepted",
            "touched_paths": record["touched_paths"],
        }
        assert record["integration_status"] == "accepted"
        assert (repo / "seed.py").read_text(encoding="utf-8") == "VALUE = 9\n"
        assert (repo / "new.txt").read_text(encoding="utf-8") == "new file\n"
        assert (repo / "image.bin").read_bytes() == b"\x00\x01openminion"
        assert not (repo / "delete_me.txt").exists()
        assert not (repo / "rename_me.txt").exists()
        assert (repo / "renamed.txt").read_text(encoding="utf-8") == "rename me\n"


def test_child_worktree_artifact_reject_leaves_parent_unchanged(tmp_path) -> None:
    repo = _git_repo(tmp_path)
    ctx, runner, _services = _ctx(
        subtasks=[
            {
                "subtask_id": "reject-child",
                "goal": "Reject artifact",
                "suggested_mode": "act",
                "inputs": {"code_bearing": True, "workspace_root": str(repo)},
            }
        ]
    )
    subtask = SubtaskSpec.model_validate(ctx.decision.subtasks[0])
    child_state = _state()
    with artifact_ctl(tmp_path / ".openminion") as ctl:
        runner.artifactctl = ctl
        lease = allocate_child_worktree(subtask=subtask, child_state=child_state)
        assert lease is not None
        (lease.worktree / "seed.py").write_text("VALUE = 5\n", encoding="utf-8")

        finalize_child_worktree(ctx, lease=lease, status="done")
        record = ctx.state.module_state["worktree_children"]["children"][0]
        rejected = reject_child_worktree_artifact(record=record, artifactctl=ctl)

    assert rejected == {"ok": True, "status": "rejected"}
    assert record["integration_status"] == "rejected"
    assert (repo / "seed.py").read_text(encoding="utf-8") == "VALUE = 0\n"


def test_child_worktree_accept_blocks_stale_base_and_dirty_paths(tmp_path) -> None:
    repo = _git_repo(tmp_path)
    ctx, runner, _services = _ctx(
        subtasks=[
            {
                "subtask_id": "blocked-child",
                "goal": "Blocked artifact",
                "suggested_mode": "act",
                "inputs": {"code_bearing": True, "workspace_root": str(repo)},
            }
        ]
    )
    subtask = SubtaskSpec.model_validate(ctx.decision.subtasks[0])
    child_state = _state()
    with artifact_ctl(tmp_path / ".openminion") as ctl:
        runner.artifactctl = ctl
        lease = allocate_child_worktree(subtask=subtask, child_state=child_state)
        assert lease is not None
        (lease.worktree / "seed.py").write_text("VALUE = 7\n", encoding="utf-8")
        finalize_child_worktree(ctx, lease=lease, status="done")
        record = ctx.state.module_state["worktree_children"]["children"][0]

        (repo / "seed.py").write_text("dirty\n", encoding="utf-8")
        dirty = accept_child_worktree_artifact(
            repo_root=repo, record=record, artifactctl=ctl
        )
        assert dirty["status"] == "dirty_affected_paths"
        _run_git(repo, "checkout", "--", "seed.py")
        (repo / "other.txt").write_text("parent commit\n", encoding="utf-8")
        _run_git(repo, "add", "other.txt")
        _run_git(repo, "commit", "-m", "parent moved")

        stale = accept_child_worktree_artifact(
            repo_root=repo, record=record, artifactctl=ctl
        )

    assert stale["status"] == "stale_base"
    assert (repo / "seed.py").read_text(encoding="utf-8") == "VALUE = 0\n"


def test_orchestrate_read_only_child_does_not_allocate_worktree(monkeypatch) -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {"subtask_id": "read-a", "goal": "Read A", "suggested_mode": "act"},
            {"subtask_id": "read-b", "goal": "Read B", "suggested_mode": "respond"},
        ],
        decisions=[
            ActDecision(
                confidence=0.8,
                reason_code="read_a",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["read-a"],
            ),
            RespondDecision(
                respond_kind="answer",
                confidence=0.8,
                reason_code="read_b",
                sub_intents=["read-b"],
                answer="read",
            ),
        ],
    )

    def _fake_invoke(runner, *, state, decision, user_input, logger, depth=0):
        del runner, decision, user_input, logger, depth
        assert "worktree_children" not in state.module_state
        return _mode_result(state, "read-only")

    _patch_orchestrate_child_invoke(monkeypatch, _fake_invoke)

    result = OrchestrateMode().execute(ctx)

    assert result.status == "done"
    assert "worktree_children" not in ctx.state.module_state


def test_decompose_prepare_rejects_subtask_count_over_limit() -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[{"goal": f"Task {index}"} for index in range(6)]
    )

    preparation = OrchestrateMode().prepare(ctx)

    assert preparation.mode_result is not None
    assert "at most 5 subtasks" in str(preparation.mode_result.message)


def test_decompose_prepare_rejects_unrepresentable_dependency_fan_in() -> None:
    predecessors = [
        {"subtask_id": f"dependency-{index}", "goal": f"Task {index}"}
        for index in range(19)
    ]
    ctx, _runner, _services = _ctx(
        subtasks=[
            *predecessors,
            {
                "subtask_id": "final",
                "goal": "Combine dependencies",
                "depends_on": [item["subtask_id"] for item in predecessors],
            },
        ]
    )
    mode = OrchestrateMode()
    mode._max_subtasks = 20

    preparation = mode.prepare(ctx)

    assert preparation.mode_result is not None
    assert "dependency identifiers exceed" in str(preparation.mode_result.message)
    assert ctx.state.child_task_order == []


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
