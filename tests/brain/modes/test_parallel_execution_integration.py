from __future__ import annotations

import time

from openminion.modules.brain.execution.orchestrate.handler import (
    OrchestrateMode,
)
from openminion.modules.brain.execution.orchestrate.parallel import (
    ConservativeSideEffectPolicy,
    ContinueOnErrorPolicy,
    DefaultConcurrencyPolicy,
    EvenSplitBudgetAllocator,
    ParallelExecutionStrategy,
)
from openminion.modules.brain.schemas import ActDecision, ExecutionTargetPayload
from .test_decompose_integration import _ctx, _mode_result


def test_parallel_execution_strategy_runs_independent_subtasks_concurrently(
    monkeypatch,
) -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {"subtask_id": "a", "goal": "Alpha", "suggested_mode": "act"},
            {"subtask_id": "b", "goal": "Beta", "suggested_mode": "act"},
            {"subtask_id": "c", "goal": "Gamma", "suggested_mode": "act"},
        ],
        decisions=[
            ActDecision(
                confidence=0.8,
                reason_code="plan-a",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["a"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="plan-b",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["b"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="plan-c",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["c"],
            ),
        ],
    )
    ctx._services.runner.llm_api.answer = "parallel summary"

    def _fake_invoke(self, *, state, decision, user_input, logger, depth=0):
        del self, state, decision, logger, depth
        time.sleep(0.05)
        return _mode_result(ctx.state, f"done:{user_input}")

    monkeypatch.setattr(
        "openminion.modules.brain.execution.orchestrate.handler.invoke_decision_direct",
        lambda runner, *, state, decision, user_input, logger, depth=0: _fake_invoke(
            None,
            state=state,
            decision=decision,
            user_input=user_input,
            logger=logger,
            depth=depth,
        ),
    )

    mode = OrchestrateMode(
        strategy=ParallelExecutionStrategy(),
        allocator=EvenSplitBudgetAllocator(),
    )
    started = time.monotonic()
    result = mode.execute(ctx)
    elapsed = time.monotonic() - started

    assert elapsed < 0.20
    assert result.message == "parallel summary"
    assert [
        item["subtask_id"] for item in result.action_result.outputs["subtask_results"]
    ] == [
        "a",
        "b",
        "c",
    ]


def test_parallel_execution_strategy_respects_dependency_levels(monkeypatch) -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {"subtask_id": "a", "goal": "A", "suggested_mode": "act"},
            {"subtask_id": "b", "goal": "B", "suggested_mode": "act"},
            {
                "subtask_id": "c",
                "goal": "C",
                "suggested_mode": "act",
                "depends_on": ["a"],
            },
            {
                "subtask_id": "d",
                "goal": "D",
                "suggested_mode": "act",
                "depends_on": ["b"],
            },
        ],
        decisions=[
            ActDecision(
                confidence=0.8,
                reason_code="a",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["a"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="b",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["b"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="c",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["c"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="d",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["d"],
            ),
        ],
    )
    ctx._services.runner.llm_api.answer = "dependency summary"
    starts: dict[str, float] = {}
    ends: dict[str, float] = {}

    def _fake_invoke(self, *, state, decision, user_input, logger, depth=0):
        del self, state, decision, logger, depth
        label = user_input.split("Subtask goal: ", 1)[1].strip()
        starts[label] = time.monotonic()
        time.sleep(0.05)
        ends[label] = time.monotonic()
        return _mode_result(ctx.state, label)

    monkeypatch.setattr(
        "openminion.modules.brain.execution.orchestrate.handler.invoke_decision_direct",
        lambda runner, *, state, decision, user_input, logger, depth=0: _fake_invoke(
            None,
            state=state,
            decision=decision,
            user_input=user_input,
            logger=logger,
            depth=depth,
        ),
    )

    mode = OrchestrateMode(
        strategy=ParallelExecutionStrategy(),
        allocator=EvenSplitBudgetAllocator(),
    )
    started = time.monotonic()
    result = mode.execute(ctx)
    elapsed = time.monotonic() - started

    assert elapsed < 0.25
    assert starts["C"] >= ends["A"]
    assert starts["D"] >= ends["B"]
    assert [
        item["subtask_id"] for item in result.action_result.outputs["subtask_results"]
    ] == [
        "a",
        "b",
        "c",
        "d",
    ]


def test_parallel_execution_strategy_falls_back_to_sequential_when_unsafe(
    monkeypatch,
) -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {
                "subtask_id": "write",
                "goal": "Write file",
                "suggested_mode": "act",
            },
            {"subtask_id": "reply", "goal": "Reply", "suggested_mode": "respond"},
        ],
        decisions=[
            ActDecision(
                confidence=0.8,
                reason_code="write",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["write"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="reply",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["reply"],
            ),
        ],
    )
    ctx._services.runner.llm_api.answer = "unsafe summary"
    starts: dict[str, float] = {}
    ends: dict[str, float] = {}

    def _fake_invoke(self, *, state, decision, user_input, logger, depth=0):
        del self, state, decision, logger, depth
        label = user_input.split("Subtask goal: ", 1)[1].strip()
        starts[label] = time.monotonic()
        time.sleep(0.04)
        ends[label] = time.monotonic()
        return _mode_result(ctx.state, label)

    monkeypatch.setattr(
        "openminion.modules.brain.execution.orchestrate.handler.invoke_decision_direct",
        lambda runner, *, state, decision, user_input, logger, depth=0: _fake_invoke(
            None,
            state=state,
            decision=decision,
            user_input=user_input,
            logger=logger,
            depth=depth,
        ),
    )

    mode = OrchestrateMode(
        strategy=ParallelExecutionStrategy(
            concurrency_policy=DefaultConcurrencyPolicy(
                max_workers_config=3, enabled=True
            ),
            side_effect_policy=ConservativeSideEffectPolicy(
                parallel_writes_enabled=False
            ),
        ),
        allocator=EvenSplitBudgetAllocator(),
    )
    mode.execute(ctx)

    assert starts["Reply"] >= ends["Write file"]


def test_parallel_execution_strategy_fail_fast_aborts_after_failure(
    monkeypatch,
) -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {"subtask_id": "a", "goal": "A", "suggested_mode": "act"},
            {"subtask_id": "b", "goal": "B", "suggested_mode": "act"},
            {"subtask_id": "c", "goal": "C", "suggested_mode": "act"},
        ],
        decisions=[
            ActDecision(
                confidence=0.8,
                reason_code="a",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["a"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="b",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["b"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="c",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["c"],
            ),
        ],
    )
    ctx._services.runner.llm_api.answer = "partial summary"

    def _fake_invoke(self, *, state, decision, user_input, logger, depth=0):
        del self, state, decision, logger, depth
        if user_input.endswith("B"):
            return _mode_result(ctx.state, "boom", failed=True)
        time.sleep(0.03)
        return _mode_result(ctx.state, user_input)

    monkeypatch.setattr(
        "openminion.modules.brain.execution.orchestrate.handler.invoke_decision_direct",
        lambda runner, *, state, decision, user_input, logger, depth=0: _fake_invoke(
            None,
            state=state,
            decision=decision,
            user_input=user_input,
            logger=logger,
            depth=depth,
        ),
    )

    mode = OrchestrateMode(
        strategy=ParallelExecutionStrategy(),
        allocator=EvenSplitBudgetAllocator(),
    )
    result = mode.execute(ctx)

    subtask_results = result.action_result.outputs["subtask_results"]
    failed = [item for item in subtask_results if item["status"] == "failed"]
    # B fails; fail-fast policy ensures at least one failure is recorded.
    assert failed
    # At least one failure should be B's direct failure or a sibling cancellation.
    assert any(
        item["subtask_id"] == "b" and item["status"] == "failed"
        for item in subtask_results
    )


def test_parallel_execution_strategy_continue_on_error_keeps_all_results(
    monkeypatch,
) -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {"subtask_id": "a", "goal": "A", "suggested_mode": "act"},
            {"subtask_id": "b", "goal": "B", "suggested_mode": "act"},
            {"subtask_id": "c", "goal": "C", "suggested_mode": "act"},
        ],
        decisions=[
            ActDecision(
                confidence=0.8,
                reason_code="a",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["a"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="b",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["b"],
            ),
            ActDecision(
                confidence=0.8,
                reason_code="c",
                act_profile="general",
                execution_target=ExecutionTargetPayload(kind="local"),
                sub_intents=["c"],
            ),
        ],
    )
    ctx._services.runner.llm_api.answer = "complete summary"

    def _fake_invoke(self, *, state, decision, user_input, logger, depth=0):
        del self, state, decision, logger, depth
        if user_input.endswith("B"):
            return _mode_result(ctx.state, "boom", failed=True)
        time.sleep(0.03)
        return _mode_result(ctx.state, user_input)

    monkeypatch.setattr(
        "openminion.modules.brain.execution.orchestrate.handler.invoke_decision_direct",
        lambda runner, *, state, decision, user_input, logger, depth=0: _fake_invoke(
            None,
            state=state,
            decision=decision,
            user_input=user_input,
            logger=logger,
            depth=depth,
        ),
    )

    mode = OrchestrateMode(
        strategy=ParallelExecutionStrategy(),
        allocator=EvenSplitBudgetAllocator(),
        failure_policy=ContinueOnErrorPolicy(),
    )
    result = mode.execute(ctx)

    subtask_results = result.action_result.outputs["subtask_results"]
    assert len(subtask_results) == 3
    assert [item["subtask_id"] for item in subtask_results] == ["a", "b", "c"]
    assert len([item for item in subtask_results if item["status"] == "failed"]) == 1


def test_parallel_execution_strategy_rejects_cyclic_dependencies() -> None:
    ctx, _runner, _services = _ctx(
        subtasks=[
            {
                "subtask_id": "a",
                "goal": "A",
                "suggested_mode": "act",
                "depends_on": ["b"],
            },
            {
                "subtask_id": "b",
                "goal": "B",
                "suggested_mode": "act",
                "depends_on": ["a"],
            },
        ]
    )

    preparation = OrchestrateMode(
        strategy=ParallelExecutionStrategy(),
        allocator=EvenSplitBudgetAllocator(),
    ).prepare(ctx)

    assert preparation.mode_result is not None
    assert "cyclic" in preparation.mode_result.message.lower()
