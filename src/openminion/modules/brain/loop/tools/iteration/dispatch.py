from __future__ import annotations

from typing import Any, NamedTuple

from openminion.modules.brain.execution.child_tasks import (
    DecomposeControlPayload,
)
from openminion.modules.brain.loop.constants import (
    PLAN_TOOL_LAST_SUBSTANTIVE_COUNT_SCRATCHPAD_KEY,
)
from openminion.modules.llm.schemas import Message

from ..budget_control import _effective_cap
from ..contracts import (
    ADAPTIVE_TERM_DECOMPOSE_REQUESTED,
    AdaptiveToolLoopContext,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
    canonical_tool_call_signature,
)
from ..decompose import (
    _DECOMPOSE_TOOL_NAME,
    _decompose_decline_result,
    _decompose_invalid_outcome,
    _decompose_tool_calls,
    _subtasks_from_decompose_control,
)
from ..events import IterationToolCallRecord
from ..dispatch import (
    _dispatch_tool_batches,
    _handle_exact_date_requirements,
    _tool_request_result,
)
from ..evidence import _count_substantive_non_control_tool_results
from ..messages import action_result_to_tool_message
from ..plan_control import (
    PLAN_TOOL_ACTIONS_SCRATCHPAD_KEY,
    PLAN_TOOL_ATTEMPTED_SCRATCHPAD_KEY,
    PLAN_TOOL_NAME,
    PLAN_TOOL_USED_SCRATCHPAD_KEY,
    handle_plan_tool_call,
    with_enabled_plan_tool_spec,
)
from ..review_control import (
    REVIEW_TOOL_ATTEMPTED_SCRATCHPAD_KEY,
    REVIEW_TOOL_NAME,
    REVIEW_TOOL_USED_SCRATCHPAD_KEY,
    handle_review_tool_call,
)
from ..shortlisting import (
    TOOL_REQUEST_TOOL_NAME,
    build_inactive_tool_directory_message,
)
from ..status import emit_adaptive_status
from ..telemetry import _emit_iteration_event


class LoopDispatchResult(NamedTuple):
    """Outputs of the per-iteration dispatch phase."""

    tool_calls: list[Any]
    ordered_tool_results: list[tuple[Any, Any]]
    cached_indices: frozenset[int]
    iter_batch_parallel_count: int
    dispatch_budget_managed: bool
    batch_had_progress: bool
    continue_loop: bool
    outcome: AdaptiveToolLoopOutcome | None


def _is_plan_tool_call(tool_call: Any) -> bool:
    return str(getattr(tool_call, "name", "") or "").strip() == PLAN_TOOL_NAME


def _record_successful_plan_action(
    loop_state: AdaptiveToolLoopState,
    arguments: dict[str, Any],
) -> None:
    loop_state.scratchpad[PLAN_TOOL_USED_SCRATCHPAD_KEY] = True
    recorded_actions = list(
        loop_state.scratchpad.get(PLAN_TOOL_ACTIONS_SCRATCHPAD_KEY, []) or []
    )
    recorded_actions.append(str(arguments.get("action", "") or "").strip())
    loop_state.scratchpad[PLAN_TOOL_ACTIONS_SCRATCHPAD_KEY] = recorded_actions
    loop_state.scratchpad[PLAN_TOOL_LAST_SUBSTANTIVE_COUNT_SCRATCHPAD_KEY] = (
        _count_substantive_non_control_tool_results(loop_state)
    )


def _handle_decompose_calls(
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
    append_tool_result_payload: Any,
    set_turn_progress: Any,
) -> LoopDispatchResult | None:
    decompose_calls = _decompose_tool_calls(tool_calls)
    if not (decompose_calls and _DECOMPOSE_TOOL_NAME in allowed_tools):
        return None
    other_tool_names = [
        str(getattr(call, "name", "") or "").strip()
        for call in tool_calls
        if str(getattr(call, "name", "") or "").strip() != _DECOMPOSE_TOOL_NAME
    ]
    if other_tool_names:
        mixed_signature = canonical_tool_call_signature(
            {
                "name": _DECOMPOSE_TOOL_NAME,
                "arguments": {"other_tool_names": sorted(other_tool_names)},
            }
        )
        seen_signatures = set(
            loop_state.scratchpad.get("decompose_mixed_retry_signatures", []) or []
        )
        if mixed_signature in seen_signatures:
            return LoopDispatchResult(
                tool_calls=tool_calls,
                ordered_tool_results=[],
                cached_indices=frozenset(),
                iter_batch_parallel_count=0,
                dispatch_budget_managed=False,
                batch_had_progress=False,
                continue_loop=False,
                outcome=_decompose_invalid_outcome(
                    loop_ctx=loop_ctx,
                    profile=profile,
                    loop_state=loop_state,
                    allowed_tools=allowed_tools,
                    public_mode_tag=public_mode_tag,
                    reason="mixed_tool_calls",
                    message=(
                        "decompose cannot be mixed with other tool calls in the "
                        f"same adaptive-loop turn: {other_tool_names}"
                    ),
                ),
            )
        seen_signatures.add(mixed_signature)
        loop_state.scratchpad["decompose_mixed_retry_signatures"] = sorted(
            seen_signatures
        )
        loop_state.messages.append(
            Message(
                role="system",
                content=(
                    "decompose is a control tool and cannot be mixed with "
                    "executable tool calls in the same turn. Retry with either "
                    "decompose alone, or with only executable tools (without "
                    f"decompose). Mixed tool names: {other_tool_names}"
                ),
            )
        )
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} decompose mixed-tool retry",
            mode_state="decompose_mixed_tool_retry",
        )
        return LoopDispatchResult(
            tool_calls=tool_calls,
            ordered_tool_results=[],
            cached_indices=frozenset(),
            iter_batch_parallel_count=0,
            dispatch_budget_managed=False,
            batch_had_progress=False,
            continue_loop=True,
            outcome=None,
        )
    try:
        payload = DecomposeControlPayload.model_validate(
            getattr(decompose_calls[0], "arguments", {}) or {}
        )
    except Exception as exc:  # noqa: BLE001
        return LoopDispatchResult(
            tool_calls=tool_calls,
            ordered_tool_results=[],
            cached_indices=frozenset(),
            iter_batch_parallel_count=0,
            dispatch_budget_managed=False,
            batch_had_progress=False,
            continue_loop=False,
            outcome=_decompose_invalid_outcome(
                loop_ctx=loop_ctx,
                profile=profile,
                loop_state=loop_state,
                allowed_tools=allowed_tools,
                public_mode_tag=public_mode_tag,
                reason="invalid_payload",
                message=str(exc),
            ),
        )
    decompose_subtasks = _subtasks_from_decompose_control(payload)
    if decompose_subtasks:
        scratchpad = dict(loop_state.scratchpad or {})
        scratchpad["adaptive.decompose_subtasks"] = list(decompose_subtasks)
        loop_state.scratchpad = scratchpad
        loop_state.termination_reason = ADAPTIVE_TERM_DECOMPOSE_REQUESTED
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} decompose requested",
            mode_state="decompose_requested",
            termination_reason=ADAPTIVE_TERM_DECOMPOSE_REQUESTED,
            extra={
                "subtask_count": len(decompose_subtasks),
                "subtask_ids": [
                    str(item.get("subtask_id", "") or "").strip()
                    for item in decompose_subtasks
                    if str(item.get("subtask_id", "") or "").strip()
                ],
            },
        )
        return LoopDispatchResult(
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
                termination_reason=ADAPTIVE_TERM_DECOMPOSE_REQUESTED,
                state=loop_state,
                allowed_tools=allowed_tools,
                decompose_subtasks=decompose_subtasks,
                tool_name=_DECOMPOSE_TOOL_NAME,
            ),
        )
    action_result = _decompose_decline_result()
    loop_state.messages.append(
        action_result_to_tool_message(
            getattr(decompose_calls[0], "id", None),
            _DECOMPOSE_TOOL_NAME,
            action_result,
        )
    )
    append_tool_result_payload(
        loop_state,
        tool_name=_DECOMPOSE_TOOL_NAME,
        action_result=action_result,
    )
    iter_tool_records.append(
        IterationToolCallRecord(
            tool_name=_DECOMPOSE_TOOL_NAME,
            duration_ms=0,
            status=str(getattr(action_result, "status", "") or ""),
            cache_hit=False,
            parallel=False,
        )
    )
    set_turn_progress(
        loop_state,
        llm_call_count=loop_state.llm_calls,
        llm_call_limit=_effective_cap(profile, loop_state),
        progress_phase="tool",
        tool_name=_DECOMPOSE_TOOL_NAME,
    )
    emit_adaptive_status(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        detail_text=f"{public_mode_tag} tool {_DECOMPOSE_TOOL_NAME}",
        mode_state="tool_call",
        extra={"tool_name": _DECOMPOSE_TOOL_NAME, "subtask_count": 0},
    )
    if on_tool_result is not None:
        on_tool_result(loop_state)
    if signature not in set(loop_state.seen_signatures):
        loop_state.seen_signatures.append(signature)
    _emit_iteration_event(
        loop_ctx=loop_ctx,
        profile=profile,
        loop_state=loop_state,
        llm_duration_ms=iter_llm_duration_ms,
        tool_records=iter_tool_records,
        tokens_used=iter_input_tokens + iter_output_tokens,
    )
    return LoopDispatchResult(
        tool_calls=tool_calls,
        ordered_tool_results=[],
        cached_indices=frozenset(),
        iter_batch_parallel_count=0,
        dispatch_budget_managed=False,
        batch_had_progress=False,
        continue_loop=True,
        outcome=None,
    )


def _process_plan_tool_calls(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    tool_calls: list[Any],
    public_mode_tag: str,
    signature: str,
    iter_tool_records: list[IterationToolCallRecord],
    iter_llm_duration_ms: int,
    iter_input_tokens: int,
    iter_output_tokens: int,
    on_tool_result: Any,
    set_turn_progress: Any,
) -> tuple[list[Any], bool, LoopDispatchResult | None]:
    batch_had_progress = False
    plan_tool_calls = [
        tool_call for tool_call in tool_calls if _is_plan_tool_call(tool_call)
    ]
    if not plan_tool_calls:
        return tool_calls, batch_had_progress, None
    regular_tool_calls = [
        tool_call for tool_call in tool_calls if not _is_plan_tool_call(tool_call)
    ]
    for tool_call in plan_tool_calls:
        arguments = dict(getattr(tool_call, "arguments", {}) or {})
        loop_state.scratchpad[PLAN_TOOL_ATTEMPTED_SCRATCHPAD_KEY] = True
        action_result = handle_plan_tool_call(loop_ctx=loop_ctx, arguments=arguments)
        if str(getattr(action_result, "status", "") or "") == "success":
            _record_successful_plan_action(loop_state, arguments)
        loop_state.messages.append(
            action_result_to_tool_message(
                getattr(tool_call, "id", None),
                PLAN_TOOL_NAME,
                action_result,
            )
        )
        iter_tool_records.append(
            IterationToolCallRecord(
                tool_name=PLAN_TOOL_NAME,
                duration_ms=0,
                status=str(getattr(action_result, "status", "") or ""),
                cache_hit=False,
                parallel=False,
            )
        )
        set_turn_progress(
            loop_state,
            llm_call_count=loop_state.llm_calls,
            llm_call_limit=_effective_cap(profile, loop_state),
            progress_phase="tool",
            tool_name=PLAN_TOOL_NAME,
        )
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} tool {PLAN_TOOL_NAME}",
            mode_state="tool_call",
            extra={
                "tool_name": PLAN_TOOL_NAME,
                "plan_action": str(arguments.get("action", "") or "").strip(),
            },
        )
    batch_had_progress = True
    if on_tool_result is not None:
        on_tool_result(loop_state)
    if not regular_tool_calls:
        if signature not in set(loop_state.seen_signatures):
            loop_state.seen_signatures.append(signature)
        _emit_iteration_event(
            loop_ctx=loop_ctx,
            profile=profile,
            loop_state=loop_state,
            llm_duration_ms=iter_llm_duration_ms,
            tool_records=iter_tool_records,
            tokens_used=iter_input_tokens + iter_output_tokens,
        )
        return (
            [],
            batch_had_progress,
            LoopDispatchResult(
                tool_calls=[],
                ordered_tool_results=[],
                cached_indices=frozenset(),
                iter_batch_parallel_count=0,
                dispatch_budget_managed=False,
                batch_had_progress=batch_had_progress,
                continue_loop=True,
                outcome=None,
            ),
        )
    return regular_tool_calls, batch_had_progress, None


def _process_review_tool_calls(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    tool_calls: list[Any],
    public_mode_tag: str,
    signature: str,
    iter_tool_records: list[IterationToolCallRecord],
    iter_llm_duration_ms: int,
    iter_input_tokens: int,
    iter_output_tokens: int,
    on_tool_result: Any,
    append_tool_result_payload: Any,
    set_turn_progress: Any,
) -> tuple[list[Any], bool, LoopDispatchResult | None]:
    """Process review tool calls helper."""
    batch_had_progress = False
    review_tool_calls = [
        tool_call
        for tool_call in tool_calls
        if str(getattr(tool_call, "name", "") or "").strip() == REVIEW_TOOL_NAME
    ]
    if not review_tool_calls:
        return tool_calls, batch_had_progress, None
    regular_tool_calls = [
        tool_call
        for tool_call in tool_calls
        if str(getattr(tool_call, "name", "") or "").strip() != REVIEW_TOOL_NAME
    ]
    for tool_call in review_tool_calls:
        arguments = dict(getattr(tool_call, "arguments", {}) or {})
        loop_state.scratchpad[REVIEW_TOOL_ATTEMPTED_SCRATCHPAD_KEY] = True
        action_result = handle_review_tool_call(loop_ctx=loop_ctx, arguments=arguments)
        if str(getattr(action_result, "status", "") or "") == "success":
            loop_state.scratchpad[REVIEW_TOOL_USED_SCRATCHPAD_KEY] = True
        loop_state.messages.append(
            action_result_to_tool_message(
                getattr(tool_call, "id", None),
                REVIEW_TOOL_NAME,
                action_result,
            )
        )
        iter_tool_records.append(
            IterationToolCallRecord(
                tool_name=REVIEW_TOOL_NAME,
                duration_ms=0,
                status=str(getattr(action_result, "status", "") or ""),
                cache_hit=False,
                parallel=False,
            )
        )
        if callable(append_tool_result_payload):
            append_tool_result_payload(
                loop_state,
                tool_name=REVIEW_TOOL_NAME,
                action_result=action_result,
            )
        set_turn_progress(
            loop_state,
            llm_call_count=loop_state.llm_calls,
            llm_call_limit=_effective_cap(profile, loop_state),
            progress_phase="tool",
            tool_name=REVIEW_TOOL_NAME,
        )
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} tool {REVIEW_TOOL_NAME}",
            mode_state="tool_call",
            extra={
                "tool_name": REVIEW_TOOL_NAME,
                "review_severity": str(
                    (getattr(action_result, "outputs", {}) or {}).get("severity", "")
                    or ""
                ).strip(),
            },
        )
    batch_had_progress = True
    if on_tool_result is not None:
        on_tool_result(loop_state)
    if not regular_tool_calls:
        if signature not in set(loop_state.seen_signatures):
            loop_state.seen_signatures.append(signature)
        _emit_iteration_event(
            loop_ctx=loop_ctx,
            profile=profile,
            loop_state=loop_state,
            llm_duration_ms=iter_llm_duration_ms,
            tool_records=iter_tool_records,
            tokens_used=iter_input_tokens + iter_output_tokens,
        )
        return (
            [],
            batch_had_progress,
            LoopDispatchResult(
                tool_calls=[],
                ordered_tool_results=[],
                cached_indices=frozenset(),
                iter_batch_parallel_count=0,
                dispatch_budget_managed=False,
                batch_had_progress=batch_had_progress,
                continue_loop=True,
                outcome=None,
            ),
        )
    return regular_tool_calls, batch_had_progress, None


def _process_tool_request_calls(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    tool_calls: list[Any],
    public_mode_tag: str,
    signature: str,
    iter_tool_records: list[IterationToolCallRecord],
    iter_llm_duration_ms: int,
    iter_input_tokens: int,
    iter_output_tokens: int,
    on_tool_result: Any,
    set_turn_progress: Any,
    active_tool_specs: list[Any],
    active_tool_names: set[str],
    requestable_specs: list[Any],
    requestable_specs_by_name: dict[str, Any],
) -> tuple[list[Any], bool, LoopDispatchResult | None]:
    tool_request_calls = [
        tool_call
        for tool_call in tool_calls
        if str(getattr(tool_call, "name", "") or "").strip() == TOOL_REQUEST_TOOL_NAME
    ]
    if not tool_request_calls:
        return tool_calls, False, None
    regular_tool_calls = [
        tool_call
        for tool_call in tool_calls
        if str(getattr(tool_call, "name", "") or "").strip() != TOOL_REQUEST_TOOL_NAME
    ]
    activated_any = False
    requested_tools = list(
        loop_state.scratchpad.get("tool_schema_shortlisting.requested_tools", []) or []
    )
    for tool_call in tool_request_calls:
        arguments = dict(getattr(tool_call, "arguments", {}) or {})
        requested_name = str(arguments.get("name", "") or "").strip()
        action_result, activated = _tool_request_result(
            requested_name=requested_name,
            active_tool_names=active_tool_names,
            requestable_specs_by_name=requestable_specs_by_name,
            active_tool_specs=active_tool_specs,
        )
        activated_any = activated_any or activated
        active_tool_specs[:] = with_enabled_plan_tool_spec(profile, active_tool_specs)
        requested_tools.append(requested_name)
        loop_state.messages.append(
            action_result_to_tool_message(
                getattr(tool_call, "id", None),
                TOOL_REQUEST_TOOL_NAME,
                action_result,
            )
        )
        loop_state.tool_calls_made.append(TOOL_REQUEST_TOOL_NAME)
        loop_state.total_tool_calls += 1
        iter_tool_records.append(
            IterationToolCallRecord(
                tool_name=TOOL_REQUEST_TOOL_NAME,
                duration_ms=0,
                status=str(getattr(action_result, "status", "") or ""),
                cache_hit=False,
                parallel=False,
            )
        )
        set_turn_progress(
            loop_state,
            llm_call_count=loop_state.llm_calls,
            llm_call_limit=_effective_cap(profile, loop_state),
            progress_phase="tool",
            tool_name=TOOL_REQUEST_TOOL_NAME,
        )
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} tool {TOOL_REQUEST_TOOL_NAME}",
            mode_state="tool_call",
            extra={
                "tool_name": TOOL_REQUEST_TOOL_NAME,
                "requested_tool_name": requested_name,
                "activated": activated,
            },
        )
    loop_state.scratchpad["tool_schema_shortlisting.requested_tools"] = requested_tools
    loop_state.scratchpad["tool_schema_shortlisting.active_tools"] = sorted(
        active_tool_names
    )
    if activated_any:
        inactive_directory_message = build_inactive_tool_directory_message(
            requestable_tool_specs=requestable_specs,
            active_tool_names=active_tool_names,
        )
        if inactive_directory_message is not None:
            loop_state.messages.append(inactive_directory_message)
    if on_tool_result is not None:
        on_tool_result(loop_state)
    if not regular_tool_calls:
        if signature not in set(loop_state.seen_signatures):
            loop_state.seen_signatures.append(signature)
        _emit_iteration_event(
            loop_ctx=loop_ctx,
            profile=profile,
            loop_state=loop_state,
            llm_duration_ms=iter_llm_duration_ms,
            tool_records=iter_tool_records,
            tokens_used=iter_input_tokens + iter_output_tokens,
        )
        return (
            [],
            True,
            LoopDispatchResult(
                tool_calls=[],
                ordered_tool_results=[],
                cached_indices=frozenset(),
                iter_batch_parallel_count=0,
                dispatch_budget_managed=False,
                batch_had_progress=True,
                continue_loop=True,
                outcome=None,
            ),
        )
    return regular_tool_calls, True, None


def prepare_iteration_dispatch(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    runtime: Any,
    model: str,
    tool_calls: list[Any],
    signature: str,
    allowed_tools: frozenset[str],
    public_mode_tag: str,
    active_tool_specs: list[Any],
    active_tool_names: set[str],
    requestable_specs: list[Any],
    requestable_specs_by_name: dict[str, Any],
    tool_request_enabled: bool,
    iter_tool_records: list[IterationToolCallRecord],
    iter_llm_duration_ms: int,
    iter_input_tokens: int,
    iter_output_tokens: int,
    tool_batch_runner: Any,
    loop_cache: Any,
    on_tool_result: Any,
    append_tool_result_payload: Any,
    set_turn_progress: Any,
    repair_stale_exact_date_search_args: Any,
    stale_exact_date_query_reason: Any,
) -> LoopDispatchResult:
    exact_date_result = _handle_exact_date_requirements(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        tool_calls=tool_calls,
        allowed_tools=allowed_tools,
        public_mode_tag=public_mode_tag,
        signature=signature,
        iter_tool_records=iter_tool_records,
        iter_llm_duration_ms=iter_llm_duration_ms,
        iter_input_tokens=iter_input_tokens,
        iter_output_tokens=iter_output_tokens,
        on_tool_result=on_tool_result,
        repair_stale_exact_date_search_args=repair_stale_exact_date_search_args,
        stale_exact_date_query_reason=stale_exact_date_query_reason,
        result_factory=LoopDispatchResult,
    )
    if exact_date_result is not None:
        return exact_date_result

    decompose_result = _handle_decompose_calls(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        tool_calls=tool_calls,
        allowed_tools=allowed_tools,
        public_mode_tag=public_mode_tag,
        signature=signature,
        iter_tool_records=iter_tool_records,
        iter_llm_duration_ms=iter_llm_duration_ms,
        iter_input_tokens=iter_input_tokens,
        iter_output_tokens=iter_output_tokens,
        on_tool_result=on_tool_result,
        append_tool_result_payload=append_tool_result_payload,
        set_turn_progress=set_turn_progress,
    )
    if decompose_result is not None:
        return decompose_result

    tool_calls, batch_had_progress, plan_result = _process_plan_tool_calls(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        tool_calls=tool_calls,
        public_mode_tag=public_mode_tag,
        signature=signature,
        iter_tool_records=iter_tool_records,
        iter_llm_duration_ms=iter_llm_duration_ms,
        iter_input_tokens=iter_input_tokens,
        iter_output_tokens=iter_output_tokens,
        on_tool_result=on_tool_result,
        set_turn_progress=set_turn_progress,
    )
    if plan_result is not None:
        return plan_result

    tool_calls, review_progress, review_result = _process_review_tool_calls(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        tool_calls=tool_calls,
        public_mode_tag=public_mode_tag,
        signature=signature,
        iter_tool_records=iter_tool_records,
        iter_llm_duration_ms=iter_llm_duration_ms,
        iter_input_tokens=iter_input_tokens,
        iter_output_tokens=iter_output_tokens,
        on_tool_result=on_tool_result,
        append_tool_result_payload=append_tool_result_payload,
        set_turn_progress=set_turn_progress,
    )
    if review_progress:
        batch_had_progress = True
    if review_result is not None:
        return review_result

    if tool_request_enabled:
        tool_calls, tool_request_progress, tool_request_result = (
            _process_tool_request_calls(
                loop_ctx,
                profile=profile,
                loop_state=loop_state,
                tool_calls=tool_calls,
                public_mode_tag=public_mode_tag,
                signature=signature,
                iter_tool_records=iter_tool_records,
                iter_llm_duration_ms=iter_llm_duration_ms,
                iter_input_tokens=iter_input_tokens,
                iter_output_tokens=iter_output_tokens,
                on_tool_result=on_tool_result,
                set_turn_progress=set_turn_progress,
                active_tool_specs=active_tool_specs,
                active_tool_names=active_tool_names,
                requestable_specs=requestable_specs,
                requestable_specs_by_name=requestable_specs_by_name,
            )
        )
        batch_had_progress = batch_had_progress or tool_request_progress
        if tool_request_result is not None:
            return tool_request_result

    (
        ordered_tool_results,
        cached_indices,
        iter_batch_parallel_count,
        dispatch_budget_managed,
    ) = _dispatch_tool_batches(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        tool_calls=tool_calls,
        tool_batch_runner=tool_batch_runner,
        loop_cache=loop_cache,
    )

    return LoopDispatchResult(
        tool_calls=tool_calls,
        ordered_tool_results=ordered_tool_results,
        cached_indices=cached_indices,
        iter_batch_parallel_count=iter_batch_parallel_count,
        dispatch_budget_managed=dispatch_budget_managed,
        batch_had_progress=batch_had_progress,
        continue_loop=False,
        outcome=None,
    )
