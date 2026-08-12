from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_STATE_ERROR,
    BRAIN_STATE_JOB_PENDING,
    BRAIN_STATE_WAITING_USER,
)
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_CIRCULAR_PATTERN,
    ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED,
    ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.llm.schemas import Message

from .artifact_gates import stage_required_write_direct_tool
from .contracts import (
    CODING_TERM_BUDGET_EXHAUSTED,
    CODING_TERM_CONFIDENT_COMPLETE,
    CODING_TERM_DISALLOWED_TOOL,
    CODING_TERM_FINAL_TEXT,
    CODING_TERM_ITERATION_CAP,
    CODING_TERM_JOB_PENDING,
    CODING_TERM_LLM_ERROR,
    CODING_TERM_NEEDS_USER,
    CODING_TERM_TOOL_FAILURE,
    CODING_TERM_VERIFY_CAP_EXCEEDED,
)
from .terminal_results import (
    _exit_autonomous_blocked,
    _exit_blocked_with_closure,
    _exit_budget_exhausted,
    _exit_continue,
    _exit_final_text,
)


def _direct_termination_result(
    runner: Any,
    ctx: ExecutionContext,
    *,
    outcome: Any,
    allowed_tools: frozenset[str],
    build_error_result,
    build_blocked_result,
) -> ExecutionResult | None:
    loop = runner._loop_state
    if outcome.termination_reason == CODING_TERM_NEEDS_USER:
        runner._finalize_checkpoint(ctx, terminal=False, cursor=loop.iteration)
        message = (
            str(getattr(ctx.state, "post_action_user_message", "") or "").strip()
            or getattr(outcome.action_result, "summary", "")
            or "Approval required."
        )
        return ExecutionResult.from_step_output(
            ctx.respond(
                message=message,
                status=BRAIN_STATE_WAITING_USER,
                action_result=outcome.action_result,
            )
        )
    if outcome.termination_reason == CODING_TERM_JOB_PENDING:
        runner._finalize_checkpoint(ctx, terminal=False, cursor=loop.iteration)
        return ExecutionResult(
            status=BRAIN_STATE_JOB_PENDING,
            working_state=ctx.state,
            message="[act:coding] async job pending; resume on next turn.",
            action_result=outcome.action_result,
        )
    if outcome.termination_reason == CODING_TERM_DISALLOWED_TOOL:
        if _maybe_continue_after_verify_disallowed_tool(
            runner, ctx, loop=loop, outcome=outcome
        ):
            runner._sync_coding_module_state(ctx)
            return _exit_continue(runner, ctx, allowed_tools=allowed_tools)
        message = outcome.error_message or "Coding mode requested a disallowed tool."
        return ExecutionResult(
            status=BRAIN_STATE_ERROR,
            working_state=ctx.state,
            message=message,
            action_result=build_blocked_result(message, "coding_disallowed_tool"),
        )
    if outcome.termination_reason == CODING_TERM_LLM_ERROR:
        message = outcome.error_message or "Coding LLM call failed."
        return ExecutionResult(
            status=BRAIN_STATE_ERROR,
            working_state=ctx.state,
            message=f"[act:coding] LLM error: {message}",
            action_result=build_error_result(message, "coding_llm_error"),
        )
    return None


def _result_from_outcome(
    runner: Any,
    ctx: ExecutionContext,
    *,
    outcome: Any,
    allowed_tools: frozenset[str],
    build_error_result,
    build_blocked_result,
) -> ExecutionResult:
    loop = runner._loop_state
    if outcome.termination_reason in {
        CODING_TERM_FINAL_TEXT,
        CODING_TERM_CONFIDENT_COMPLETE,
    }:
        verify_failure = runner._latest_tool_failure_summary()
        if (
            runner._coding_plan is not None
            and runner._coding_plan.current_phase == "verify"
            and verify_failure
        ):
            synthetic_outcome = outcome.__class__(
                profile_name=outcome.profile_name,
                mode_name=outcome.mode_name,
                state=outcome.state,
                termination_reason=CODING_TERM_TOOL_FAILURE,
                allowed_tools=outcome.allowed_tools,
                final_text=outcome.final_text,
                action_result=build_error_result(
                    verify_failure,
                    "coding_verify_failure",
                ),
                error_message=verify_failure,
            )
            return _result_from_outcome(
                runner,
                ctx,
                outcome=synthetic_outcome,
                allowed_tools=allowed_tools,
                build_error_result=build_error_result,
                build_blocked_result=build_blocked_result,
            )
        missing_write_result = _missing_write_gate_result(
            runner, ctx, loop, outcome, allowed_tools, build_blocked_result
        )
        if missing_write_result is not None:
            return missing_write_result
        return _exit_final_text(
            runner,
            ctx,
            loop,
            outcome.final_text or "",
            allowed_tools,
            build_blocked_result=build_blocked_result,
        )
    if outcome.termination_reason == CODING_TERM_BUDGET_EXHAUSTED:
        missing_write_result = _missing_write_gate_result(
            runner, ctx, loop, outcome, allowed_tools, build_blocked_result
        )
        if missing_write_result is not None:
            return missing_write_result
        return _exit_budget_exhausted(
            runner,
            ctx,
            loop,
            allowed_tools,
            build_blocked_result=build_blocked_result,
        )
    if outcome.termination_reason == ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED:
        missing_write_result = _missing_write_gate_result(
            runner, ctx, loop, outcome, allowed_tools, build_blocked_result
        )
        if missing_write_result is not None:
            return missing_write_result
    direct_result = _direct_termination_result(
        runner,
        ctx,
        outcome=outcome,
        allowed_tools=allowed_tools,
        build_error_result=build_error_result,
        build_blocked_result=build_blocked_result,
    )
    if direct_result is not None:
        return direct_result
    if outcome.termination_reason in {
        ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
        CODING_TERM_TOOL_FAILURE,
    }:
        if _maybe_continue_after_tool_failure(runner, ctx, loop=loop, outcome=outcome):
            runner._sync_coding_module_state(ctx)
            return _exit_continue(runner, ctx, allowed_tools=allowed_tools)
        if loop.termination_reason in {"blocked_cap", "blocked_novel_failure"}:
            return _exit_autonomous_blocked(
                runner,
                ctx,
                reason_code=loop.termination_reason,
                failure_summary=(
                    getattr(outcome.action_result, "summary", "")
                    or outcome.error_message
                    or "Verification failed."
                ),
                allowed_tools=allowed_tools,
                build_blocked_result=build_blocked_result,
            )
        message = (
            getattr(outcome.action_result, "summary", "")
            or outcome.error_message
            or "Tool execution failed."
        )
        return ExecutionResult(
            status=BRAIN_STATE_ERROR,
            working_state=ctx.state,
            message=message,
            action_result=outcome.action_result
            if outcome.action_result is not None
            else build_error_result(message, "coding_tool_failure"),
        )
    if outcome.termination_reason in {
        ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
        ADAPTIVE_TERM_CIRCULAR_PATTERN,
        CODING_TERM_ITERATION_CAP,
    }:
        readonly_retry = _maybe_retry_required_write_after_readonly_dead_end(
            runner,
            ctx,
            loop=loop,
            allowed_tools=allowed_tools,
            outcome_state=getattr(outcome, "state", None),
        )
        if readonly_retry is not None:
            return readonly_retry
        missing_write_result = _missing_write_gate_result(
            runner, ctx, loop, outcome, allowed_tools, build_blocked_result
        )
        if missing_write_result is not None:
            return missing_write_result
    if outcome.termination_reason == ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS:
        return _exit_loop_pattern_blocked(
            runner,
            ctx,
            loop,
            allowed_tools,
            build_blocked_result=build_blocked_result,
            code="coding_duplicate_tool_calls",
            message=(
                "[act:coding] repeated identical tool calls detected without reaching a "
                "final answer. Consider narrowing the scope or continuing in a follow-up turn."
            ),
        )
    if outcome.termination_reason == ADAPTIVE_TERM_CIRCULAR_PATTERN:
        return _exit_loop_pattern_blocked(
            runner,
            ctx,
            loop,
            allowed_tools,
            build_blocked_result=build_blocked_result,
            code="coding_circular_tool_pattern",
            message=(
                "[act:coding] repeated the same tool pattern without making progress. "
                "Continue in a follow-up turn with a narrower implementation step."
            ),
        )
    if outcome.termination_reason == CODING_TERM_ITERATION_CAP:
        return _exit_loop_pattern_blocked(
            runner,
            ctx,
            loop,
            allowed_tools,
            build_blocked_result=build_blocked_result,
            code="coding_iteration_cap",
            message=(
                "[act:coding] reached maximum iterations without a final answer. "
                "Consider narrowing the scope or continuing in a follow-up turn."
            ),
        )
    message = outcome.error_message or "Coding loop stopped unexpectedly."
    return ExecutionResult(
        status=BRAIN_STATE_ERROR,
        working_state=ctx.state,
        message=message,
        action_result=build_error_result(message, "coding_loop_error"),
    )


def _exit_loop_pattern_blocked(
    runner: Any,
    ctx: ExecutionContext,
    loop: Any,
    allowed_tools: frozenset[str],
    *,
    build_blocked_result,
    code: str,
    message: str,
) -> ExecutionResult:
    return _exit_blocked_with_closure(
        runner,
        ctx,
        loop=loop,
        message=message,
        code=code,
        telemetry_payload=loop.telemetry_payload(allowed_tools),
        allowed_tools=allowed_tools,
        build_blocked_result=build_blocked_result,
    )


def _missing_write_gate_result(
    runner: Any,
    ctx: ExecutionContext,
    loop: Any,
    outcome: Any,
    allowed_tools: frozenset[str],
    build_blocked_result,
) -> ExecutionResult | None:
    return _maybe_gate_missing_required_write(
        runner,
        ctx,
        loop=loop,
        allowed_tools=allowed_tools,
        build_blocked_result=build_blocked_result,
        outcome_state=getattr(outcome, "state", None),
    )


def _plan_or_state_requires_file_change(runner: Any, loop_state: Any) -> bool:
    plan = getattr(runner, "_coding_plan", None)
    if plan is not None and bool(getattr(plan, "requires_file_change", False)):
        return True
    scratchpad = getattr(loop_state, "scratchpad", {}) or {}
    return bool(scratchpad.get("coding.requires_file_change"))


def _maybe_retry_required_write_after_readonly_dead_end(
    runner: Any,
    ctx: ExecutionContext,
    *,
    loop: Any,
    allowed_tools: frozenset[str],
    outcome_state: Any | None = None,
) -> ExecutionResult | None:
    if runner._has_successful_mutating_file_result():
        return None
    if bool(loop.scratchpad.get("coding.readonly_dead_end_write_retry_used")):
        return None
    requires_file_change = _plan_or_state_requires_file_change(
        runner, loop
    ) or _plan_or_state_requires_file_change(runner, outcome_state)
    if not requires_file_change:
        return None

    failure_summary = (
        "Coding task repeated read-only tool calls before creating the requested "
        "file artifacts. Retry with the mutating writer directly."
    )
    loop.scratchpad["coding.readonly_dead_end_write_retry_used"] = True
    loop.scratchpad["coding.verify_gate_reason"] = "readonly_dead_end_missing_write"
    if runner._coding_plan is not None:
        runner._coding_plan.current_phase = "implement"
        runner._coding_plan.record_open_issue(failure_summary)
        runner._sync_plan_telemetry()
    stage_required_write_direct_tool(loop, allowed_tools=allowed_tools)
    loop.messages.append(
        Message(
            role="user",
            content=(
                "The task is still in implement. You repeated read-only inspection "
                "without creating the requested files. Call `file.write` now with "
                "the target path and content for the first project file. Do not "
                "call list/read/repo-map tools before that writer call."
            ),
        )
    )
    runner._emit_phase_status(ctx)
    runner._sync_coding_module_state(ctx)
    return _exit_continue(runner, ctx, allowed_tools=allowed_tools)


def _maybe_gate_missing_required_write(
    runner: Any,
    ctx: ExecutionContext,
    *,
    loop: Any,
    allowed_tools: frozenset[str],
    build_blocked_result,
    outcome_state: Any | None = None,
) -> ExecutionResult | None:
    requires_file_change = (
        runner._coding_plan_requires_file_change()
        or _plan_or_state_requires_file_change(runner, runner._loop_state)
        or _plan_or_state_requires_file_change(runner, outcome_state)
    )
    if not requires_file_change or runner._has_successful_mutating_file_result():
        return None
    failure_summary = (
        "Coding plan requires a mutating implementation step before final "
        "answer, but no successful file.write or code.patch result was recorded."
    )
    if runner._coding_plan is not None:
        runner._coding_plan.current_phase = "implement"
        runner._coding_plan.record_open_issue(failure_summary)
    stage_required_write_direct_tool(loop, allowed_tools=allowed_tools)
    budgets = getattr(ctx.state, "budgets_remaining", None)
    if budgets is not None:
        budgets.tool_calls = max(budgets.tool_calls, 1)
    attempt = runner._record_verify_gate_block(
        ctx,
        failure_summary=failure_summary,
        reason="missing_implementation_write",
        required_tool="file.write or code.patch",
    )
    if attempt > max(1, runner._max_self_corrections):
        loop.termination_reason = CODING_TERM_VERIFY_CAP_EXCEEDED
        runner._sync_plan_telemetry()
        runner._emit_phase_status(ctx)
        runner._sync_coding_module_state(ctx)
        return _exit_autonomous_blocked(
            runner,
            ctx,
            reason_code=CODING_TERM_VERIFY_CAP_EXCEEDED,
            failure_summary=failure_summary,
            allowed_tools=allowed_tools,
            build_blocked_result=build_blocked_result,
        )

    loop.messages.append(
        Message(
            role="user",
            content=(
                "Stay in implement and use a mutating implementation tool "
                "(`file.write` or `code.patch`) before returning a final answer."
            ),
        )
    )
    runner._sync_plan_telemetry()
    runner._emit_phase_status(ctx)
    runner._sync_coding_module_state(ctx)
    return _exit_continue(runner, ctx, allowed_tools=allowed_tools)


def _maybe_continue_after_tool_failure(
    runner: Any,
    ctx: ExecutionContext,
    *,
    loop: Any,
    outcome: Any,
) -> bool:
    if runner._coding_plan is None or runner._coding_plan.current_phase != "verify":
        return False
    failure_summary = (
        getattr(outcome.action_result, "summary", "")
        or outcome.error_message
        or "Verification failed."
    )
    previous_failure = str(
        loop.scratchpad.get("coding.last_failure_summary", "") or ""
    ).strip()
    attempted = int(loop.scratchpad.get("coding.self_corrections", 0) or 0)
    if previous_failure and previous_failure == failure_summary:
        loop.termination_reason = "blocked_novel_failure"
        return False
    if attempted >= runner._max_self_corrections:
        loop.termination_reason = "blocked_cap"
        return False
    runner._coding_plan.current_phase = "implement"
    runner._coding_plan.record_open_issue(failure_summary)
    runner._record_autonomous_correction(
        ctx,
        failure_summary=str(failure_summary or "").strip(),
    )
    runner._append_phase_instruction()
    runner._emit_phase_status(ctx)
    return True


def _maybe_continue_after_verify_disallowed_tool(
    runner: Any,
    ctx: ExecutionContext,
    *,
    loop: Any,
    outcome: Any,
) -> bool:
    if runner._coding_plan is None or runner._coding_plan.current_phase != "verify":
        return False
    tool_name = str(getattr(outcome, "tool_name", "") or "").strip()
    if tool_name not in {"file.write", "code.patch"}:
        return False
    if bool(loop.scratchpad.get("coding.final_answer_reserve_used")):
        return False
    if runner._has_verifier_candidate():
        return runner._queue_final_answer_reserve(
            ctx,
            restore_answer_only_state=False,
        )
    if not runner._has_successful_mutating_file_result():
        return False
    return runner._queue_verification_reserve(
        ctx,
        restore_answer_only_state=False,
        ensure_tool_budget=False,
    )
