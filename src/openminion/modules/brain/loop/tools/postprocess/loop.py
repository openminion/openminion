from __future__ import annotations

from typing import Any

from ..contracts import AdaptiveToolLoopContext, AdaptiveToolLoopProfile
from ..snapshot import LoopSnapshot, LoopToolCallRecord, compress_transcript
from ..telemetry import _emit_iteration_event
from openminion.modules.brain.constants import STATE_KEY_MODULE_STATE


def finalize_iteration_state(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: Any,
    batch_had_progress: bool,
    signature: str,
    ordered_tool_results: list[tuple[Any, Any]],
    tool_calls: list[Any],
    prefetch_predictor: Any,
    prefetch_pending: Any,
    loop_cache: Any,
    loop_profiler: Any,
    iter_llm_duration_ms: int,
    iter_tool_records: list[Any],
    iter_input_tokens: int,
    iter_output_tokens: int,
    turn_scope_id: Any,
    model: str,
    public_mode_name: str,
    record_duplicate_batch_execution_facts: Any,
    direct_tool_batch_completed_successfully: Any,
) -> Any:
    if batch_had_progress and signature not in set(loop_state.seen_signatures):
        loop_state.seen_signatures.append(signature)
    if batch_had_progress:
        record_duplicate_batch_execution_facts(
            loop_state,
            signature=signature,
            ordered_tool_results=ordered_tool_results,
        )
    if direct_tool_batch_completed_successfully(
        loop_state=loop_state,
        signature=signature,
        ordered_tool_results=ordered_tool_results,
        profile=profile,
    ):
        loop_state.direct_tool_requested_batch_satisfied = True

    if prefetch_predictor is not None:
        iter_tool_names = [
            str(getattr(tc, "name", "") or "").strip() for tc in tool_calls
        ]
        if prefetch_pending is not None and iter_tool_names:
            prefetch_predictor.record_outcome(prefetch_pending, iter_tool_names[0])
            prefetch_pending = None
        prefetch_predictor.observe(iter_tool_names)
        pred_tool, pred_conf = prefetch_predictor.predict(
            list(loop_state.tool_calls_made)
        )
        if pred_tool is not None and pred_conf >= float(
            profile.speculative_prefetch_threshold
        ):
            prefetch_pending = pred_tool
        loop_state.scratchpad["loop.prefetch_correct"] = prefetch_predictor.correct
        loop_state.scratchpad["loop.prefetch_wrong"] = prefetch_predictor.wrong

    loop_state.scratchpad["loop.cache_hits"] = loop_cache.hits
    loop_state.scratchpad["loop.cache_misses"] = loop_cache.misses

    loop_profiler.record_cache(
        hits=loop_cache.hits - (loop_state.scratchpad.get("_profiler_prev_hits", 0)),
        misses=loop_cache.misses
        - (loop_state.scratchpad.get("_profiler_prev_misses", 0)),
    )
    loop_state.scratchpad["_profiler_prev_hits"] = loop_cache.hits
    loop_state.scratchpad["_profiler_prev_misses"] = loop_cache.misses
    loop_profiler.record_iteration(profile.profile_name, iter_llm_duration_ms)

    _emit_iteration_event(
        loop_ctx=loop_ctx,
        profile=profile,
        loop_state=loop_state,
        llm_duration_ms=iter_llm_duration_ms,
        tool_records=iter_tool_records,
        tokens_used=iter_input_tokens + iter_output_tokens,
    )

    ctx_state = getattr(loop_ctx, "state", None)
    if ctx_state is not None and hasattr(ctx_state, STATE_KEY_MODULE_STATE):
        snapshot = LoopSnapshot(
            iteration_index=loop_state.iteration,
            message_transcript=compress_transcript(
                [
                    {
                        "role": str(getattr(message, "role", "") or ""),
                        "content": str(getattr(message, "content", "") or ""),
                    }
                    for message in loop_state.messages
                ]
            ),
            tool_call_history=[
                LoopToolCallRecord(tool_name=tc, args_hash="", result_summary="")
                for tc in loop_state.tool_calls_made
            ],
            budgets_consumed={
                "llm_calls": loop_state.llm_calls,
                "tool_calls": loop_state.total_tool_calls,
            },
            turn_scope_id=turn_scope_id,
            profile_name=profile.profile_name,
            model=model,
            allowed_tools=profile.allowed_tools or frozenset(),
            tool_results=[
                item
                for item in list(
                    loop_state.scratchpad.get("adaptive.tool_results", []) or []
                )
                if isinstance(item, dict)
            ][-24:],
        )
        module_state = dict(getattr(ctx_state, STATE_KEY_MODULE_STATE, {}) or {})
        module_state["adaptive_loop"] = snapshot.to_dict()
        ctx_state.module_state = module_state

    return prefetch_pending
