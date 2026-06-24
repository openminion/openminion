from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.modules.brain.constants import (
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
)
from openminion.modules.brain.execution.child_tasks import (
    ChildTaskResult,
    ExecutionStrategy,
    FailureAction,
    SubtaskResult,
    SubtaskSpec,
)
from openminion.modules.brain.execution.orchestrate.parallel import (
    ConcurrencyPolicy,
    CyclicDependencyError,
    DependencyAnalyzer,
    ParallelBudgetAllocator,
    ParallelGroup,
    ParallelResultMerger,
    SideEffectIsolationPolicy,
)
from openminion.modules.brain.execution.orchestrate.parallel import (
    ConservativeSideEffectPolicy,
    ContinueOnErrorPolicy,
    DefaultConcurrencyPolicy,
    EvenSplitBudgetAllocator,
    OrderPreservingResultMerger,
    ParallelExecutionStrategy,
    TopologicalDependencyAnalyzer,
)
from openminion.modules.brain.execution.orchestrate.strategies import (
    FailFastPolicy,
)
from openminion.modules.brain.schemas import (
    BudgetCounters,
    ModeProfileConfig,
    WorkingState,
)


def _child_result(
    subtask_id: str, *, output: str = "", failed: bool = False
) -> ChildTaskResult:
    return ChildTaskResult(
        subtask_id=subtask_id,
        task_id=None,
        was_promoted=False,
        result=SubtaskResult(
            subtask_id=subtask_id,
            goal=subtask_id,
            status="failed" if failed else "completed",
            mode_used="act",
            output=output,
            error="boom" if failed else None,
        ),
    )


def test_parallel_contract_types_are_runtime_checkable() -> None:
    assert isinstance(TopologicalDependencyAnalyzer(), DependencyAnalyzer)
    assert isinstance(DefaultConcurrencyPolicy(), ConcurrencyPolicy)
    assert isinstance(ConservativeSideEffectPolicy(), SideEffectIsolationPolicy)
    assert isinstance(EvenSplitBudgetAllocator(), ParallelBudgetAllocator)
    assert isinstance(OrderPreservingResultMerger(), ParallelResultMerger)
    assert isinstance(ParallelExecutionStrategy(), ExecutionStrategy)
    assert isinstance(ParallelGroup(subtasks=[]), ParallelGroup)


def test_dependency_analyzer_groups_parallel_levels() -> None:
    subtasks = [
        SubtaskSpec(subtask_id="a", goal="A"),
        SubtaskSpec(subtask_id="b", goal="B"),
        SubtaskSpec(subtask_id="c", goal="C", depends_on=["a"]),
        SubtaskSpec(subtask_id="d", goal="D", depends_on=["b"]),
        SubtaskSpec(subtask_id="e", goal="E", depends_on=["c", "d"]),
    ]

    groups = TopologicalDependencyAnalyzer().analyze(subtasks)

    assert [[item.subtask_id for item in group.subtasks] for group in groups] == [
        ["a", "b"],
        ["c", "d"],
        ["e"],
    ]


def test_dependency_analyzer_rejects_cycles() -> None:
    subtasks = [
        SubtaskSpec(subtask_id="a", goal="A", depends_on=["b"]),
        SubtaskSpec(subtask_id="b", goal="B", depends_on=["a"]),
    ]

    with pytest.raises(CyclicDependencyError):
        TopologicalDependencyAnalyzer().analyze(subtasks)


def test_default_concurrency_policy_caps_worker_count() -> None:
    policy = DefaultConcurrencyPolicy(max_workers_config=3)
    assert policy.max_workers(5) == 3
    assert policy.max_workers(2) == 2
    assert (
        DefaultConcurrencyPolicy(max_workers_config=3, enabled=False).max_workers(5)
        == 1
    )


def test_conservative_side_effect_policy_blocks_write_modes_by_default() -> None:
    policy = ConservativeSideEffectPolicy()
    assert (
        policy.is_safe_to_parallelize(
            SubtaskSpec(subtask_id="r", goal="R", suggested_mode="respond")
        )
        is True
    )
    assert (
        policy.is_safe_to_parallelize(
            SubtaskSpec(subtask_id="o", goal="O", suggested_mode="act:orchestrate")
        )
        is False
    )
    assert (
        policy.is_safe_to_parallelize(
            SubtaskSpec(subtask_id="a", goal="A", suggested_mode="act")
        )
        is False
    )
    assert (
        policy.is_safe_to_parallelize(
            SubtaskSpec(
                subtask_id="d",
                goal="D",
                suggested_mode=BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
            )
        )
        is False
    )
    assert (
        ConservativeSideEffectPolicy(
            parallel_writes_enabled=True
        ).is_safe_to_parallelize(
            SubtaskSpec(subtask_id="a", goal="A", suggested_mode="act")
        )
        is True
    )


def test_even_split_budget_allocator_splits_and_returns_budget() -> None:
    allocator = EvenSplitBudgetAllocator()
    total = BudgetCounters(ticks=10, tool_calls=6, a2a_calls=5, tokens=10, time_ms=9)

    slices = allocator.allocate_slices(total, 3)

    assert [(s.ticks, s.tool_calls) for s in slices] == [(4, 2), (3, 2), (3, 2)]
    unused = allocator.return_unused(
        [
            BudgetCounters(ticks=1, tool_calls=1, a2a_calls=1, tokens=1, time_ms=1),
            BudgetCounters(ticks=2, tool_calls=0, a2a_calls=0, tokens=1, time_ms=1),
            BudgetCounters(ticks=0, tool_calls=1, a2a_calls=1, tokens=0, time_ms=1),
        ]
    )
    assert unused.ticks == 3
    assert unused.tool_calls == 2
    assert unused.a2a_calls == 2


def test_order_preserving_result_merger_reorders_out_of_order_results() -> None:
    merger = OrderPreservingResultMerger()
    merged = merger.merge(
        results={
            "b": _child_result("b", output="B"),
            "a": _child_result("a", output="A"),
            "c": _child_result("c", output="C"),
        },
        original_order=["a", "b", "c"],
    )

    assert [item.subtask_id for item in merged] == ["a", "b", "c"]
    assert [item.result.output for item in merged] == ["A", "B", "C"]


def test_continue_on_error_policy_returns_continue() -> None:
    policy = ContinueOnErrorPolicy()
    result = _child_result("x", failed=True)
    action = policy.on_failure(
        subtask=SubtaskSpec(subtask_id="x", goal="X"), result=result
    )
    assert action == FailureAction.CONTINUE
    assert (
        FailFastPolicy().on_failure(
            subtask=SubtaskSpec(subtask_id="x", goal="X"),
            result=result,
        )
        == FailureAction.ABORT
    )


def test_parallel_config_round_trip_and_invalid_values() -> None:
    config = ModeProfileConfig(
        enabled=True,
        parallel_enabled=True,
        parallel_writes_enabled=False,
        max_parallel_workers=3,
    )
    loaded = ModeProfileConfig.model_validate(config.model_dump(mode="python"))
    assert loaded.parallel_enabled is True
    assert loaded.parallel_writes_enabled is False
    assert loaded.max_parallel_workers == 3

    with pytest.raises(Exception):
        ModeProfileConfig(max_parallel_workers=0)
    with pytest.raises(Exception):
        ModeProfileConfig(max_parallel_workers=11)


def test_parallel_execution_strategy_is_drop_in_execution_strategy() -> None:
    strategy = ParallelExecutionStrategy()
    ctx = SimpleNamespace(
        options=SimpleNamespace(),
        state=WorkingState(
            session_id="s-parallel",
            agent_id="agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=100, time_ms=1000
            ),
        ),
    )
    subtasks = [
        SubtaskSpec(subtask_id="a", goal="A", suggested_mode="respond"),
        SubtaskSpec(subtask_id="b", goal="B", suggested_mode="respond"),
    ]
    budgets = [
        BudgetCounters(ticks=5, tool_calls=2, a2a_calls=2, tokens=50, time_ms=500),
        BudgetCounters(ticks=5, tool_calls=3, a2a_calls=3, tokens=50, time_ms=500),
    ]

    results = strategy.execute(
        ctx=ctx,
        subtasks=subtasks,
        budgets=budgets,
        run_subtask=lambda subtask, budget, index, total: _child_result(
            subtask.subtask_id,
            output=f"{subtask.goal}:{budget.tokens}:{index}:{total}",
        ),
        failure_policy=ContinueOnErrorPolicy(),
        progress_monitor=SimpleNamespace(),
        cancellation_policy=SimpleNamespace(
            should_cancel=lambda *, ctx, results, attempts: False
        ),
    )

    assert [item.subtask_id for item in results] == ["a", "b"]
