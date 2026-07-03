from __future__ import annotations

from typing import Any, NamedTuple

from openminion.modules.tool.contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_BLOCKED,
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_ACTION_STATUS_TIMEOUT,
)
from openminion.modules.brain.loop.constants import (
    PLAN_TOOL_LAST_SUBSTANTIVE_COUNT_SCRATCHPAD_KEY,
)

from ..contracts import (
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_JOB_PENDING,
    ADAPTIVE_TERM_LLM_ERROR,
    ADAPTIVE_TERM_NEEDS_USER,
    ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
    AdaptiveToolLoopContext,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
    canonical_tool_call_signature,
)
from ..evidence import _count_substantive_non_control_tool_results
from ..confirmation import (
    attach_confirmation_replay_queue,
    confirmation_required_user_message,
)
from ..events import IterationToolCallRecord
from ..messages import action_result_to_tool_message
from ..plan_control import is_plan_family_tool_name
from ..reflection import detect_anomaly
from ..status import emit_adaptive_status


MICRO_CORRECTION_ANOMALY_THRESHOLD = 0.5


class LoopExecutionResult(NamedTuple):
    """Outputs of the per-iteration execution phase."""

    batch_had_progress: bool
    outcome: AdaptiveToolLoopOutcome | None


def _error_code(action_result: Any) -> str:
    raw_error = getattr(action_result, "error", None)
    if isinstance(raw_error, dict):
        return str(raw_error.get("code", "") or "").strip().upper()
    return str(getattr(raw_error, "code", "") or "").strip().upper()


def _is_structured_policy_recoverable(action_result: Any) -> bool:
    if _error_code(action_result) != "POLICY_DENIED":
        return False
    raw_error = getattr(action_result, "error", None)
    details = (
        raw_error.get("details")
        if isinstance(raw_error, dict)
        else getattr(raw_error, "details", None)
    )
    if not isinstance(details, dict):
        return False
    return bool(str(details.get("suggested_tool", "") or "").strip())


def _is_confirm_required(action_result: Any) -> bool:
    return (
        str(getattr(action_result, "status", "") or "").strip()
        == BRAIN_ACTION_STATUS_NEEDS_USER
        and _error_code(action_result) == TOOL_ERROR_CONFIRM_REQUIRED
    )


def _record_plan_family_call(
    loop_state: AdaptiveToolLoopState,
    *,
    tool_name: str,
    action_result: Any,
) -> None:
    if not is_plan_family_tool_name(tool_name):
        return
    if (
        str(getattr(action_result, "status", "") or "").strip()
        != BRAIN_ACTION_STATUS_SUCCESS
    ):
        return
    loop_state.scratchpad[PLAN_TOOL_LAST_SUBSTANTIVE_COUNT_SCRATCHPAD_KEY] = (
        _count_substantive_non_control_tool_results(loop_state)
    )


def execute_iteration_results(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    runtime: Any,
    model: str,
    max_output_tokens: Any,
    metadata: dict[str, Any] | None,
    allowed_tools: frozenset[str],
    public_mode_tag: str,
    signature: str,
    ordered_tool_results: list[tuple[Any, Any]],
    cached_indices: frozenset[int],
    iter_batch_parallel_count: int,
    dispatch_budget_managed: bool,
    initial_batch_had_progress: bool,
    loop_cache: Any,
    loop_profiler: Any,
    on_tool_result: Any,
    iter_tool_records: list[IterationToolCallRecord],
    append_tool_result_payload: Any,
    set_turn_progress: Any,
    effective_cap: Any,
    debit_tool_budget: Any,
    profile_budget_exhausted: Any,
    tool_budget_exhausted_for_answer_only: Any,
    force_budget_answer_only_finalization: Any,
    build_missing_action_result: Any,
    build_tool_failure_recovery_message: Any,
    build_enrichment_message: Any,
    direct_tool_turn_active: Any,
    trigger_macro_correction: Any,
    dispatch_correction_plan: Any,
) -> LoopExecutionResult:
    batch_had_progress = initial_batch_had_progress
    iter_tc_idx = 0
    for result_index, (tool_call, command_outcome) in enumerate(ordered_tool_results):
        tool_name = str(getattr(tool_call, "name", "") or "").strip()
        iter_tc_cache_hit = iter_tc_idx in cached_indices
        iter_tc_parallel = not iter_tc_cache_hit and iter_batch_parallel_count > 0
        iter_tc_idx += 1
        set_turn_progress(
            loop_state,
            llm_call_count=loop_state.llm_calls,
            llm_call_limit=effective_cap(profile, loop_state),
            progress_phase="tool",
            tool_name=tool_name,
        )
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} tool {tool_name}",
            mode_state="tool_call",
            extra={"tool_name": tool_name},
        )
        loop_state.tool_calls_made.append(tool_name)
        loop_state.total_tool_calls += 1
        if not (dispatch_budget_managed and not iter_tc_cache_hit):
            debit_tool_budget(loop_ctx)

        action_result = command_outcome.action_result or build_missing_action_result(
            tool_name
        )
        append_tool_result_payload(
            loop_state,
            tool_name=tool_name,
            action_result=action_result,
        )
        _record_plan_family_call(loop_state, tool_name=tool_name, action_result=action_result)

        tc_args_for_cache = dict(getattr(tool_call, "arguments", {}) or {})
        loop_cache.invalidate_for_write(tool_name, tc_args_for_cache)
        loop_cache.put(tool_name, tc_args_for_cache, command_outcome)

        iter_tool_records.append(
            IterationToolCallRecord(
                tool_name=tool_name,
                duration_ms=0,
                status=str(getattr(action_result, "status", "") or ""),
                cache_hit=iter_tc_cache_hit,
                parallel=iter_tc_parallel,
            )
        )
        loop_profiler.record_tool_call(tool_name, 0)

        if command_outcome.job is not None and profile.stop_on_job_pending:
            loop_state.termination_reason = ADAPTIVE_TERM_JOB_PENDING
            emit_adaptive_status(
                loop_ctx,
                profile=profile,
                loop_state=loop_state,
                detail_text=f"{public_mode_tag} job pending",
                mode_state="job_pending",
                termination_reason=ADAPTIVE_TERM_JOB_PENDING,
            )
            return LoopExecutionResult(
                batch_had_progress=batch_had_progress,
                outcome=AdaptiveToolLoopOutcome(
                    profile_name=profile.profile_name,
                    mode_name=profile.mode_name,
                    termination_reason=ADAPTIVE_TERM_JOB_PENDING,
                    state=loop_state,
                    allowed_tools=allowed_tools,
                    action_result=action_result,
                    job=command_outcome.job,
                ),
            )

        if (
            action_result.status == BRAIN_ACTION_STATUS_NEEDS_USER
            and profile.stop_on_needs_user
        ):
            if _is_confirm_required(action_result):
                pending_command = getattr(command_outcome, "approved_command", None)
                queued_siblings = []
                for _later_tool_call, later_outcome in ordered_tool_results[
                    result_index + 1 :
                ]:
                    later_action_result = getattr(later_outcome, "action_result", None)
                    later_command = getattr(later_outcome, "approved_command", None)
                    if (
                        later_action_result is not None
                        and later_command is not None
                        and _is_confirm_required(later_action_result)
                    ):
                        queued_siblings.append(later_command.model_copy(deep=True))
                if pending_command is not None:
                    pending_confirmation = pending_command.model_copy(deep=True)
                    if queued_siblings:
                        loop_ctx.state.pending_confirmation_command = (
                            attach_confirmation_replay_queue(
                                pending_confirmation, queued_siblings
                            )
                        )
                    else:
                        loop_ctx.state.pending_confirmation_command = (
                            pending_confirmation
                        )
                    loop_ctx.state.post_action_user_message = (
                        confirmation_required_user_message(
                            loop_ctx.state.pending_confirmation_command
                        )
                    )
            loop_state.termination_reason = ADAPTIVE_TERM_NEEDS_USER
            emit_adaptive_status(
                loop_ctx,
                profile=profile,
                loop_state=loop_state,
                detail_text=f"{public_mode_tag} waiting for approval",
                mode_state="needs_user",
                termination_reason=ADAPTIVE_TERM_NEEDS_USER,
            )
            return LoopExecutionResult(
                batch_had_progress=batch_had_progress,
                outcome=AdaptiveToolLoopOutcome(
                    profile_name=profile.profile_name,
                    mode_name=profile.mode_name,
                    termination_reason=ADAPTIVE_TERM_NEEDS_USER,
                    state=loop_state,
                    allowed_tools=allowed_tools,
                    action_result=action_result,
                ),
            )

        if (
            action_result.status == BRAIN_ACTION_STATUS_BLOCKED
            and getattr(getattr(action_result, "error", None), "code", "")
            == "BUDGET_EXCEEDED"
        ):
            loop_state.messages.append(
                action_result_to_tool_message(
                    getattr(tool_call, "id", None),
                    tool_name,
                    action_result,
                )
            )
            budget_finalization_outcome = force_budget_answer_only_finalization(
                loop_ctx=loop_ctx,
                profile=profile,
                loop_state=loop_state,
                runtime=runtime,
                model=model,
                max_output_tokens=int(max_output_tokens)
                if max_output_tokens is not None
                else None,
                metadata=metadata,
                allowed_tools=allowed_tools,
                public_mode_tag=public_mode_tag,
            )
            if budget_finalization_outcome is not None:
                return LoopExecutionResult(
                    batch_had_progress=batch_had_progress,
                    outcome=budget_finalization_outcome,
                )
            loop_state.termination_reason = ADAPTIVE_TERM_BUDGET_EXHAUSTED
            emit_adaptive_status(
                loop_ctx,
                profile=profile,
                loop_state=loop_state,
                detail_text=f"{public_mode_tag} budget exhausted",
                mode_state="budget_exhausted",
                termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            )
            return LoopExecutionResult(
                batch_had_progress=batch_had_progress,
                outcome=AdaptiveToolLoopOutcome(
                    profile_name=profile.profile_name,
                    mode_name=profile.mode_name,
                    termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
                    state=loop_state,
                    allowed_tools=allowed_tools,
                    action_result=action_result,
                ),
            )

        loop_state.messages.append(
            action_result_to_tool_message(
                getattr(tool_call, "id", None),
                tool_name,
                action_result,
            )
        )
        recovery_message = build_tool_failure_recovery_message(
            tool_name=tool_name,
            action_result=action_result,
        )
        if (
            recovery_message is not None
            and profile.allow_llm_recovery_after_tool_failure
        ):
            loop_state.messages.append(recovery_message)
        batch_had_progress = True
        if on_tool_result is not None:
            on_tool_result(loop_state)

        if profile.reflection_policy == "anomaly":
            tool_history = [
                message
                for message in loop_state.messages
                if getattr(message, "_tool_name", None) == tool_name
            ]
            score = detect_anomaly(
                result=action_result,
                history=tool_history,
            )
            triggered = score.score >= profile.reflection_anomaly_threshold
            if triggered:
                loop_state.scratchpad["reflection_calls"] = (
                    loop_state.scratchpad.get("reflection_calls", 0) + 1
                )
            triggers = loop_state.scratchpad.setdefault("reflection_triggers", [])
            triggers.append(
                {
                    "iteration": loop_state.iteration,
                    "tool_name": tool_name,
                    "anomaly_score": score.score,
                    "triggered": triggered,
                }
            )

        micro_score = detect_anomaly(
            result=action_result,
            history=[],
        )
        if micro_score.score >= MICRO_CORRECTION_ANOMALY_THRESHOLD:
            result_summary = str(getattr(action_result, "summary", "") or "")
            loop_state.messages.append(
                build_enrichment_message(
                    tool_name=tool_name,
                    score=micro_score.score,
                    result_summary=result_summary,
                )
            )
            loop_state.scratchpad["micro_correction_count"] = (
                loop_state.scratchpad.get("micro_correction_count", 0) + 1
            )
            current_sig = canonical_tool_call_signature(tool_call)
            last_anomalous_sig = loop_state.scratchpad.get("last_anomalous_signature")
            if last_anomalous_sig is not None and current_sig == last_anomalous_sig:
                loop_state.scratchpad["layer2_escalation_needed"] = True
            loop_state.scratchpad["last_anomalous_signature"] = current_sig

            if loop_state.scratchpad.get("layer2_escalation_needed"):
                failure_ctx = (
                    f"Tool {tool_name!r} has produced anomalous output "
                    f"(score: {micro_score.score:.2f}) twice in a row. "
                    f"Last result: {str(getattr(action_result, 'summary', ''))[:300]}"
                )
                plan = trigger_macro_correction(
                    loop_ctx=loop_ctx,
                    profile=profile,
                    loop_state=loop_state,
                    failure_context=failure_ctx,
                    model=model,
                    runtime=runtime,
                    messages=loop_state.messages,
                )
                if plan is not None:
                    loop_state.scratchpad["layer2_escalation_needed"] = False
                    try:
                        dispatch_result = dispatch_correction_plan(
                            plan=plan,
                            loop_ctx=loop_ctx,
                            loop_state=loop_state,
                            messages=loop_state.messages,
                            last_tool_call=tool_call,
                            profile=profile,
                        )
                    except ValueError:
                        dispatch_result = ADAPTIVE_TERM_LLM_ERROR
                    if dispatch_result is not None:
                        loop_state.termination_reason = dispatch_result
                        return LoopExecutionResult(
                            batch_had_progress=batch_had_progress,
                            outcome=AdaptiveToolLoopOutcome(
                                profile_name=profile.profile_name,
                                mode_name=profile.mode_name,
                                termination_reason=dispatch_result,
                                state=loop_state,
                                allowed_tools=allowed_tools,
                            ),
                        )

        layer2_severe = (
            micro_score.score >= 0.9
            and loop_state.scratchpad.get("macro_correction_count", 0)
            < profile.max_macro_corrections
            and not profile.allow_llm_recovery_after_tool_failure
        )
        if layer2_severe:
            failure_ctx = (
                f"Tool {tool_name!r} returned a severe anomaly "
                f"(score: {micro_score.score:.2f}) on first occurrence. "
                f"Last result: {str(getattr(action_result, 'summary', ''))[:300]}"
            )
            plan = trigger_macro_correction(
                loop_ctx=loop_ctx,
                profile=profile,
                loop_state=loop_state,
                failure_context=failure_ctx,
                model=model,
                runtime=runtime,
                messages=loop_state.messages,
            )
            if plan is not None:
                try:
                    dispatch_result = dispatch_correction_plan(
                        plan=plan,
                        loop_ctx=loop_ctx,
                        loop_state=loop_state,
                        messages=loop_state.messages,
                        last_tool_call=tool_call,
                        profile=profile,
                    )
                except ValueError:
                    dispatch_result = ADAPTIVE_TERM_LLM_ERROR
                if dispatch_result is not None:
                    loop_state.termination_reason = dispatch_result
                    return LoopExecutionResult(
                        batch_had_progress=batch_had_progress,
                        outcome=AdaptiveToolLoopOutcome(
                            profile_name=profile.profile_name,
                            mode_name=profile.mode_name,
                            termination_reason=dispatch_result,
                            state=loop_state,
                            allowed_tools=allowed_tools,
                        ),
                    )

        direct_tool_failure = (
            direct_tool_turn_active(loop_state)
            and action_result.status
            in {
                BRAIN_ACTION_STATUS_FAILED,
                BRAIN_ACTION_STATUS_TIMEOUT,
            }
            and not _is_structured_policy_recoverable(action_result)
        )
        if action_result.status in {
            BRAIN_ACTION_STATUS_FAILED,
            BRAIN_ACTION_STATUS_TIMEOUT,
        } and (
            not profile.allow_llm_recovery_after_tool_failure or direct_tool_failure
        ):
            if signature not in set(loop_state.seen_signatures):
                loop_state.seen_signatures.append(signature)
            loop_state.termination_reason = ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY
            emit_adaptive_status(
                loop_ctx,
                profile=profile,
                loop_state=loop_state,
                detail_text=f"{public_mode_tag} tool failure",
                mode_state="tool_failure",
                termination_reason=ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
                extra={"tool_name": tool_name},
            )
            return LoopExecutionResult(
                batch_had_progress=batch_had_progress,
                outcome=AdaptiveToolLoopOutcome(
                    profile_name=profile.profile_name,
                    mode_name=profile.mode_name,
                    termination_reason=ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
                    state=loop_state,
                    allowed_tools=allowed_tools,
                    action_result=action_result,
                    tool_name=tool_name,
                ),
            )

    return LoopExecutionResult(
        batch_had_progress=batch_had_progress,
        outcome=None,
    )
