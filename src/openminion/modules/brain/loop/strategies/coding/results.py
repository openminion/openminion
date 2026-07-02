from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_DISPOSITION_CONTINUE,
    BRAIN_DECISION_ROUTE_ACT,
    BRAIN_STATE_ACTIVE,
    BRAIN_STATE_CONTINUE,
    BRAIN_STATE_DONE,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_JOB_PENDING,
    BRAIN_STATE_WAITING_USER,
)
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_CIRCULAR_PATTERN,
    ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.schemas import ActionResult, new_uuid

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
        return _exit_final_text(
            runner,
            ctx,
            loop,
            outcome.final_text or "",
            allowed_tools,
            build_blocked_result=build_blocked_result,
        )
    if outcome.termination_reason == CODING_TERM_BUDGET_EXHAUSTED:
        return _exit_budget_exhausted(
            runner,
            ctx,
            loop,
            allowed_tools,
            build_blocked_result=build_blocked_result,
        )
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
    if outcome.termination_reason == ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS:
        message = (
            "[act:coding] repeated identical tool calls detected without reaching a "
            "final answer. Consider narrowing the scope or continuing in a follow-up turn."
        )
        return ExecutionResult(
            status=BRAIN_STATE_ACTIVE,
            working_state=ctx.state,
            message=message,
            action_result=build_error_result(message, "coding_duplicate_tool_calls"),
        )
    if outcome.termination_reason == ADAPTIVE_TERM_CIRCULAR_PATTERN:
        message = (
            "[act:coding] repeated the same tool pattern without making progress. "
            "Continue in a follow-up turn with a narrower implementation step."
        )
        return ExecutionResult(
            status=BRAIN_STATE_ACTIVE,
            working_state=ctx.state,
            message=message,
            action_result=build_error_result(message, "coding_circular_tool_pattern"),
        )
    if outcome.termination_reason == CODING_TERM_ITERATION_CAP:
        message = (
            "[act:coding] reached maximum iterations without a final answer. "
            "Consider narrowing the scope or continuing in a follow-up turn."
        )
        return ExecutionResult(
            status=BRAIN_STATE_ACTIVE,
            working_state=ctx.state,
            message=message,
            action_result=build_error_result(message, "coding_iteration_cap"),
        )
    message = outcome.error_message or "Coding loop stopped unexpectedly."
    return ExecutionResult(
        status=BRAIN_STATE_ERROR,
        working_state=ctx.state,
        message=message,
        action_result=build_error_result(message, "coding_loop_error"),
    )


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


def _exit_continue(
    runner: Any,
    ctx: ExecutionContext,
    *,
    allowed_tools: frozenset[str],
) -> ExecutionResult:
    loop = runner._loop_state
    summary = "[act:coding] continuing autonomous implementation."
    telemetry_payload = loop.telemetry_payload(allowed_tools)
    action_result = ActionResult(
        command_id=new_uuid(),
        status=BRAIN_ACTION_STATUS_SUCCESS,
        summary=summary,
        outputs=telemetry_payload,
    )
    ctx.emit_status(
        source_phase="coding.loop",
        detail_text=summary,
        mode=BRAIN_DECISION_ROUTE_ACT,
        mode_state="continue",
        payload={
            **telemetry_payload,
            "act.profile": BRAIN_ACT_PROFILE_CODING,
        },
    )
    runner._finalize_checkpoint(ctx, terminal=False, cursor=loop.iteration)
    return ExecutionResult.from_step_output(
        ctx.respond(
            message=summary,
            status=BRAIN_STATE_CONTINUE,
            action_result=action_result,
        )
    )


def _exit_autonomous_blocked(
    runner: Any,
    ctx: ExecutionContext,
    *,
    reason_code: str,
    failure_summary: str,
    allowed_tools: frozenset[str],
    build_blocked_result,
) -> ExecutionResult:
    loop = runner._loop_state
    reason_text = {
        "blocked_cap": "self-correction cap reached",
        "blocked_novel_failure": "same verification failure repeated",
        CODING_TERM_VERIFY_CAP_EXCEEDED: "verify gate cap reached",
    }.get(reason_code, "verification is blocked")
    issues = []
    if runner._coding_plan is not None:
        issues = list(runner._coding_plan.open_issues)
    summary = (
        f"[act:coding] blocked: {reason_text}. Latest failure: "
        f"{str(failure_summary or 'verification failed').strip()}. "
        f"Open issues: {', '.join(issues) if issues else 'none'}"
    )
    telemetry_payload = loop.telemetry_payload(allowed_tools)
    blocked_result = build_blocked_result(summary, reason_code)
    blocked_result.outputs = telemetry_payload
    ctx.emit_status(
        source_phase="coding.loop",
        detail_text=summary,
        mode=BRAIN_DECISION_ROUTE_ACT,
        mode_state="blocked",
        payload={
            **telemetry_payload,
            "act.profile": BRAIN_ACT_PROFILE_CODING,
        },
    )
    runner._finalize_checkpoint(ctx, terminal=False, cursor=loop.iteration)
    return ExecutionResult.from_step_output(
        ctx.respond(
            message=summary,
            status=BRAIN_STATE_WAITING_USER,
            action_result=blocked_result,
        )
    )


def _exit_final_text(
    runner: Any,
    ctx: ExecutionContext,
    loop: Any,
    output_text: str,
    allowed_tools: frozenset[str],
    *,
    build_blocked_result,
) -> ExecutionResult:
    del build_blocked_result
    telemetry_payload = loop.telemetry_payload(allowed_tools)
    final_action = ActionResult(
        command_id=new_uuid(),
        status=BRAIN_ACTION_STATUS_SUCCESS,
        summary=output_text or "[act:coding] done",
        outputs=telemetry_payload,
    )

    ctx.emit_status(
        source_phase="coding.loop",
        detail_text="[act:coding] done",
        mode=BRAIN_DECISION_ROUTE_ACT,
        mode_state="done",
        payload={
            **telemetry_payload,
            "act.profile": BRAIN_ACT_PROFILE_CODING,
        },
    )

    try:
        judgment = ctx.evaluate_turn_closure(
            action_result=final_action,
            completion_reason="coding_final_text",
        )
        disposition = ctx.apply_closure_judgment(judgment=judgment)
    except Exception:  # noqa: BLE001
        judgment = None
        disposition = None

    if disposition == BRAIN_DISPOSITION_CONTINUE:
        runner._append_phase_instruction()
        runner._sync_coding_module_state(ctx)
        return _exit_continue(runner, ctx, allowed_tools=allowed_tools)

    runner._clear_coding_module_state(ctx)
    step_output = ctx.respond(
        message=output_text or "",
        status=BRAIN_STATE_DONE,
        action_result=final_action,
    )
    runner._finalize_checkpoint(ctx, terminal=True, cursor=loop.iteration)
    return ExecutionResult.from_step_output(step_output, judgment=judgment)


def _exit_budget_exhausted(
    runner: Any,
    ctx: ExecutionContext,
    loop: Any,
    allowed_tools: frozenset[str],
    *,
    build_blocked_result,
) -> ExecutionResult:
    del allowed_tools
    msg = (
        "[act:coding] budget exhausted before a final answer. "
        "Consider narrowing the scope or continuing in a follow-up turn."
    )
    runner._finalize_checkpoint(ctx, terminal=False, cursor=loop.iteration)
    return ExecutionResult(
        status=BRAIN_STATE_ACTIVE,
        working_state=ctx.state,
        message=msg,
        action_result=build_blocked_result(msg, "coding_budget_exhausted"),
    )
