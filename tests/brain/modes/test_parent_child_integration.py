from __future__ import annotations

from openminion.modules.brain.constants import BRAIN_INTERNAL_MODE_ACT_RESEARCH
from openminion.modules.brain.execution.orchestrate.handler import (
    OrchestrateMode,
)
from openminion.modules.brain.execution.orchestrate.strategies import (
    AllInlinePromoter,
    HeuristicPromoter,
)
from openminion.modules.brain.schemas import (
    ActDecision,
    ExecutionTargetPayload,
    RespondDecision,
)
from .test_decompose_integration import _ctx, _mode_result


def _patch_invoke(monkeypatch, callback) -> None:
    monkeypatch.setattr(
        "openminion.modules.brain.execution.orchestrate.handler.invoke_decision_direct",
        lambda runner, *, state, decision, user_input, logger, depth=0: callback(
            state=state,
            decision=decision,
            user_input=user_input,
            logger=logger,
            depth=depth,
        ),
    )


def test_decompose_all_inline_promoter_preserves_inline_behavior(monkeypatch) -> None:
    ctx, runner, _services = _ctx(
        subtasks=[
            {"goal": "Research AWS pricing", "suggested_mode": "act"},
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
            RespondDecision(
                respond_kind="answer",
                confidence=0.8,
                reason_code="summary",
                sub_intents=["summary"],
                answer="summary",
            ),
        ],
    )
    invoked: list[str] = []

    def _fake_invoke(*, state, decision, user_input, logger, depth=0):
        del state, decision, logger, depth
        invoked.append(user_input)
        return _mode_result(ctx.state, f"result:{user_input}")

    _patch_invoke(monkeypatch, _fake_invoke)

    result = OrchestrateMode(promoter=AllInlinePromoter()).execute(ctx)

    assert result.status == "done"
    assert len(invoked) == 2
    assert ctx.state.child_tasks == {
        "subtask-1": "inline",
        "subtask-2": "inline",
    }
    assert runner.task_manager.list_open_tasks_for_session(ctx.state.session_id) == []


def test_decompose_promotes_research_child_and_collects_result(monkeypatch) -> None:
    ctx, runner, _services = _ctx(
        subtasks=[
            {
                "goal": "Research quantum computing",
                "suggested_mode": BRAIN_INTERNAL_MODE_ACT_RESEARCH,
            },
            {"goal": "Write executive summary", "suggested_mode": "respond"},
        ],
        decisions=[
            ActDecision(
                confidence=0.8,
                reason_code="research",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["research"],
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

    def _fake_invoke(*, state, decision, user_input, logger, depth=0):
        del logger, depth
        label = str(getattr(decision, "reason_code", "") or "child")
        return _mode_result(state, f"child:{label}:{user_input}")

    _patch_invoke(monkeypatch, _fake_invoke)
    ctx._services.runner.llm_api.answer = "Research plus summary"

    result = OrchestrateMode(promoter=HeuristicPromoter()).execute(ctx)

    child_tasks = dict(ctx.state.child_tasks)
    promoted_task_id = child_tasks["subtask-1"]
    assert promoted_task_id != "inline"
    assert child_tasks["subtask-2"] == "inline"
    record = runner.task_manager.get_task(promoted_task_id)
    assert record is not None
    assert str(record.state) == "done"
    assert record.metadata["mode_name"] == BRAIN_INTERNAL_MODE_ACT_RESEARCH
    assert record.metadata["progress"]["child_task_result"]["subtask_id"] == "subtask-1"
    assert result.message == "Research plus summary"


def test_decompose_collector_preserves_mixed_inline_and_promoted_results(
    monkeypatch,
) -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {
                "goal": "Research topic",
                "suggested_mode": BRAIN_INTERNAL_MODE_ACT_RESEARCH,
            },
            {"goal": "Quick response", "suggested_mode": "respond"},
            {"goal": "Another quick response", "suggested_mode": "respond"},
        ],
        decisions=[
            ActDecision(
                confidence=0.8,
                reason_code="research",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["research"],
            ),
            RespondDecision(
                respond_kind="answer",
                confidence=0.8,
                reason_code="quick-a",
                sub_intents=["quick-a"],
                answer="quick-a",
            ),
            RespondDecision(
                respond_kind="answer",
                confidence=0.8,
                reason_code="quick-b",
                sub_intents=["quick-b"],
                answer="quick-b",
            ),
        ],
    )

    def _fake_invoke(*, state, decision, user_input, logger, depth=0):
        del logger, depth
        label = str(getattr(decision, "reason_code", "") or "child")
        return _mode_result(state, f"normalized:{label}:{user_input}")

    _patch_invoke(monkeypatch, _fake_invoke)
    ctx._services.runner.llm_api.answer = "all child results used"

    result = OrchestrateMode(promoter=HeuristicPromoter()).execute(ctx)

    subtask_results = result.action_result.outputs["subtask_results"]
    assert len(subtask_results) == 3
    assert [item["subtask_id"] for item in subtask_results] == [
        "subtask-1",
        "subtask-2",
        "subtask-3",
    ]
    assert result.message == "all child results used"


def test_decompose_depends_on_executes_in_topological_order(monkeypatch) -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {
                "subtask_id": "report",
                "goal": "Write report",
                "suggested_mode": "respond",
                "depends_on": ["analyze"],
            },
            {
                "subtask_id": "gather",
                "goal": "Gather data",
                "suggested_mode": "act",
            },
            {
                "subtask_id": "analyze",
                "goal": "Analyze data",
                "suggested_mode": "act",
                "depends_on": ["gather"],
            },
        ],
        decisions=[
            ActDecision(
                confidence=0.8,
                reason_code="gather",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["gather"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="analyze",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["analyze"],
            ),
            RespondDecision(
                respond_kind="answer",
                confidence=0.8,
                reason_code="report",
                sub_intents=["report"],
                answer="report",
            ),
        ],
    )
    invoked: list[str] = []

    def _fake_invoke(*, state, decision, user_input, logger, depth=0):
        del state, decision, logger, depth
        invoked.append(user_input)
        return _mode_result(ctx.state, user_input)

    _patch_invoke(monkeypatch, _fake_invoke)

    OrchestrateMode().execute(ctx)

    assert ctx.state.child_task_order == ["gather", "analyze", "report"]
    assert invoked == [
        "Parent goal: Compare providers\nSubtask goal: Gather data",
        "Parent goal: Compare providers\nSubtask goal: Analyze data",
        "Parent goal: Compare providers\nSubtask goal: Write report",
    ]


def test_decompose_rejects_cyclic_depends_on_graph() -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {
                "subtask_id": "a",
                "goal": "Step A",
                "suggested_mode": "act",
                "depends_on": ["b"],
            },
            {
                "subtask_id": "b",
                "goal": "Step B",
                "suggested_mode": "act",
                "depends_on": ["a"],
            },
        ]
    )

    preparation = OrchestrateMode().prepare(ctx)

    assert preparation.mode_result is not None
    assert "cyclic depends_on graph" in preparation.mode_result.message
