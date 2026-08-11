from __future__ import annotations

from typing import Any

from ..memory_templates import LoopTemplate
from ..contracts import (
    ADAPTIVE_TERM_CONFIDENT_COMPLETE,
    ADAPTIVE_TERM_FINAL_TEXT,
    ADAPTIVE_TERM_FINALIZATION_BLOCKED,
    ADAPTIVE_TERM_FINALIZATION_INCOMPLETE,
    ADAPTIVE_TERM_ITERATION_CAP,
    ADAPTIVE_TERM_LLM_ERROR,
    AdaptiveToolLoopOutcome,
)
from ..budget_control import _force_budget_answer_only_finalization
from ..postprocess.evidence_closeout import tool_evidence_closeout_outcome
from ..startup import _loop_template_match_tags
from ..status import emit_adaptive_status
from ..telemetry import _emit_iteration_event


def _model_payload(value: Any) -> dict[str, Any] | None:
    return value.model_dump(mode="json") if value is not None else None


def _iteration_cap_evidence_fallback(
    *,
    profile: Any,
    loop_state: Any,
    allowed_tools: frozenset[str],
) -> AdaptiveToolLoopOutcome | None:
    return tool_evidence_closeout_outcome(
        profile=profile,
        loop_state=loop_state,
        allowed_tools=allowed_tools,
        reason=(
            "the loop reached its iteration cap before a polished final answer, "
            "so preserved tool evidence is returned."
        ),
        scratchpad_key="iteration_cap_used_evidence_fallback",
    )


def _iteration_cap_macro_correction_outcome(
    *,
    loop_ctx: Any,
    profile: Any,
    loop_state: Any,
    runtime: Any,
    model: str,
    allowed_tools: frozenset[str],
    trigger_macro_correction: Any,
    dispatch_correction_plan: Any,
) -> AdaptiveToolLoopOutcome | None:
    if profile.max_macro_corrections <= 0:
        return None
    cap_plan = trigger_macro_correction(
        loop_ctx=loop_ctx,
        profile=profile,
        loop_state=loop_state,
        failure_context=(
            f"Loop reached iteration cap ({profile.max_iterations}) "
            "without producing a final text response."
        ),
        model=model,
        runtime=runtime,
        messages=loop_state.messages,
    )
    if cap_plan is None:
        return None
    try:
        cap_dispatch = dispatch_correction_plan(
            plan=cap_plan,
            loop_ctx=loop_ctx,
            loop_state=loop_state,
            messages=loop_state.messages,
            last_tool_call=None,
            profile=profile,
        )
    except ValueError:
        cap_dispatch = ADAPTIVE_TERM_LLM_ERROR
    if cap_dispatch is None:
        return None
    loop_state.termination_reason = cap_dispatch
    return AdaptiveToolLoopOutcome(
        profile_name=profile.profile_name,
        mode_name=profile.mode_name,
        termination_reason=cap_dispatch,
        state=loop_state,
        allowed_tools=allowed_tools,
    )


def finalize_iteration_cap_exit(
    loop_ctx: Any,
    *,
    profile: Any,
    loop_state: Any,
    runtime: Any,
    model: str,
    allowed_tools: frozenset[str],
    public_mode_name: str,
    public_mode_tag: str,
    max_output_tokens: int | None,
    metadata: dict[str, Any] | None,
    loop_profiler: Any,
    trigger_macro_correction: Any,
    dispatch_correction_plan: Any,
) -> AdaptiveToolLoopOutcome:
    finalization_outcome = _force_budget_answer_only_finalization(
        loop_ctx=loop_ctx,
        profile=profile,
        loop_state=loop_state,
        runtime=runtime,
        model=model,
        max_output_tokens=max_output_tokens,
        metadata=metadata,
        allowed_tools=allowed_tools,
        public_mode_tag=public_mode_tag,
    )
    if finalization_outcome is not None:
        if loop_state.scratchpad.get("budget_answer_only_finalization_error"):
            loop_state.termination_reason = ADAPTIVE_TERM_ITERATION_CAP
            return AdaptiveToolLoopOutcome(
                profile_name=profile.profile_name,
                mode_name=profile.mode_name,
                termination_reason=ADAPTIVE_TERM_ITERATION_CAP,
                state=loop_state,
                allowed_tools=allowed_tools,
                error_message=finalization_outcome.error_message,
            )
        if finalization_outcome.termination_reason != ADAPTIVE_TERM_LLM_ERROR:
            loop_state.scratchpad["iteration_cap_answer_only_finalization_forced"] = (
                True
            )
            return finalization_outcome

    macro_outcome = _iteration_cap_macro_correction_outcome(
        loop_ctx=loop_ctx,
        profile=profile,
        loop_state=loop_state,
        runtime=runtime,
        model=model,
        allowed_tools=allowed_tools,
        trigger_macro_correction=trigger_macro_correction,
        dispatch_correction_plan=dispatch_correction_plan,
    )
    if macro_outcome is not None:
        return macro_outcome

    fallback_outcome = _iteration_cap_evidence_fallback(
        profile=profile,
        loop_state=loop_state,
        allowed_tools=allowed_tools,
    )
    if fallback_outcome is not None:
        return fallback_outcome

    profiler_summary = loop_profiler.summary()
    loop_state.scratchpad["loop.session_profile_summary"] = profiler_summary
    loop_ctx.emit_status(
        source_phase="ACT",
        source_event="loop.session_profile_summary",
        payload=profiler_summary,
        mode=public_mode_name,
        mode_state="loop_end",
        mode_step_index=loop_state.iteration,
        mode_step_total=profile.max_iterations,
    )

    loop_state.termination_reason = ADAPTIVE_TERM_ITERATION_CAP
    emit_adaptive_status(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        detail_text=f"{public_mode_tag} iteration cap reached",
        mode_state="iteration_cap",
        termination_reason=ADAPTIVE_TERM_ITERATION_CAP,
    )
    return AdaptiveToolLoopOutcome(
        profile_name=profile.profile_name,
        mode_name=profile.mode_name,
        termination_reason=ADAPTIVE_TERM_ITERATION_CAP,
        state=loop_state,
        allowed_tools=allowed_tools,
    )


def build_no_tool_outcome(
    loop_ctx: Any,
    *,
    profile: Any,
    loop_state: Any,
    allowed_tools: frozenset[str],
    final_text: str,
    confident_complete: Any,
    finalization_status: Any,
    pending_turn_context: Any,
    meta_rule_preference: Any,
    memory_consolidation: Any,
    session_work_summary: Any,
    goal_declaration: Any,
    goal_revision: Any,
    delegation_context: Any,
    delegation_result_summary: Any,
    task_plan: Any,
    task_plan_step_completed: Any,
    task_plan_step_blocked: Any,
    task_plan_revision: Any,
    task_plan_abandoned: Any,
    task_plan_completed: Any,
    watch_outcome: Any,
    llm_duration_ms: int,
    tokens_used: int,
    finalizer: Any,
) -> AdaptiveToolLoopOutcome:
    if finalization_status is None:
        termination_reason = (
            ADAPTIVE_TERM_CONFIDENT_COMPLETE
            if (
                confident_complete is not None
                and confident_complete.complete
                and str(final_text or "").strip()
            )
            else ADAPTIVE_TERM_FINAL_TEXT
        )
    else:
        termination_reason = {
            "blocked": ADAPTIVE_TERM_FINALIZATION_BLOCKED,
            "incomplete": ADAPTIVE_TERM_FINALIZATION_INCOMPLETE,
        }.get(finalization_status.status, ADAPTIVE_TERM_FINAL_TEXT)
    loop_state.termination_reason = termination_reason
    _emit_iteration_event(
        loop_ctx=loop_ctx,
        profile=profile,
        loop_state=loop_state,
        llm_duration_ms=llm_duration_ms,
        tool_records=[],
        tokens_used=tokens_used,
    )
    outcome = AdaptiveToolLoopOutcome(
        profile_name=profile.profile_name,
        mode_name=profile.mode_name,
        termination_reason=termination_reason,
        state=loop_state,
        allowed_tools=allowed_tools,
        final_text=final_text,
        pending_turn_context=_model_payload(pending_turn_context),
        confident_complete_reasoning=(
            confident_complete.reasoning
            if confident_complete is not None and confident_complete.complete
            else None
        ),
        finalization_status=_model_payload(finalization_status),
        meta_rule_preference=_model_payload(meta_rule_preference),
        memory_consolidation_decisions=(
            [item.model_dump(mode="json") for item in memory_consolidation.decisions]
            if memory_consolidation is not None
            else None
        ),
        session_work_summary=(
            session_work_summary.summary if session_work_summary is not None else None
        ),
        goal_declaration=_model_payload(goal_declaration),
        goal_revision=_model_payload(goal_revision),
        delegation_context=_model_payload(delegation_context),
        delegation_result_summary=_model_payload(delegation_result_summary),
        task_plan=_model_payload(task_plan),
        task_plan_step_completed=_model_payload(task_plan_step_completed),
        task_plan_step_blocked=_model_payload(task_plan_step_blocked),
        task_plan_revision=_model_payload(task_plan_revision),
        task_plan_abandoned=_model_payload(task_plan_abandoned),
        task_plan_completed=_model_payload(task_plan_completed),
        watch_condition_met=(
            watch_outcome.condition_met if watch_outcome is not None else None
        ),
        watch_summary=watch_outcome.summary if watch_outcome is not None else None,
    )
    if profile.final_closure_policy == "engine_single_pass" and finalizer is not None:
        outcome.mode_result = finalizer(outcome)
    if profile.use_memory_templates:
        new_template = LoopTemplate(
            match_tags=_loop_template_match_tags(loop_ctx),
            tool_sequence=tuple(loop_state.tool_calls_made),
            avg_iterations=float(loop_state.iteration),
            success=True,
        )
        template_payload = new_template.to_dict()
        stored_templates = list(loop_state.scratchpad.get("loop_templates", []))
        stored_templates.append(template_payload)
        loop_state.scratchpad["loop_template"] = template_payload
        loop_state.scratchpad["loop_templates"] = stored_templates
    return outcome
