from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_DISPOSITION_CONTINUE,
    BRAIN_DECISION_ROUTE_ACT,
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
    DirectToolTurnContext,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.closure import final_close_message
from openminion.modules.brain.schemas import ActionResult, new_uuid
from openminion.modules.brain.loop.tools.postprocess.evidence_closeout import (
    mutating_file_evidence_fallback_text,
)
from openminion.modules.brain.loop.tools.postprocess.rules import (
    _looks_like_unexecutable_tool_payload_text,
)
from openminion.modules.llm.schemas import Message

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
        salvaged_final_text = _salvage_final_answer_after_disallowed_writer(
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
        missing_write_result = _maybe_gate_missing_required_write(
            runner,
            ctx,
            loop=loop,
            allowed_tools=allowed_tools,
            build_blocked_result=build_blocked_result,
            final_text=outcome.final_text or "",
            outcome_state=getattr(outcome, "state", None),
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
        missing_write_result = _maybe_gate_missing_required_write(
            runner,
            ctx,
            loop=loop,
            allowed_tools=allowed_tools,
            build_blocked_result=build_blocked_result,
            final_text=getattr(outcome, "final_text", "") or "",
            outcome_state=getattr(outcome, "state", None),
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
        missing_write_result = _maybe_gate_missing_required_write(
            runner,
            ctx,
            loop=loop,
            allowed_tools=allowed_tools,
            build_blocked_result=build_blocked_result,
            final_text=getattr(outcome, "final_text", "") or "",
            outcome_state=getattr(outcome, "state", None),
        )
        if missing_write_result is not None:
            return missing_write_result
    if outcome.termination_reason == ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS:
        message = (
            "[act:coding] repeated identical tool calls detected without reaching a "
            "final answer. Consider narrowing the scope or continuing in a follow-up turn."
        )
        return _exit_blocked_with_closure(
            runner,
            ctx,
            loop=loop,
            message=message,
            code="coding_duplicate_tool_calls",
            telemetry_payload=loop.telemetry_payload(allowed_tools),
            allowed_tools=allowed_tools,
            build_blocked_result=build_blocked_result,
        )
    if outcome.termination_reason == ADAPTIVE_TERM_CIRCULAR_PATTERN:
        message = (
            "[act:coding] repeated the same tool pattern without making progress. "
            "Continue in a follow-up turn with a narrower implementation step."
        )
        return _exit_blocked_with_closure(
            runner,
            ctx,
            loop=loop,
            message=message,
            code="coding_circular_tool_pattern",
            telemetry_payload=loop.telemetry_payload(allowed_tools),
            allowed_tools=allowed_tools,
            build_blocked_result=build_blocked_result,
        )
    if outcome.termination_reason == CODING_TERM_ITERATION_CAP:
        message = (
            "[act:coding] reached maximum iterations without a final answer. "
            "Consider narrowing the scope or continuing in a follow-up turn."
        )
        return _exit_blocked_with_closure(
            runner,
            ctx,
            loop=loop,
            message=message,
            code="coding_iteration_cap",
            telemetry_payload=loop.telemetry_payload(allowed_tools),
            allowed_tools=allowed_tools,
            build_blocked_result=build_blocked_result,
        )
    message = outcome.error_message or "Coding loop stopped unexpectedly."
    return ExecutionResult(
        status=BRAIN_STATE_ERROR,
        working_state=ctx.state,
        message=message,
        action_result=build_error_result(message, "coding_loop_error"),
    )


def _user_explicitly_requested_file_artifact(loop_state: Any) -> bool:
    user_text = "\n".join(
        str(getattr(message, "content", "") or "")
        for message in list(getattr(loop_state, "messages", []) or [])
        if str(getattr(message, "role", "") or "").strip().lower() == "user"
    ).lower()
    if not user_text:
        return False
    explicit_tooling = any(
        pattern in user_text
        for pattern in (
            "do not only show code",
            "file.write/file.read",
            "file.write for files",
            "implement it with file.write",
            "use file.write",
            "using file.write",
            "with file.write",
        )
    )
    if not explicit_tooling:
        return False
    return any(
        token in user_text
        for token in ("build", "create", "implement", "write", "project", "module")
    )


def _stage_required_write_direct_tool(
    loop_state: Any,
    *,
    allowed_tools: frozenset[str],
) -> None:
    if getattr(loop_state, "direct_tool_turn", None) is not None:
        return
    requested_name = "file.write" if "file.write" in allowed_tools else "code.patch"
    loop_state.direct_tool_turn = DirectToolTurnContext(
        requested_tool_names=(requested_name,),
        requested_batch_signature="",
        match_by_name_only=True,
    )
    loop_state.scratchpad["coding.required_write_direct_tool"] = requested_name


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
        or _user_explicitly_requested_file_artifact(runner._loop_state)
        or _user_explicitly_requested_file_artifact(outcome_state)
    )
    if not requires_file_change:
        return None
    if runner._has_successful_mutating_file_result():
        return None

    failure_summary = (
        "Coding plan requires a mutating implementation step before final "
        "answer, but no successful file.write or code.patch result was recorded."
    )
    if runner._coding_plan is not None:
        runner._coding_plan.current_phase = "implement"
        runner._coding_plan.record_open_issue(failure_summary)
    _stage_required_write_direct_tool(loop, allowed_tools=allowed_tools)
    attempt = runner._record_verify_gate_block(
        ctx,
        failure_summary=failure_summary,
        reason="missing_implementation_write",
        required_tool="file.write or code.patch",
    )
    if attempt >= runner._max_self_corrections:
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
    if _looks_like_unexecutable_tool_payload_text(final_text):
        retry_message = (
            "Stay in implement. Do not print JSON tool payloads, path/content "
            "objects, or file contents as prose. Call `file.write` or "
            "`code.patch` as an actual tool with the target path and content, "
            "then verify from disk before returning a final answer."
        )
    else:
        retry_message = (
            "Stay in implement and use a mutating implementation tool "
            "(`file.write` or `code.patch`) before returning a final answer."
        )
    loop.messages.append(
        Message(
            role="user",
            content=retry_message,
        )
    )
    runner._emit_phase_status(ctx)
    runner._sync_coding_module_state(ctx)
    return _exit_continue(runner, ctx, allowed_tools=allowed_tools)


def _salvage_final_answer_after_disallowed_writer(
    runner: Any,
    *,
    outcome: Any,
) -> str | None:
    loop = runner._loop_state
    if not bool(loop.scratchpad.get("coding.final_answer_reserve_used")):
        return None
    tool_name = str(getattr(outcome, "tool_name", "") or "").strip()
    if tool_name not in {"file.write", "code.patch"}:
        return None
    tool_results = [
        item
        for item in list(loop.scratchpad.get("adaptive.tool_results", []) or [])
        if isinstance(item, dict) and bool(item.get("ok"))
    ]
    return _salvage_reserved_closeout_from_existing_evidence(
        runner,
        tool_results=tool_results,
        interruption_detail=(
            "The model kept asking for extra write calls during the reserved "
            "answer-only closeout, so this summary is derived from the existing "
            "coding evidence."
        ),
    )


def _salvage_reserved_closeout_from_existing_evidence(
    runner: Any,
    *,
    tool_results: list[dict[str, Any]] | None = None,
    interruption_detail: str,
) -> str | None:
    loop = runner._loop_state
    if not bool(loop.scratchpad.get("coding.final_answer_reserve_used")):
        return None
    if tool_results is None:
        tool_results = [
            item
            for item in list(loop.scratchpad.get("adaptive.tool_results", []) or [])
            if isinstance(item, dict) and bool(item.get("ok"))
        ]
    if not tool_results:
        return None

    changed_paths: list[str] = []
    for item in tool_results:
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        path = str(data.get("path", "") or "").strip()
        if path and path not in changed_paths:
            changed_paths.append(path)

    verifier_status = (
        "preserved from an earlier read-only verification step"
        if runner._has_verifier_candidate()
        else "not recorded after the final successful write"
    )
    requested_markers = runner._requested_final_markers()
    marker_lines: list[str] = []
    for marker in requested_markers:
        normalized = str(marker or "").strip().lower().rstrip(":")
        if not normalized:
            continue
        if normalized == "result":
            marker_lines.append(
                "result: reserved final closeout was interrupted after successful "
                f"tool writes; returning deterministic run evidence instead. "
                f"{interruption_detail}"
            )
            continue
        if normalized == "files changed":
            rendered_paths = (
                ", ".join(changed_paths[:8]) if changed_paths else "none recorded"
            )
            marker_lines.append(f"files changed: {rendered_paths}")
            continue
        if normalized in {"validation", "validation result"}:
            marker_lines.append(f"{normalized}: {verifier_status}")
            continue
        if normalized == "remaining follow-ups":
            marker_lines.append(
                "remaining follow-ups: no deterministic follow-up list was captured "
                "before the reserved closeout was interrupted."
            )
            continue
        marker_lines.append(
            f"{normalized}: not captured before closeout interruption; preserved "
            "written-file evidence is reported instead."
        )

    if not marker_lines:
        rendered_paths = (
            ", ".join(changed_paths[:8]) if changed_paths else "none recorded"
        )
        marker_lines = [
            f"files changed: {rendered_paths}",
            (
                "result: reserved final closeout was interrupted after successful "
                f"tool writes; returning deterministic run evidence instead. "
                f"{interruption_detail}"
            ),
            f"validation: {verifier_status}",
        ]
    return "\n".join(marker_lines)


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
    salvaged_final_text = _salvage_reserved_closeout_from_existing_evidence(
        runner,
        interruption_detail=(
            "The reserved answer-only closeout was interrupted by a repeated "
            "verification failure, so this summary is derived from the existing "
            "coding evidence."
        ),
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


def _mutating_file_evidence_final_text(runner: Any) -> str:
    if not runner._has_successful_mutating_file_result():
        return ""
    return mutating_file_evidence_fallback_text(runner._loop_state)


def _exit_budget_exhausted(
    runner: Any,
    ctx: ExecutionContext,
    loop: Any,
    allowed_tools: frozenset[str],
    *,
    build_blocked_result,
) -> ExecutionResult:
    salvaged_final_text = _salvage_reserved_closeout_from_existing_evidence(
        runner,
        interruption_detail=(
            "The reserved answer-only closeout was interrupted by budget "
            "exhaustion, so this summary is derived from the existing coding "
            "evidence."
        ),
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
    fallback_text = _mutating_file_evidence_final_text(runner)
    if fallback_text:
        return _exit_final_text(
            runner,
            ctx,
            loop,
            fallback_text,
            allowed_tools,
            build_blocked_result=build_blocked_result,
        )
    telemetry_payload = loop.telemetry_payload(allowed_tools)
    msg = (
        "[act:coding] budget exhausted before a final answer. "
        "Consider narrowing the scope or continuing in a follow-up turn."
    )
    return _exit_blocked_with_closure(
        runner,
        ctx,
        loop=loop,
        message=msg,
        code="coding_budget_exhausted",
        telemetry_payload=telemetry_payload,
        allowed_tools=allowed_tools,
        build_blocked_result=build_blocked_result,
    )


def _exit_blocked_with_closure(
    runner: Any,
    ctx: ExecutionContext,
    *,
    loop: Any,
    message: str,
    code: str,
    telemetry_payload: dict[str, Any],
    allowed_tools: frozenset[str],
    build_blocked_result,
) -> ExecutionResult:
    salvaged_final_text = _salvage_reserved_closeout_from_existing_evidence(
        runner,
        interruption_detail=(
            "The reserved answer-only closeout was interrupted before the model "
            "could finish the summary, so this answer is derived from the existing "
            "coding evidence."
        ),
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
    fallback_text = _mutating_file_evidence_final_text(runner)
    if fallback_text:
        return _exit_final_text(
            runner,
            ctx,
            loop,
            fallback_text,
            allowed_tools,
            build_blocked_result=build_blocked_result,
        )
    blocked_action = build_blocked_result(message, code).model_copy(
        update={"outputs": telemetry_payload},
        deep=True,
    )

    try:
        judgment = ctx.evaluate_turn_closure(
            action_result=blocked_action,
            completion_reason=code,
        )
        disposition = ctx.apply_closure_judgment(judgment=judgment)
    except Exception:  # noqa: BLE001
        judgment = None
        disposition = None

    if (
        judgment is not None
        and disposition != BRAIN_DISPOSITION_CONTINUE
        and str(getattr(judgment, "final_answer", "") or "").strip()
    ):
        return _exit_closed_by_closure_gate(
            runner,
            ctx,
            loop=loop,
            message=message,
            code=code,
            telemetry_payload=telemetry_payload,
            blocked_action=blocked_action,
            judgment=judgment,
        )

    runner._finalize_checkpoint(ctx, terminal=False, cursor=loop.iteration)
    return ExecutionResult(
        status=BRAIN_STATE_WAITING_USER,
        working_state=ctx.state,
        message=message,
        action_result=blocked_action,
    )


def _exit_closed_by_closure_gate(
    runner: Any,
    ctx: ExecutionContext,
    *,
    loop: Any,
    message: str,
    code: str,
    telemetry_payload: dict[str, Any],
    blocked_action: ActionResult,
    judgment: Any,
) -> ExecutionResult:
    close_message = final_close_message(
        state=ctx.state,
        judgment=judgment,
        action_result=blocked_action,
        fallback_message=message,
    )
    resolved_action = blocked_action.model_copy(
        update={
            "status": BRAIN_ACTION_STATUS_SUCCESS,
            "summary": close_message,
            "error": None,
        },
        deep=True,
    )
    ctx.extract_success_memories(
        action_result=resolved_action,
        judgment=judgment,
    )
    ctx.emit_status(
        source_phase="coding.loop",
        detail_text="[act:coding] done",
        mode=BRAIN_DECISION_ROUTE_ACT,
        mode_state="done",
        terminal=True,
        payload={
            **telemetry_payload,
            "act.profile": BRAIN_ACT_PROFILE_CODING,
            "coding.closed_by_closure_gate": True,
            "coding.exhaustion_reason": code,
        },
    )
    runner._clear_coding_module_state(ctx)
    step_output = ctx.respond(
        message=close_message,
        status=BRAIN_STATE_DONE,
        action_result=resolved_action,
    )
    runner._finalize_checkpoint(ctx, terminal=True, cursor=loop.iteration)
    return ExecutionResult.from_step_output(step_output, judgment=judgment)
