"""Adaptive loop dispatch runtime helpers."""

from __future__ import annotations

from typing import Any

from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_FAILED
from openminion.modules.brain.schemas import ActionError, ActionResult, new_uuid
from openminion.modules.llm.schemas import Message

from .contracts import (
    ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
    AdaptiveToolLoopContext,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
    canonical_tool_call_signature,
    profile_include_reflect,
)
from .events import IterationToolCallRecord
from .parallel import execute_parallel_tool_batch
from .shortlisting import TOOL_REQUEST_TOOL_NAME, with_tool_request_spec
from .status import emit_adaptive_status
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
        return (
            ActionResult(
                command_id=new_uuid(),
                status=BRAIN_ACTION_STATUS_FAILED,
                summary=f"Tool schema is not requestable in this loop: {requested_name}",
                outputs={"tool_name": requested_name, "activated": False},
                error=ActionError(
                    code="TOOL_REQUEST_UNAVAILABLE",
                    message="Requested tool is not available in this loop.",
                    details={"tool_name": requested_name},
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


def _handle_exact_date_requirements(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    tool_calls: list[Any],
    allowed_tools: frozenset[str],
    public_mode_tag: str,
    signature: str,
    iter_tool_records: list[IterationToolCallRecord],
    iter_llm_duration_ms: int,
    iter_input_tokens: int,
    iter_output_tokens: int,
    on_tool_result: Any,
    repair_stale_exact_date_search_args: Any,
    stale_exact_date_query_reason: Any,
    result_factory: Any,
) -> Any | None:
    freshness_obligations = getattr(loop_ctx.state, "freshness_obligations", None)
    require_exact_date = bool(
        getattr(freshness_obligations, "require_exact_date", False)
    )
    user_input_for_exact_date = str(
        getattr(loop_ctx, "user_input", "") or getattr(loop_ctx.state, "goal", "") or ""
    ).strip()
    for tool_call in tool_calls:
        tool_name = str(getattr(tool_call, "name", "") or "").strip()
        tool_args = dict(getattr(tool_call, "arguments", {}) or {})
        freshness_reason = stale_exact_date_query_reason(
            user_input=user_input_for_exact_date,
            require_exact_date=require_exact_date,
            tool_name=tool_name,
            tool_args=tool_args,
        )
        if not freshness_reason:
            continue
        repaired_tool_args = repair_stale_exact_date_search_args(
            user_input=user_input_for_exact_date,
            require_exact_date=require_exact_date,
            tool_name=tool_name,
            tool_args=tool_args,
        )
        if repaired_tool_args is not None:
            tool_call.arguments = repaired_tool_args
            loop_state.messages.append(
                Message(
                    role="system",
                    content=(
                        f"{freshness_reason} Runtime removed the stale explicit "
                        "year from this search because the user did not request "
                        "a historical year. Prefer current_datetime or omit the "
                        "year on future exact-date searches."
                    ),
                )
            )
            emit_adaptive_status(
                loop_ctx,
                profile=profile,
                loop_state=loop_state,
                detail_text=f"{public_mode_tag} exact-date query auto-repaired",
                mode_state="freshness_exact_date_query_autorepair",
                extra={"tool_name": tool_name},
            )
            continue
        retry_signature = canonical_tool_call_signature(
            {"name": tool_name, "arguments": tool_args}
        )
        seen_signatures = set(
            loop_state.scratchpad.get("freshness_exact_date_rejected_signatures", [])
            or []
        )
        if retry_signature in seen_signatures:
            loop_state.termination_reason = ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY
            emit_adaptive_status(
                loop_ctx,
                profile=profile,
                loop_state=loop_state,
                detail_text=f"{public_mode_tag} exact-date query mismatch",
                mode_state="freshness_exact_date_query_rejected",
                termination_reason=ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
                extra={"tool_name": tool_name},
            )
            return result_factory(
                tool_calls=tool_calls,
                ordered_tool_results=[],
                cached_indices=frozenset(),
                iter_batch_parallel_count=0,
                dispatch_budget_managed=False,
                batch_had_progress=False,
                continue_loop=False,
                outcome=AdaptiveToolLoopOutcome(
                    profile_name=profile.profile_name,
                    mode_name=profile.mode_name,
                    termination_reason=ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
                    state=loop_state,
                    allowed_tools=allowed_tools,
                    error_message=freshness_reason,
                    tool_name=tool_name,
                ),
            )
        seen_signatures.add(retry_signature)
        loop_state.scratchpad["freshness_exact_date_rejected_signatures"] = sorted(
            seen_signatures
        )
        loop_state.messages.append(
            Message(
                role="system",
                content=(
                    f"{freshness_reason} Retry the search with a query whose "
                    "explicit date framing is consistent with current_datetime, "
                    "or omit the year if you do not need to pin it."
                ),
            )
        )
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} exact-date query mismatch",
            mode_state="freshness_exact_date_query_retry",
            extra={"tool_name": tool_name},
        )
        return result_factory(
            tool_calls=tool_calls,
            ordered_tool_results=[],
            cached_indices=frozenset(),
            iter_batch_parallel_count=0,
            dispatch_budget_managed=False,
            batch_had_progress=False,
            continue_loop=True,
            outcome=None,
        )
    return None


def _dispatch_tool_batches(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    tool_calls: list[Any],
    tool_batch_runner: Any,
    loop_cache: Any,
) -> tuple[list[tuple[Any, Any]], frozenset[int], int, bool]:
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
    dispatch_budget_managed = False
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
            dispatch_budget_managed = bool(
                getattr(dispatch_result, "budget_managed_in_dispatch", False)
            )
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
        dispatch_budget_managed,
    )
