"""Adaptive loop dispatch runtime helpers."""

from __future__ import annotations

from typing import Any

from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_FAILED
from openminion.modules.brain.schemas import ActionError, ActionResult, new_uuid

from .contracts import (
    AdaptiveToolLoopContext,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
    profile_include_reflect,
)
from .parallel import execute_parallel_tool_batch
from .shortlisting import TOOL_REQUEST_TOOL_NAME, with_tool_request_spec
from .telemetry import _accumulate_parallel_telemetry


def _tool_request_result(
    *,
    requested_name: str,
    active_tool_names: set[str],
    requestable_specs_by_name: dict[str, Any],
    active_tool_specs: list[Any],
) -> tuple[ActionResult, bool]:
    if not requested_name:
        return (
            ActionResult(
                command_id=new_uuid(),
                status=BRAIN_ACTION_STATUS_FAILED,
                summary="tool.request requires an exact inactive tool name.",
                error=ActionError(
                    code="TOOL_REQUEST_MISSING_NAME",
                    message="Missing required tool name.",
                ),
            ),
            False,
        )
    if requested_name in active_tool_names:
        return (
            ActionResult(
                command_id=new_uuid(),
                status="success",
                summary=f"Tool schema already active: {requested_name}",
                outputs={"tool_name": requested_name, "activated": False},
            ),
            False,
        )
    requested_spec = requestable_specs_by_name.get(requested_name)
    if requested_spec is None:
        requestable_names = sorted(requestable_specs_by_name)
        return (
            ActionResult(
                command_id=new_uuid(),
                status=BRAIN_ACTION_STATUS_FAILED,
                summary=(
                    f"Tool schema is not requestable in this loop: {requested_name}. "
                    f"Use one exact name from: {requestable_names}"
                ),
                outputs={"tool_name": requested_name, "activated": False},
                error=ActionError(
                    code="TOOL_REQUEST_UNAVAILABLE",
                    message="Requested tool is not available in this loop.",
                    details={
                        "tool_name": requested_name,
                        "requestable_tool_names": requestable_names,
                    },
                ),
            ),
            False,
        )
    active_tool_names.add(requested_name)
    active_tool_specs[:] = with_tool_request_spec(
        [
            *[
                spec
                for spec in active_tool_specs
                if str(getattr(spec, "name", "") or "").strip()
                != TOOL_REQUEST_TOOL_NAME
            ],
            requested_spec,
        ]
    )
    return (
        ActionResult(
            command_id=new_uuid(),
            status="success",
            summary=f"Activated tool schema: {requested_name}",
            outputs={"tool_name": requested_name, "activated": True},
        ),
        True,
    )


def _dispatch_tool_batches(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    tool_calls: list[Any],
    tool_batch_runner: Any,
    loop_cache: Any,
) -> tuple[list[tuple[Any, Any]], frozenset[int], int]:
    cached_results: dict[int, Any] = {}
    uncached_tool_calls: list[Any] = []
    for tc_idx, tool_call in enumerate(tool_calls):
        tc_name = str(getattr(tool_call, "name", "") or "").strip()
        tc_args = dict(getattr(tool_call, "arguments", {}) or {})
        cached_result = loop_cache.get(tc_name, tc_args)
        if cached_result is not None:
            cached_results[tc_idx] = cached_result
        else:
            uncached_tool_calls.append(tool_call)

    iter_batch_parallel_count = 0
    if tool_batch_runner is None:
        if uncached_tool_calls:
            dispatch_result = execute_parallel_tool_batch(
                loop_ctx=loop_ctx,
                tool_calls=uncached_tool_calls,
                include_reflect=profile_include_reflect(profile),
                provider_parallel_tool_capacity=int(
                    profile.provider_parallel_tool_capacity or 0
                ),
            )
            _accumulate_parallel_telemetry(
                loop_state,
                parallel_fan_out_count=dispatch_result.parallel_fan_out_count,
                tool_calls_parallel=dispatch_result.tool_calls_parallel,
                tool_calls_sequential=dispatch_result.tool_calls_sequential,
            )
            iter_batch_parallel_count = dispatch_result.tool_calls_parallel
            dispatch_pairs: list[tuple[Any, Any]] = list(
                dispatch_result.ordered_results
            )
        else:
            dispatch_pairs = []
    elif uncached_tool_calls:
        dispatch_pairs = list(
            tool_batch_runner(
                loop_ctx=loop_ctx,
                tool_calls=uncached_tool_calls,
                include_reflect=profile_include_reflect(profile),
                loop_state=loop_state,
            )
        )
    else:
        dispatch_pairs = []

    ordered_tool_results: list[tuple[Any, Any]] = []
    dispatch_iter = iter(dispatch_pairs)
    for tc_idx, tool_call in enumerate(tool_calls):
        if tc_idx in cached_results:
            ordered_tool_results.append((tool_call, cached_results[tc_idx]))
        else:
            ordered_tool_results.append(next(dispatch_iter))

    return (
        ordered_tool_results,
        frozenset(cached_results),
        iter_batch_parallel_count,
    )
