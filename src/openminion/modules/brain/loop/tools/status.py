from __future__ import annotations

from typing import Any

from openminion.modules.brain.execution.public_taxonomy import (
    public_mode_name_for_state,
    public_surface_payload_for_mode_name,
    public_surface_payload_for_state,
)

from .contracts import (
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
    loop_parallel_payload,
    loop_turn_progress_payload,
)


def loop_reflection_payload(scratchpad: dict) -> dict:
    return {
        "loop.reflection_calls": scratchpad.get("reflection_calls", 0),
        "loop.reflection_triggers": scratchpad.get("reflection_triggers", []),
    }


def loop_resume_payload(scratchpad: dict) -> dict:
    return {
        "loop.resumed_from_snapshot": scratchpad.get("resumed_from_snapshot", False),
        "loop.resume_iteration_index": scratchpad.get("resume_iteration_index"),
    }


def loop_correction_payload(scratchpad: dict) -> dict:
    history = scratchpad.get("correction_history", [])
    type_counts: dict[str, int] = {}
    for record in history:
        ct = (
            record.get("correction_type", "unknown")
            if isinstance(record, dict)
            else getattr(record, "correction_type", "unknown")
        )
        type_counts[ct] = type_counts.get(ct, 0) + 1

    return {
        "loop.micro_corrections": scratchpad.get("micro_correction_count", 0),
        "loop.macro_corrections": scratchpad.get("macro_correction_count", 0),
        "loop.correction_types": type_counts,
        "loop.correction_budget_remaining": max(
            0,
            scratchpad.get("max_macro_corrections", 0)
            - scratchpad.get("macro_correction_count", 0),
        ),
        "loop.correction_history_length": len(history),
    }


def adaptive_status_payload(
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    working_state: Any | None = None,
    termination_reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    public_surface = (
        public_surface_payload_for_state(working_state, mode_name=profile.mode_name)
        if working_state is not None
        else public_surface_payload_for_mode_name(profile.mode_name)
    )
    public_mode_name = (
        str(public_surface.pop("mode_name", "") or "").strip() or profile.mode_name
    )
    payload = {
        "adaptive.profile": profile.profile_name,
        "adaptive.mode": public_mode_name,
        "adaptive.iteration": loop_state.iteration,
        "adaptive.llm_calls": loop_state.llm_calls,
        "adaptive.tool_calls": list(loop_state.tool_calls_made),
        "adaptive.tool_calls_total": loop_state.total_tool_calls,
        "adaptive.allowed_tools": sorted(profile.allowed_tools or ()),
        "intent.mode": public_mode_name,
    }
    if act_profile := public_surface.get("act_profile"):
        payload["act.profile"] = act_profile
    if execution_target := public_surface.get("execution_target"):
        payload["execution.target"] = execution_target
    if loop_phase := public_surface.get("loop_phase"):
        payload["loop.phase"] = loop_phase
    payload.update(loop_parallel_payload(loop_state.scratchpad))
    payload.update(loop_turn_progress_payload(loop_state.scratchpad))
    payload.update(loop_reflection_payload(loop_state.scratchpad))
    payload.update(loop_resume_payload(loop_state.scratchpad))
    payload.update(loop_correction_payload(loop_state.scratchpad))
    if termination_reason:
        payload["adaptive.termination_reason"] = termination_reason
    if extra:
        payload.update(dict(extra))
    return payload


def adaptive_outcome_payload(outcome: AdaptiveToolLoopOutcome) -> dict[str, Any]:
    return outcome.telemetry_payload()


def emit_adaptive_status(
    loop_ctx: Any,
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    detail_text: str,
    mode_state: str,
    terminal: bool = False,
    termination_reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    public_mode_name = (
        public_mode_name_for_state(
            getattr(loop_ctx, "state", None),
            mode_name=profile.mode_name,
        )
        or profile.mode_name
    )
    loop_ctx.emit_status(
        source_phase="ACT",
        detail_text=detail_text,
        mode=public_mode_name,
        mode_state=mode_state,
        terminal=terminal,
        mode_step_index=loop_state.iteration,
        mode_step_total=profile.max_iterations,
        payload=adaptive_status_payload(
            profile=profile,
            loop_state=loop_state,
            working_state=getattr(loop_ctx, "state", None),
            termination_reason=termination_reason,
            extra=extra,
        ),
    )
