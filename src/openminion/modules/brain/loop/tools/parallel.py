from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from openminion.modules.brain.schemas import ToolCommand
from .contracts import PreparedToolDispatch, PrepareOutcome

from .causal import classify_batch


@dataclass(frozen=True, slots=True)
class ParallelDispatchResult:
    ordered_results: tuple[tuple[Any, Any], ...]
    parallel_fan_out_count: int
    tool_calls_parallel: int
    tool_calls_sequential: int
    budget_managed_in_dispatch: bool = False


def _tool_command_for_call(
    *,
    tool_call: Any,
) -> ToolCommand:
    if isinstance(tool_call, ToolCommand):
        return tool_call.model_copy(deep=True)
    tool_name = str(getattr(tool_call, "name", "") or "").strip()
    inputs = getattr(tool_call, "inputs", None)
    return ToolCommand(
        title=tool_name,
        tool_name=tool_name,
        args=(
            dict(getattr(tool_call, "arguments", {}) or {})
            if isinstance(getattr(tool_call, "arguments", {}), dict)
            else {}
        ),
        inputs=dict(inputs) if isinstance(inputs, dict) else {},
    )


def _prepare_one(
    loop_ctx: Any,
    *,
    tool_call: Any,
    include_reflect: bool,
) -> PreparedToolDispatch | PrepareOutcome:
    command = _tool_command_for_call(tool_call=tool_call)
    return loop_ctx.prepare_tool_dispatch(
        command=command,
        include_reflect=include_reflect,
    )


def _execute_one(
    loop_ctx: Any,
    *,
    prepared_dispatch: PreparedToolDispatch,
) -> Any:
    return (
        prepared_dispatch,
        loop_ctx.execute_prepared_tool_dispatch(
            prepared_dispatch=prepared_dispatch,
        ),
    )


def _execute_single_call(
    loop_ctx: Any,
    *,
    tool_call: Any,
    include_reflect: bool,
) -> tuple[Any, Any]:
    command = _tool_command_for_call(tool_call=tool_call)
    return (
        tool_call,
        loop_ctx.execute_command(
            command=command,
            include_reflect=include_reflect,
        ),
    )


def _execute_parallel_tool_batch_without_prepared_dispatch(
    *,
    loop_ctx: Any,
    tool_calls: list[Any],
    include_reflect: bool,
    provider_parallel_tool_capacity: int,
) -> ParallelDispatchResult:
    causal_batch = classify_batch(list(tool_calls))
    results_by_index: dict[int, tuple[Any, Any]] = {}
    parallel_fan_out_count = 0
    tool_calls_parallel = 0
    tool_calls_sequential = 0

    capacity = max(0, int(provider_parallel_tool_capacity or 0))

    for group in causal_batch.groups:
        if len(group) <= 1 or capacity == 1:
            tool_calls_sequential += len(group)
            for index in group:
                results_by_index[index] = _execute_single_call(
                    loop_ctx,
                    tool_call=tool_calls[index],
                    include_reflect=include_reflect,
                )
            continue

        indices = sorted(group)
        effective_cap = capacity if capacity > 0 else len(indices)
        sub_batches = [
            indices[start : start + effective_cap]
            for start in range(0, len(indices), effective_cap)
        ]
        for sub_batch in sub_batches:
            if len(sub_batch) == 1:
                tool_calls_sequential += 1
                results_by_index[sub_batch[0]] = _execute_single_call(
                    loop_ctx,
                    tool_call=tool_calls[sub_batch[0]],
                    include_reflect=include_reflect,
                )
            else:
                parallel_fan_out_count += 1
                tool_calls_parallel += len(sub_batch)
                with ThreadPoolExecutor(max_workers=len(sub_batch)) as executor:
                    futures = {
                        index: executor.submit(
                            _execute_single_call,
                            loop_ctx,
                            tool_call=tool_calls[index],
                            include_reflect=include_reflect,
                        )
                        for index in sub_batch
                    }
                    for index in sub_batch:
                        results_by_index[index] = futures[index].result()

    return ParallelDispatchResult(
        ordered_results=tuple(
            results_by_index[index] for index in range(len(tool_calls))
        ),
        parallel_fan_out_count=parallel_fan_out_count,
        tool_calls_parallel=tool_calls_parallel,
        tool_calls_sequential=tool_calls_sequential,
        budget_managed_in_dispatch=False,
    )


def _supports_prepared_parallel_dispatch(loop_ctx: Any) -> bool:
    return all(
        callable(getattr(loop_ctx, attr, None))
        for attr in (
            "prepare_tool_dispatch",
            "execute_prepared_tool_dispatch",
            "finalize_tool_result",
            "finalize_prepare_outcome",
        )
    )


def _prepare_dispatch_entries(
    *,
    loop_ctx: Any,
    tool_calls: list[Any],
    include_reflect: bool,
) -> list[PreparedToolDispatch | PrepareOutcome]:
    return [
        _prepare_one(
            loop_ctx,
            tool_call=tool_call,
            include_reflect=include_reflect,
        )
        for tool_call in tool_calls
    ]


def _collect_prepared_dispatches(
    *,
    loop_ctx: Any,
    tool_calls: list[Any],
    prepared_entries: list[PreparedToolDispatch | PrepareOutcome],
    results_by_index: dict[int, tuple[Any, Any]],
) -> dict[int, PreparedToolDispatch]:
    prepared_by_index: dict[int, PreparedToolDispatch] = {}
    for index, prepared_entry in enumerate(prepared_entries):
        if isinstance(prepared_entry, PrepareOutcome):
            results_by_index[index] = (
                tool_calls[index],
                loop_ctx.finalize_prepare_outcome(prepare_outcome=prepared_entry),
            )
            continue
        prepared_by_index[index] = prepared_entry
    return prepared_by_index


def _finalized_prepared_result(
    *,
    loop_ctx: Any,
    tool_call: Any,
    prepared_dispatch: PreparedToolDispatch,
) -> tuple[Any, Any]:
    prepared_dispatch, raw_result = _execute_one(
        loop_ctx,
        prepared_dispatch=prepared_dispatch,
    )
    return (
        tool_call,
        loop_ctx.finalize_tool_result(
            prepared_dispatch=prepared_dispatch,
            raw_result=raw_result,
        ),
    )


def _execute_prepared_sub_batch(
    *,
    loop_ctx: Any,
    tool_calls: list[Any],
    prepared_by_index: dict[int, PreparedToolDispatch],
    sub_batch: list[int],
) -> dict[int, tuple[Any, Any]]:
    if len(sub_batch) == 1:
        index = sub_batch[0]
        return {
            index: _finalized_prepared_result(
                loop_ctx=loop_ctx,
                tool_call=tool_calls[index],
                prepared_dispatch=prepared_by_index[index],
            )
        }
    with ThreadPoolExecutor(max_workers=len(sub_batch)) as executor:
        futures = {
            index: executor.submit(
                _execute_one,
                loop_ctx,
                prepared_dispatch=prepared_by_index[index],
            )
            for index in sub_batch
        }
        results: dict[int, tuple[Any, Any]] = {}
        for index in sub_batch:
            prepared_dispatch, raw_result = futures[index].result()
            results[index] = (
                tool_calls[index],
                loop_ctx.finalize_tool_result(
                    prepared_dispatch=prepared_dispatch,
                    raw_result=raw_result,
                ),
            )
        return results


def execute_parallel_tool_batch(
    *,
    loop_ctx: Any,
    tool_calls: list[Any],
    include_reflect: bool,
    provider_parallel_tool_capacity: int = 1,
) -> ParallelDispatchResult:
    if not _supports_prepared_parallel_dispatch(loop_ctx):
        return _execute_parallel_tool_batch_without_prepared_dispatch(
            loop_ctx=loop_ctx,
            tool_calls=tool_calls,
            include_reflect=include_reflect,
            provider_parallel_tool_capacity=provider_parallel_tool_capacity,
        )

    causal_batch = classify_batch(list(tool_calls))
    results_by_index: dict[int, tuple[Any, Any]] = {}
    prepared_by_index: dict[int, PreparedToolDispatch] = {}
    parallel_fan_out_count = 0
    tool_calls_parallel = 0
    tool_calls_sequential = 0

    capacity = max(0, int(provider_parallel_tool_capacity or 0))

    prepared_by_index = _collect_prepared_dispatches(
        loop_ctx=loop_ctx,
        tool_calls=tool_calls,
        prepared_entries=_prepare_dispatch_entries(
            loop_ctx=loop_ctx,
            tool_calls=tool_calls,
            include_reflect=include_reflect,
        ),
        results_by_index=results_by_index,
    )

    for group in causal_batch.groups:
        dispatch_indices = [index for index in group if index in prepared_by_index]
        if not dispatch_indices:
            continue
        if len(dispatch_indices) <= 1 or capacity == 1:
            tool_calls_sequential += len(dispatch_indices)
            for index in dispatch_indices:
                results_by_index[index] = (
                    _finalized_prepared_result(
                        loop_ctx=loop_ctx,
                        tool_call=tool_calls[index],
                        prepared_dispatch=prepared_by_index[index],
                    )
                )
            continue

        indices = sorted(dispatch_indices)
        effective_cap = capacity if capacity > 0 else len(indices)
        sub_batches = [
            indices[start : start + effective_cap]
            for start in range(0, len(indices), effective_cap)
        ]
        for sub_batch in sub_batches:
            if len(sub_batch) == 1:
                tool_calls_sequential += 1
            else:
                parallel_fan_out_count += 1
                tool_calls_parallel += len(sub_batch)
            results_by_index.update(
                _execute_prepared_sub_batch(
                    loop_ctx=loop_ctx,
                    tool_calls=tool_calls,
                    prepared_by_index=prepared_by_index,
                    sub_batch=sub_batch,
                )
            )

    return ParallelDispatchResult(
        ordered_results=tuple(
            results_by_index[index] for index in range(len(tool_calls))
        ),
        parallel_fan_out_count=parallel_fan_out_count,
        tool_calls_parallel=tool_calls_parallel,
        tool_calls_sequential=tool_calls_sequential,
        budget_managed_in_dispatch=False,
    )
