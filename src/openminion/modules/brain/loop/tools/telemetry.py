from __future__ import annotations

from typing import Any

from openminion.modules.brain.execution.public_taxonomy import (
    public_mode_name_for_mode_name,
)

from .contracts import (
    AdaptiveToolLoopContext,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
)
from .events import AdaptiveLoopIterationEvent, IterationToolCallRecord


def _public_loop_tag(mode_name: str) -> str:
    public_name = public_mode_name_for_mode_name(mode_name) or mode_name
    return f"[{public_name}]"


def _current_turn_scope_id(loop_ctx: AdaptiveToolLoopContext) -> str:
    state = loop_ctx.state
    trace_id = str(state.trace_id or "").strip()
    if trace_id:
        return trace_id
    from openminion.modules.brain.schemas import new_uuid  # noqa: PLC0415

    trace_id = new_uuid()
    state.trace_id = trace_id
    return trace_id


def _accumulate_parallel_telemetry(
    loop_state: AdaptiveToolLoopState,
    *,
    parallel_fan_out_count: int,
    tool_calls_parallel: int,
    tool_calls_sequential: int,
) -> None:
    scratchpad = loop_state.scratchpad
    scratchpad["loop.parallel_fan_out_count"] = (
        scratchpad.get("loop.parallel_fan_out_count", 0) + parallel_fan_out_count
    )
    scratchpad["loop.tool_calls_parallel"] = (
        scratchpad.get("loop.tool_calls_parallel", 0) + tool_calls_parallel
    )
    scratchpad["loop.tool_calls_sequential"] = (
        scratchpad.get("loop.tool_calls_sequential", 0) + tool_calls_sequential
    )


def _emit_iteration_event(
    loop_ctx: Any,
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    llm_duration_ms: int,
    tool_records: list[IterationToolCallRecord],
    tokens_used: int,
) -> None:
    public_mode_name = (
        public_mode_name_for_mode_name(profile.mode_name) or profile.mode_name
    )
    _budgets = getattr(loop_ctx.state, "budgets_remaining", None)
    _budget_remaining_dict: dict[str, Any] = {}
    if _budgets is not None:
        _budget_remaining_dict = {
            "tokens": int(getattr(_budgets, "tokens", 0) or 0),
            "tool_calls": int(getattr(_budgets, "tool_calls", 0) or 0),
            "llm_calls_used": int(getattr(loop_ctx.state, "llm_calls_used", 0) or 0),
            "llm_calls_max": int(getattr(loop_ctx.state, "llm_calls_max", 0) or 0),
        }
    _reflection_triggered = bool(
        loop_state.scratchpad.get("reflection_triggers")
        and any(
            t.get("iteration") == loop_state.iteration
            for t in loop_state.scratchpad["reflection_triggers"]
            if isinstance(t, dict)
        )
    )
    _event = AdaptiveLoopIterationEvent(
        iteration_index=loop_state.iteration,
        llm_call_duration_ms=llm_duration_ms,
        tool_calls=tuple(tool_records),
        tokens_used_this_iteration=tokens_used,
        budget_remaining=_budget_remaining_dict,
        reflection_triggered=_reflection_triggered,
        termination_reason=None,
    )
    loop_ctx.emit_status(
        source_phase="ACT",
        source_event="adaptive_loop_iteration",
        payload=_event.to_dict(),
        mode=public_mode_name,
        mode_state="iterating",
        mode_step_index=loop_state.iteration,
        mode_step_total=profile.max_iterations,
    )
