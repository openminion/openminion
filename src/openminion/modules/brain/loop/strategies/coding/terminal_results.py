from collections.abc import Callable
from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_DISPOSITION_CONTINUE,
    BRAIN_DECISION_ROUTE_ACT,
    BRAIN_STATE_CONTINUE,
    BRAIN_STATE_DONE,
    BRAIN_STATE_WAITING_USER,
)
from openminion.modules.brain.execution.closure import final_close_message
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.loop.tools.postprocess.evidence_closeout import (
    mutating_file_evidence_can_closeout,
    mutating_file_evidence_fallback_text,
)
from openminion.modules.brain.schemas.base import new_uuid
from openminion.modules.brain.schemas.state.action import ActionResult

from .closeout_salvage import salvage_reserved_closeout_from_existing_evidence
from .contracts import CODING_TERM_VERIFY_CAP_EXCEEDED

BuildBlockedResult = Callable[[str, str], ActionResult]


def _emit_loop_status(
    ctx: ExecutionContext,
    *,
    detail_text: str,
    mode_state: str,
    telemetry_payload: dict[str, Any],
    terminal: bool = False,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    payload = {**telemetry_payload, "act.profile": BRAIN_ACT_PROFILE_CODING}
    if extra_payload:
        payload.update(extra_payload)
    ctx.emit_status(
        source_phase="coding.loop",
        detail_text=detail_text,
        mode=BRAIN_DECISION_ROUTE_ACT,
        mode_state=mode_state,
        terminal=terminal,
        payload=payload,
    )


def _salvaged_or_mutating_final_text(runner: Any, interruption_detail: str) -> str:
    return salvage_reserved_closeout_from_existing_evidence(
        runner,
        interruption_detail=interruption_detail,
    ) or _mutating_file_evidence_final_text(runner)


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
    _emit_loop_status(
        ctx,
        detail_text=summary,
        mode_state="continue",
        telemetry_payload=telemetry_payload,
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
    build_blocked_result: BuildBlockedResult,
) -> ExecutionResult:
    loop = runner._loop_state
    salvaged_final_text = salvage_reserved_closeout_from_existing_evidence(
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
    _emit_loop_status(
        ctx,
        detail_text=summary,
        mode_state="blocked",
        telemetry_payload=telemetry_payload,
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
    build_blocked_result: BuildBlockedResult,
) -> ExecutionResult:
    del build_blocked_result
    telemetry_payload = loop.telemetry_payload(allowed_tools)
    final_action = ActionResult(
        command_id=new_uuid(),
        status=BRAIN_ACTION_STATUS_SUCCESS,
        summary=output_text or "[act:coding] done",
        outputs=telemetry_payload,
    )

    _emit_loop_status(
        ctx,
        detail_text="[act:coding] done",
        mode_state="done",
        telemetry_payload=telemetry_payload,
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
    if not mutating_file_evidence_can_closeout(runner._loop_state):
        return ""
    return mutating_file_evidence_fallback_text(runner._loop_state)


def _exit_budget_exhausted(
    runner: Any,
    ctx: ExecutionContext,
    loop: Any,
    allowed_tools: frozenset[str],
    *,
    build_blocked_result: BuildBlockedResult,
) -> ExecutionResult:
    fallback_text = _salvaged_or_mutating_final_text(
        runner,
        "The reserved answer-only closeout was interrupted by budget exhaustion, "
        "so this summary is derived from the existing coding evidence.",
    )
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
    build_blocked_result: BuildBlockedResult,
) -> ExecutionResult:
    fallback_text = _salvaged_or_mutating_final_text(
        runner,
        "The reserved answer-only closeout was interrupted before the model could "
        "finish the summary, so this answer is derived from the existing coding "
        "evidence.",
    )
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
    _emit_loop_status(
        ctx,
        detail_text="[act:coding] done",
        mode_state="done",
        terminal=True,
        telemetry_payload=telemetry_payload,
        extra_payload={
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
