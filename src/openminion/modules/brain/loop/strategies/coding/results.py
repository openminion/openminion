from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_DECISION_ROUTE_ACT,
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
from openminion.modules.brain.loop.tools.postprocess.evidence_closeout import (
    missing_requested_file_artifact_labels,
)
from openminion.modules.brain.loop.tools.postprocess.rules import (
    _looks_like_unexecutable_tool_payload_text,
)
from openminion.modules.llm.schemas import Message

from .artifact_gates import (
    stage_required_write_direct_tool,
    suggest_missing_artifact_paths,
    user_explicitly_requested_file_artifact,
    write_missing_artifact_scaffolds,
)
from .closeout_salvage import salvage_final_answer_after_disallowed_writer
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
        salvaged_final_text = salvage_final_answer_after_disallowed_writer(
            runner, outcome=outcome
        )
        if salvaged_final_text is not None:
            return _exit_final_text(
                runner,
                ctx,
                loop,
                salvaged_final_text,
                allowed_tools,
                build_blocked_result=build_blocked_result,
            )
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
        final_text=getattr(outcome, "final_text", "") or "",
        outcome_state=getattr(outcome, "state", None),
    )


def _plan_or_user_requires_file_change(runner: Any, loop_state: Any) -> bool:
    plan = getattr(runner, "_coding_plan", None)
    if plan is not None and bool(getattr(plan, "requires_file_change", False)):
        return True
    scratchpad = getattr(loop_state, "scratchpad", {}) or {}
    if bool(scratchpad.get("coding.requires_file_change")):
        return True
    return user_explicitly_requested_file_artifact(loop_state)


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
    requires_file_change = _plan_or_user_requires_file_change(
        runner, loop
    ) or _plan_or_user_requires_file_change(runner, outcome_state)
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
    final_text: str = "",
    outcome_state: Any | None = None,
) -> ExecutionResult | None:
    requires_file_change = (
        runner._coding_plan_requires_file_change()
        or user_explicitly_requested_file_artifact(runner._loop_state)
        or user_explicitly_requested_file_artifact(outcome_state)
    )
    if not requires_file_change:
        return None
    missing_artifacts = missing_requested_file_artifact_labels(runner._loop_state)
    if runner._has_successful_mutating_file_result() and not missing_artifacts:
        return None

    rendered_missing, rendered_paths, failure_summary = _missing_write_details(
        runner,
        missing_artifacts=missing_artifacts,
    )
    _prepare_missing_write_retry(
        runner,
        ctx,
        loop=loop,
        allowed_tools=allowed_tools,
        missing_artifacts=missing_artifacts,
        failure_summary=failure_summary,
    )
    if missing_artifacts:
        attempt, correction_cap = _record_missing_artifact_attempt(
            ctx,
            loop=loop,
            missing_artifacts=missing_artifacts,
            rendered_missing=rendered_missing,
            failure_summary=failure_summary,
        )
    else:
        attempt = runner._record_verify_gate_block(
            ctx,
            failure_summary=failure_summary,
            reason="missing_implementation_write",
            required_tool="file.write or code.patch",
        )
        correction_cap = max(1, int(getattr(runner, "_max_self_corrections", 0) or 0))
    if attempt > correction_cap:
        scaffolded_result = _continue_after_scaffolded_missing_artifacts(
            runner,
            ctx,
            loop=loop,
            allowed_tools=allowed_tools,
            missing_artifacts=missing_artifacts,
        )
        if scaffolded_result is not None:
            return scaffolded_result
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

    if runner._coding_plan is not None:
        runner._sync_plan_telemetry()
    retry_message = _missing_write_retry_message(
        final_text=final_text,
        missing_artifacts=missing_artifacts,
        rendered_missing=rendered_missing,
        rendered_paths=rendered_paths,
    )
    loop.messages.append(
        Message(
            role="system" if missing_artifacts else "user",
            content=retry_message,
        )
    )
    runner._emit_phase_status(ctx)
    runner._sync_coding_module_state(ctx)
    return _exit_continue(runner, ctx, allowed_tools=allowed_tools)


def _continue_after_scaffolded_missing_artifacts(
    runner: Any,
    ctx: ExecutionContext,
    *,
    loop: Any,
    allowed_tools: frozenset[str],
    missing_artifacts: tuple[str, ...],
) -> ExecutionResult | None:
    if not missing_artifacts or not write_missing_artifact_scaffolds(
        runner,
        ctx,
        missing_artifacts=missing_artifacts,
    ):
        return None
    loop.scratchpad["coding.verify_gate_reason"] = (
        "missing_requested_file_artifacts_scaffolded"
    )
    loop.scratchpad.pop("coding.required_write_direct_tool", None)
    loop.direct_tool_turn = None
    loop.direct_tool_requested_batch_satisfied = False
    loop.messages.append(
        Message(
            role="system",
            content=(
                "The missing ancillary coding artifacts have been created through "
                "tool execution. Continue by running the requested validation from "
                "disk before returning the final answer."
            ),
        )
    )
    if runner._coding_plan is not None:
        runner._sync_plan_telemetry()
    runner._emit_phase_status(ctx)
    runner._sync_coding_module_state(ctx)
    return _exit_continue(runner, ctx, allowed_tools=allowed_tools)


def _missing_write_details(
    runner: Any,
    *,
    missing_artifacts: tuple[str, ...],
) -> tuple[str, str, str]:
    if not missing_artifacts:
        return (
            "",
            "",
            "Coding plan requires a mutating implementation step before final "
            "answer, but no successful file.write or code.patch result was recorded.",
        )
    rendered_missing = ", ".join(missing_artifacts)
    suggested_paths = suggest_missing_artifact_paths(
        loop_state=runner._loop_state,
        missing_artifacts=missing_artifacts,
    )
    rendered_paths = ", ".join(f"`{path}`" for path in suggested_paths)
    return (
        rendered_missing,
        rendered_paths,
        "Coding request still requires these file artifacts before final "
        f"answer: {rendered_missing}.",
    )


def _prepare_missing_write_retry(
    runner: Any,
    ctx: ExecutionContext,
    *,
    loop: Any,
    allowed_tools: frozenset[str],
    missing_artifacts: tuple[str, ...],
    failure_summary: str,
) -> None:
    if runner._coding_plan is not None:
        runner._coding_plan.current_phase = "implement"
        runner._coding_plan.record_open_issue(failure_summary)
    if missing_artifacts:
        loop.direct_tool_turn = None
        loop.direct_tool_requested_batch_satisfied = False
        loop.scratchpad.pop("direct_tool_completed_tool_names", None)
    stage_required_write_direct_tool(loop, allowed_tools=allowed_tools)
    budgets = getattr(ctx.state, "budgets_remaining", None)
    if budgets is not None:
        budgets.tool_calls = max(int(getattr(budgets, "tool_calls", 0) or 0), 1)


def _record_missing_artifact_attempt(
    ctx: ExecutionContext,
    *,
    loop: Any,
    missing_artifacts: tuple[str, ...],
    rendered_missing: str,
    failure_summary: str,
) -> tuple[int, int]:
    counts = dict(
        loop.scratchpad.get("coding.missing_requested_artifact_retry_counts", {}) or {}
    )
    count_key = "|".join(missing_artifacts)
    attempt = int(counts.get(count_key, 0) or 0) + 1
    counts[count_key] = attempt
    loop.scratchpad["coding.missing_requested_artifact_retry_counts"] = counts
    loop.scratchpad["coding.verify_gate_reason"] = "missing_requested_file_artifacts"
    loop.scratchpad["coding.verify_gate_required_tool"] = "file.write or code.patch"
    loop.scratchpad["coding.last_failure_summary"] = failure_summary
    ctx.emit_status(
        source_phase="coding.verify_gate",
        detail_text=(
            "[act:coding] missing requested file artifacts: " f"{rendered_missing}"
        ),
        mode=BRAIN_DECISION_ROUTE_ACT,
        mode_state="missing_requested_file_artifacts",
        payload={
            "act.profile": BRAIN_ACT_PROFILE_CODING,
            "coding.verify_gate_reason": "missing_requested_file_artifacts",
            "coding.missing_requested_artifacts": list(missing_artifacts),
        },
    )
    return attempt, max(4, len(missing_artifacts) + 2)


def _missing_write_retry_message(
    *,
    final_text: str,
    missing_artifacts: tuple[str, ...],
    rendered_missing: str,
    rendered_paths: str,
) -> str:
    if missing_artifacts:
        path_instruction = (
            f" The next missing path candidates are: {rendered_paths}."
            if rendered_paths
            else ""
        )
        return (
            "Stay in implement. The current tool evidence is missing requested "
            f"file artifacts: {rendered_missing}. Use `file.write` or "
            "`code.patch` to create the missing files now."
            f"{path_instruction} Do not describe those files in prose instead of "
            "calling the writer. After the missing files exist, run the requested "
            "validation before returning a final answer."
        )
    if _looks_like_unexecutable_tool_payload_text(final_text):
        return (
            "Stay in implement. Do not print JSON tool payloads, path/content "
            "objects, or file contents as prose. Call `file.write` or "
            "`code.patch` as an actual tool with the target path and content, "
            "then verify from disk before returning a final answer."
        )
    return (
        "Stay in implement and use a mutating implementation tool "
        "(`file.write` or `code.patch`) before returning a final answer."
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
