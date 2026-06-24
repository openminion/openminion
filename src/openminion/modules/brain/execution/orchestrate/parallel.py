from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from openminion.modules.brain.constants import (
    BRAIN_DECISION_ROUTE_ACT,
    BRAIN_DECISION_ROUTE_RESPOND,
    BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE,
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
)
from openminion.modules.brain.execution.public_taxonomy import (
    public_mode_name_for_mode_name,
)
from openminion.modules.brain.schemas import BudgetCounters
from ..child_tasks import (
    ChildTaskResult,
    ExecutionStrategy,
    FailureAction,
    FailurePolicy,
    SubtaskResult,
    SubtaskSpec,
)


class CyclicDependencyError(ValueError):
    """Raised when a subtask dependency graph contains a cycle."""


@dataclass(slots=True, frozen=True)
class ParallelGroup:
    subtasks: list[SubtaskSpec]


@runtime_checkable
class DependencyAnalyzer(Protocol):
    def analyze(self, subtasks: list[SubtaskSpec]) -> list[ParallelGroup]: ...


@runtime_checkable
class ConcurrencyPolicy(Protocol):
    def max_workers(self, subtask_count: int) -> int: ...


@runtime_checkable
class SideEffectIsolationPolicy(Protocol):
    def is_safe_to_parallelize(self, subtask: SubtaskSpec) -> bool: ...


@runtime_checkable
class ParallelBudgetAllocator(Protocol):
    def allocate(
        self, *, budget: BudgetCounters, subtask_count: int
    ) -> list[BudgetCounters]: ...

    def allocate_slices(
        self, total_budget: BudgetCounters, worker_count: int
    ) -> list[BudgetCounters]: ...

    def return_unused(self, slices: list[BudgetCounters]) -> BudgetCounters: ...


@runtime_checkable
class ParallelResultMerger(Protocol):
    def merge(
        self,
        *,
        results: dict[str, ChildTaskResult],
        original_order: list[str],
    ) -> list[ChildTaskResult]: ...


class TopologicalDependencyAnalyzer(DependencyAnalyzer):
    def analyze(self, subtasks: list[SubtaskSpec]) -> list[ParallelGroup]:
        by_id = {item.subtask_id: item for item in subtasks}
        if len(by_id) != len(subtasks):
            raise CyclicDependencyError(
                "Parallel execution requires unique orchestrate subtask_id values."
            )
        in_degree = {item.subtask_id: 0 for item in subtasks}
        outgoing: dict[str, list[str]] = {item.subtask_id: [] for item in subtasks}
        for item in subtasks:
            for dependency in item.depends_on:
                normalized = str(dependency or "").strip()
                if not normalized:
                    continue
                if normalized not in by_id:
                    raise CyclicDependencyError(
                        f"Unknown dependency {normalized!r} for subtask {item.subtask_id!r}."
                    )
                outgoing[normalized].append(item.subtask_id)
                in_degree[item.subtask_id] += 1

        ready = [
            item.subtask_id for item in subtasks if in_degree[item.subtask_id] == 0
        ]
        groups: list[ParallelGroup] = []
        processed = 0
        while ready:
            current = list(ready)
            ready = []
            groups.append(
                ParallelGroup(subtasks=[by_id[item_id] for item_id in current])
            )
            for item_id in current:
                processed += 1
                for successor in outgoing[item_id]:
                    in_degree[successor] -= 1
                    if in_degree[successor] == 0:
                        ready.append(successor)
        if processed != len(subtasks):
            raise CyclicDependencyError("Orchestrate parallel graph contains a cycle.")
        return groups


@dataclass(slots=True)
class DefaultConcurrencyPolicy(ConcurrencyPolicy):
    max_workers_config: int = 3
    enabled: bool = True

    def max_workers(self, subtask_count: int) -> int:
        if not self.enabled:
            return 1
        return max(1, min(int(subtask_count), int(self.max_workers_config)))


@dataclass(slots=True)
class ConservativeSideEffectPolicy(SideEffectIsolationPolicy):
    parallel_writes_enabled: bool = False

    def is_safe_to_parallelize(self, subtask: SubtaskSpec) -> bool:
        raw_mode = str(subtask.suggested_mode or "").strip().lower()
        mode = public_mode_name_for_mode_name(raw_mode) or raw_mode
        if mode in {BRAIN_DECISION_ROUTE_RESPOND}:
            return True
        if raw_mode in {
            BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
            BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE,
        }:
            return bool(self.parallel_writes_enabled)
        if mode == BRAIN_DECISION_ROUTE_ACT:
            return bool(self.parallel_writes_enabled)
        return False


class EvenSplitBudgetAllocator(ParallelBudgetAllocator):
    def allocate(
        self, *, budget: BudgetCounters, subtask_count: int
    ) -> list[BudgetCounters]:
        return self.allocate_slices(budget, subtask_count)

    def allocate_slices(
        self, total_budget: BudgetCounters, worker_count: int
    ) -> list[BudgetCounters]:
        if worker_count <= 0:
            return []

        def _split(value: int) -> list[int]:
            base = int(value // worker_count)
            remainder = int(value % worker_count)
            chunks = [base for _ in range(worker_count)]
            if chunks:
                chunks[0] += remainder
            return chunks

        ticks = _split(int(total_budget.ticks))
        tool_calls = _split(int(total_budget.tool_calls))
        a2a_calls = _split(int(total_budget.a2a_calls))
        tokens = _split(int(total_budget.tokens))
        time_ms = _split(int(total_budget.time_ms))
        return [
            BudgetCounters(
                ticks=ticks[idx],
                tool_calls=tool_calls[idx],
                a2a_calls=a2a_calls[idx],
                tokens=tokens[idx],
                time_ms=time_ms[idx],
            )
            for idx in range(worker_count)
        ]

    def return_unused(self, slices: list[BudgetCounters]) -> BudgetCounters:
        return BudgetCounters(
            ticks=sum(item.ticks for item in slices),
            tool_calls=sum(item.tool_calls for item in slices),
            a2a_calls=sum(item.a2a_calls for item in slices),
            tokens=sum(item.tokens for item in slices),
            time_ms=sum(item.time_ms for item in slices),
        )


class OrderPreservingResultMerger(ParallelResultMerger):
    def merge(
        self,
        *,
        results: dict[str, ChildTaskResult],
        original_order: list[str],
    ) -> list[ChildTaskResult]:
        merged: list[ChildTaskResult] = []
        for subtask_id in original_order:
            if subtask_id in results:
                merged.append(results[subtask_id])
                continue
            placeholder = ChildTaskResult(
                subtask_id=subtask_id,
                task_id=None,
                was_promoted=False,
                result=SubtaskResult(
                    subtask_id=subtask_id,
                    goal=subtask_id,
                    status="failed",
                    mode_used=BRAIN_DECISION_ROUTE_ACT,
                    error=f"Missing parallel result for subtask {subtask_id!r}.",
                ),
            )
            merged.append(placeholder)
        return merged


class ContinueOnErrorPolicy(FailurePolicy):
    def on_failure(
        self,
        *,
        subtask: SubtaskSpec,
        result: ChildTaskResult,
    ) -> FailureAction:
        del subtask, result
        return FailureAction.CONTINUE


def _error_child_result(
    *,
    subtask: SubtaskSpec,
    error: str,
) -> ChildTaskResult:
    return ChildTaskResult(
        subtask_id=subtask.subtask_id,
        task_id=None,
        was_promoted=False,
        result=SubtaskResult(
            subtask_id=subtask.subtask_id,
            goal=subtask.goal,
            status="failed",
            mode_used=str(subtask.suggested_mode or "act").strip() or "act",
            error=error,
        ),
    )


class ParallelExecutionStrategy(ExecutionStrategy):
    def __init__(
        self,
        *,
        analyzer: DependencyAnalyzer | None = None,
        concurrency_policy: ConcurrencyPolicy | None = None,
        side_effect_policy: SideEffectIsolationPolicy | None = None,
        result_merger: ParallelResultMerger | None = None,
    ) -> None:
        self._analyzer = analyzer or TopologicalDependencyAnalyzer()
        self._concurrency_policy = concurrency_policy or DefaultConcurrencyPolicy()
        self._side_effect_policy = side_effect_policy or ConservativeSideEffectPolicy()
        self._result_merger = result_merger or OrderPreservingResultMerger()

    def execute(
        self,
        *,
        ctx,
        subtasks: list[SubtaskSpec],
        budgets: list[BudgetCounters],
        run_subtask,
        failure_policy: FailurePolicy,
        progress_monitor,
        cancellation_policy,
    ) -> list[ChildTaskResult]:
        del progress_monitor
        if len(subtasks) != len(budgets):
            raise ValueError("subtasks and budgets must have the same length")
        original_order = [item.subtask_id for item in subtasks]
        budget_by_id = {
            subtask.subtask_id: budget
            for subtask, budget in zip(subtasks, budgets, strict=False)
        }
        index_by_id = {
            subtask.subtask_id: index for index, subtask in enumerate(subtasks, start=1)
        }
        results: dict[str, ChildTaskResult] = {}
        groups = self._analyzer.analyze(subtasks)
        total = len(subtasks)
        abort = False

        for group in groups:
            if abort:
                break
            if cancellation_policy.should_cancel(
                ctx=ctx,
                results=list(results.values()),
                attempts=len(results) + 1,
            ):
                for subtask in group.subtasks:
                    results[subtask.subtask_id] = _error_child_result(
                        subtask=subtask,
                        error="Cancelled before parallel execution.",
                    )
                break

            group_subtasks = list(group.subtasks)
            workers = self._concurrency_policy.max_workers(len(group_subtasks))
            safe = all(
                self._side_effect_policy.is_safe_to_parallelize(subtask)
                for subtask in group_subtasks
            )
            if workers <= 1 or len(group_subtasks) <= 1 or not safe:
                abort = self._run_sequential_group(
                    group_subtasks=group_subtasks,
                    budget_by_id=budget_by_id,
                    index_by_id=index_by_id,
                    total=total,
                    run_subtask=run_subtask,
                    failure_policy=failure_policy,
                    results=results,
                )
                continue

            abort = self._run_parallel_group(
                group_subtasks=group_subtasks,
                workers=workers,
                budget_by_id=budget_by_id,
                index_by_id=index_by_id,
                total=total,
                run_subtask=run_subtask,
                failure_policy=failure_policy,
                results=results,
            )

        return self._result_merger.merge(results=results, original_order=original_order)

    def _run_sequential_group(
        self,
        *,
        group_subtasks: list[SubtaskSpec],
        budget_by_id: dict[str, BudgetCounters],
        index_by_id: dict[str, int],
        total: int,
        run_subtask: Callable[[SubtaskSpec, BudgetCounters, int, int], ChildTaskResult],
        failure_policy: FailurePolicy,
        results: dict[str, ChildTaskResult],
    ) -> bool:
        for subtask in group_subtasks:
            result = run_subtask(
                subtask,
                budget_by_id[subtask.subtask_id],
                index_by_id[subtask.subtask_id],
                total,
            )
            results[subtask.subtask_id] = result
            if result.result.status == "failed":
                action = failure_policy.on_failure(subtask=subtask, result=result)
                if action == FailureAction.ABORT:
                    return True
        return False

    def _run_parallel_group(
        self,
        *,
        group_subtasks: list[SubtaskSpec],
        workers: int,
        budget_by_id: dict[str, BudgetCounters],
        index_by_id: dict[str, int],
        total: int,
        run_subtask: Callable[[SubtaskSpec, BudgetCounters, int, int], ChildTaskResult],
        failure_policy: FailurePolicy,
        results: dict[str, ChildTaskResult],
    ) -> bool:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            pending: dict[Future[ChildTaskResult], SubtaskSpec] = {}
            for subtask in group_subtasks:
                future = executor.submit(
                    run_subtask,
                    subtask,
                    budget_by_id[subtask.subtask_id],
                    index_by_id[subtask.subtask_id],
                    total,
                )
                pending[future] = subtask

            abort = False
            while pending:
                done, _ = wait(set(pending.keys()), return_when=FIRST_COMPLETED)
                for future in done:
                    subtask = pending.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:  # pragma: no cover - defensive
                        result = _error_child_result(subtask=subtask, error=str(exc))
                    results[subtask.subtask_id] = result
                    if result.result.status != "failed":
                        continue
                    action = failure_policy.on_failure(subtask=subtask, result=result)
                    if action == FailureAction.ABORT:
                        abort = True
                        for pending_future, pending_subtask in list(pending.items()):
                            pending_future.cancel()
                            results[pending_subtask.subtask_id] = _error_child_result(
                                subtask=pending_subtask,
                                error="Cancelled after sibling parallel failure.",
                            )
                        pending.clear()
                        break
                if abort:
                    break
        return abort


__all__ = [
    "ConcurrencyPolicy",
    "ConservativeSideEffectPolicy",
    "CyclicDependencyError",
    "ContinueOnErrorPolicy",
    "DefaultConcurrencyPolicy",
    "DependencyAnalyzer",
    "EvenSplitBudgetAllocator",
    "ParallelBudgetAllocator",
    "ParallelGroup",
    "OrderPreservingResultMerger",
    "ParallelExecutionStrategy",
    "ParallelResultMerger",
    "SideEffectIsolationPolicy",
    "TopologicalDependencyAnalyzer",
]
