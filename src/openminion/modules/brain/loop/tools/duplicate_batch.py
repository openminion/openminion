from __future__ import annotations

import time
from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_ACTION_STATUS_RETRY,
)
from openminion.modules.brain.schemas import ActionError, ActionResult, new_uuid
from openminion.modules.llm.schemas import Message

from .budget import _debit_llm_usage, _profile_budget_exhausted, _token_budget_exhausted
from .budget_control import (
    _answer_only_finalization_messages,
    _is_internal_failure_final_text,
    _llm_budget_available_for_answer_only,
)
from .contracts import (
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    ADAPTIVE_TERM_FINAL_TEXT,
    ADAPTIVE_TERM_LLM_ERROR,
    AdaptiveToolLoopContext,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
)
from .evidence import (
    _is_substantive_tool_name,
    _successful_substantive_tool_results,
)
from .postprocess.rules import _looks_like_unexecutable_tool_payload_text
from .runtime import _extract_visible_response_text
from .status import emit_adaptive_status


def _build_missing_action_result(tool_name: str) -> ActionResult:
    message = f"No action result from tool {tool_name!r}"
    return ActionResult(
        command_id=new_uuid(),
        status=BRAIN_ACTION_STATUS_FAILED,
        summary=message,
        error=ActionError(code="adaptive_tool_no_result", message=message),
    )


def _duplicate_batch_retry_counts(
    loop_state: AdaptiveToolLoopState,
) -> dict[str, int]:
    scratchpad = dict(loop_state.scratchpad or {})
    counts = scratchpad.get("duplicate_signature_retry_counts")
    if not isinstance(counts, dict):
        counts = {}
        scratchpad["duplicate_signature_retry_counts"] = counts
        loop_state.scratchpad = scratchpad
    return counts


def _duplicate_batch_recovery_message(tool_calls: list[Any]) -> Message:
    tool_names = [
        str(getattr(item, "name", "") or "").strip()
        for item in tool_calls
        if str(getattr(item, "name", "") or "").strip()
    ]
    rendered_tools = ", ".join(tool_names) if tool_names else "the previous tool batch"
    return Message(
        role="system",
        content=(
            f"The tool batch ({rendered_tools}) was already executed with the same "
            "arguments and produced tool results in this loop. Do not repeat the "
            "same tool call with identical arguments unless the prior tool result "
            "explicitly instructed you to poll or retry with changed inputs."
        ),
    )


def _duplicate_batch_execution_facts(
    loop_state: AdaptiveToolLoopState,
) -> dict[str, dict[str, Any]]:
    scratchpad = dict(loop_state.scratchpad or {})
    facts = scratchpad.get("duplicate_signature_execution_facts")
    if not isinstance(facts, dict):
        facts = {}
        scratchpad["duplicate_signature_execution_facts"] = facts
        loop_state.scratchpad = scratchpad
    return facts


def _reset_duplicate_batch_tracking(loop_state: AdaptiveToolLoopState) -> None:
    """Drop duplicate-batch memory when successful file mutation changed the workspace."""
    scratchpad = dict(loop_state.scratchpad or {})
    scratchpad.pop("duplicate_signature_retry_counts", None)
    scratchpad.pop("duplicate_signature_execution_facts", None)
    scratchpad.pop("duplicate_batch_answer_only_closure_pending", None)
    loop_state.scratchpad = scratchpad


def _action_result_has_retry_or_poll_signal(
    *,
    action_result: ActionResult,
    command_outcome: Any,
) -> bool:
    if getattr(command_outcome, "job", None) is not None:
        return True
    status = str(getattr(action_result, "status", "") or "").strip().lower()
    if status in {BRAIN_ACTION_STATUS_RETRY, BRAIN_ACTION_STATUS_NEEDS_USER}:
        return True
    outputs = getattr(action_result, "outputs", {})
    if not isinstance(outputs, dict):
        return False
    if bool(outputs.get("retryable")) or bool(outputs.get("_structured_retryable")):
        return True
    for key in ("poll_after_ms", "retry_after_ms", "wait_for_ms"):
        value = outputs.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return True
    return False


def _record_duplicate_batch_execution_facts(
    loop_state: AdaptiveToolLoopState,
    *,
    signature: str,
    ordered_tool_results: list[tuple[Any, Any]],
) -> None:
    if not signature or not ordered_tool_results:
        return
    all_success = True
    has_retry_or_poll = False
    has_substantive_success = False
    has_non_success = False
    has_job = False
    for tool_call, command_outcome in ordered_tool_results:
        action_result = getattr(
            command_outcome, "action_result", None
        ) or _build_missing_action_result(
            str(getattr(tool_call, "name", "") or "unknown").strip()
        )
        status = str(getattr(action_result, "status", "") or "").strip().lower()
        if status != "success":
            all_success = False
            has_non_success = True
        elif _is_substantive_tool_name(getattr(tool_call, "name", "")):
            has_substantive_success = True
        if getattr(command_outcome, "job", None) is not None:
            has_job = True
        if _action_result_has_retry_or_poll_signal(
            action_result=action_result,
            command_outcome=command_outcome,
        ):
            has_retry_or_poll = True
    _duplicate_batch_execution_facts(loop_state)[signature] = {
        "all_success": all_success,
        "has_job": has_job,
        "has_non_success": has_non_success,
        "has_retry_or_poll": has_retry_or_poll,
        "has_substantive_success": has_substantive_success,
        "answer_only_closure_consumed": False,
    }


def _eligible_duplicate_batch_execution_facts(
    loop_state: AdaptiveToolLoopState,
    *,
    signature: str,
) -> dict[str, Any] | None:
    facts = _duplicate_batch_execution_facts(loop_state).get(signature)
    if not isinstance(facts, dict):
        return None
    if bool(facts.get("answer_only_closure_consumed")):
        return None
    if not bool(facts.get("all_success")):
        return None
    if bool(facts.get("has_non_success")):
        return None
    if bool(facts.get("has_job")):
        return None
    if bool(facts.get("has_retry_or_poll")):
        return None
    if not bool(facts.get("has_substantive_success")):
        return None
    return facts


def _build_duplicate_batch_answer_only_closure_message(
    tool_calls: list[Any],
) -> Message:
    tool_names = [
        str(getattr(item, "name", "") or "").strip()
        for item in tool_calls
        if str(getattr(item, "name", "") or "").strip()
    ]
    rendered_tools = ", ".join(tool_names) if tool_names else "the previous tool batch"
    return Message(
        role="system",
        content=(
            f"The identical tool batch ({rendered_tools}) already completed "
            "successfully in this loop. Do not call more tools."
        ),
    )


def _duplicate_batch_answer_only_messages(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    loop_state: AdaptiveToolLoopState,
    tool_calls: list[Any],
) -> list[Message]:
    tool_results = _successful_substantive_tool_results(loop_state)
    messages = _answer_only_finalization_messages(
        loop_ctx=loop_ctx,
        loop_state=loop_state,
        tool_results=tool_results,
        reason=(
            "You have repeated an identical successful tool batch. Do not call "
            "more tools. This must be the final answer for the current turn."
        ),
    )
    if tool_results:
        return messages
    return [*messages, _build_duplicate_batch_answer_only_closure_message(tool_calls)]


def _looks_like_unexecutable_tool_markup_final_text(text: str) -> bool:
    return _looks_like_unexecutable_tool_payload_text(text)


def _truncate_duplicate_batch_fallback_text(value: Any, *, limit: int = 400) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}..."


def _duplicate_batch_fallback_final_text(loop_state: AdaptiveToolLoopState) -> str:
    tool_results = _successful_substantive_tool_results(loop_state)
    if not tool_results:
        return ""
    lines = [
        (
            "Result: tool work completed, but the model repeated an identical "
            "tool batch before writing a final answer."
        ),
        "",
        "Successful tool evidence:",
    ]
    for item in tool_results[-5:]:
        tool_name = str(item.get("tool_name") or "tool").strip() or "tool"
        summary = str(item.get("content") or "").strip()
        if not summary:
            data = item.get("data")
            if isinstance(data, dict):
                summary = str(data.get("summary") or data.get("stdout") or "").strip()
        lines.append(
            f"- {tool_name}: "
            f"{_truncate_duplicate_batch_fallback_text(summary) or 'success'}"
        )
    return "\n".join(lines)


def _duplicate_batch_evidence_fallback_outcome(
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    allowed_tools: frozenset[str],
    duration_ms: int,
    tokens_used: int,
) -> tuple[AdaptiveToolLoopOutcome, int, int] | None:
    fallback_final_text = _duplicate_batch_fallback_final_text(loop_state)
    if not fallback_final_text:
        return None
    loop_state.scratchpad[
        "duplicate_batch_answer_only_closure_used_evidence_fallback"
    ] = True
    loop_state.termination_reason = ADAPTIVE_TERM_FINAL_TEXT
    return (
        AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
            state=loop_state,
            allowed_tools=allowed_tools,
            final_text=fallback_final_text,
        ),
        duration_ms,
        tokens_used,
    )


def _duplicate_batch_budget_exhausted_outcome(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    allowed_tools: frozenset[str],
    public_mode_tag: str,
) -> tuple[AdaptiveToolLoopOutcome, int, int]:
    loop_state.termination_reason = ADAPTIVE_TERM_BUDGET_EXHAUSTED
    emit_adaptive_status(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        detail_text=f"{public_mode_tag} budget exhausted",
        mode_state="budget_exhausted",
        termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    )
    return (
        AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            state=loop_state,
            allowed_tools=allowed_tools,
        ),
        0,
        0,
    )


def _force_duplicate_batch_answer_only_closure(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    runtime: Any,
    model: str,
    tool_calls: list[Any],
    tool_specs: list[Any],
    max_output_tokens: int | None,
    metadata: dict[str, Any] | None,
    allowed_tools: frozenset[str],
    public_mode_tag: str,
    signature: str,
) -> tuple[AdaptiveToolLoopOutcome | None, int, int]:
    facts = _eligible_duplicate_batch_execution_facts(loop_state, signature=signature)
    if facts is None:
        return None, 0, 0
    if _token_budget_exhausted(loop_ctx, loop_state) or _profile_budget_exhausted(
        profile=profile,
        state=loop_state,
    ):
        return _duplicate_batch_budget_exhausted_outcome(
            loop_ctx=loop_ctx,
            profile=profile,
            loop_state=loop_state,
            allowed_tools=allowed_tools,
            public_mode_tag=public_mode_tag,
        )
    if not _llm_budget_available_for_answer_only(
        loop_ctx=loop_ctx,
        profile=profile,
        loop_state=loop_state,
        reserve_final_answer=True,
    ):
        return None, 0, 0
    facts["answer_only_closure_consumed"] = True
    loop_state.scratchpad["duplicate_batch_answer_only_closure_forced"] = True
    finalization_messages = _duplicate_batch_answer_only_messages(
        loop_ctx=loop_ctx,
        loop_state=loop_state,
        tool_calls=tool_calls,
    )
    emit_adaptive_status(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        detail_text=f"{public_mode_tag} duplicate batch answer-only closure",
        mode_state="duplicate_tool_closure",
    )
    closure_start = time.monotonic()
    try:
        response = runtime.complete(
            messages=finalization_messages,
            tools=[],
            model=model,
            tool_choice="none",
            max_output_tokens=int(max_output_tokens)
            if max_output_tokens is not None
            else None,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        loop_state.termination_reason = ADAPTIVE_TERM_LLM_ERROR
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} duplicate batch closure failed",
            mode_state="llm_error",
            termination_reason=ADAPTIVE_TERM_LLM_ERROR,
        )
        return (
            AdaptiveToolLoopOutcome(
                profile_name=profile.profile_name,
                mode_name=profile.mode_name,
                termination_reason=ADAPTIVE_TERM_LLM_ERROR,
                state=loop_state,
                allowed_tools=allowed_tools,
                error_message=str(exc),
            ),
            0,
            0,
        )
    duration_ms = int((time.monotonic() - closure_start) * 1000)
    usage = getattr(response, "usage", None)
    tokens_used = int(getattr(usage, "input_tokens", 0) or 0) + int(
        getattr(usage, "output_tokens", 0) or 0
    )
    _debit_llm_usage(loop_ctx, response)
    loop_state.llm_calls += 1
    if not bool(getattr(response, "ok", False)):
        error = getattr(response, "error", None)
        error_message = str(getattr(error, "message", "") or "LLM returned not-ok")
        loop_state.termination_reason = ADAPTIVE_TERM_LLM_ERROR
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} duplicate batch closure error",
            mode_state="llm_error",
            termination_reason=ADAPTIVE_TERM_LLM_ERROR,
        )
        return (
            AdaptiveToolLoopOutcome(
                profile_name=profile.profile_name,
                mode_name=profile.mode_name,
                termination_reason=ADAPTIVE_TERM_LLM_ERROR,
                state=loop_state,
                allowed_tools=allowed_tools,
                error_message=error_message,
            ),
            duration_ms,
            tokens_used,
        )
    if list(getattr(response, "tool_calls", []) or []):
        loop_state.scratchpad[
            "duplicate_batch_answer_only_closure_returned_tool_calls"
        ] = True
        fallback_outcome = _duplicate_batch_evidence_fallback_outcome(
            profile=profile,
            loop_state=loop_state,
            allowed_tools=allowed_tools,
            duration_ms=duration_ms,
            tokens_used=tokens_used,
        )
        if fallback_outcome is not None:
            return fallback_outcome
        loop_state.termination_reason = ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} duplicate batch closure still wanted tools",
            mode_state="duplicate_tool_calls",
            termination_reason=ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
        )
        return (
            AdaptiveToolLoopOutcome(
                profile_name=profile.profile_name,
                mode_name=profile.mode_name,
                termination_reason=ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
                state=loop_state,
                allowed_tools=allowed_tools,
                error_message=(
                    "Answer-only closure returned more tool calls after an "
                    "identical successful tool batch had already completed."
                ),
            ),
            duration_ms,
            tokens_used,
        )
    for assistant_message in list(getattr(response, "assistant_messages", []) or []):
        loop_state.messages.append(assistant_message)
    final_text = _extract_visible_response_text(response)
    if _looks_like_unexecutable_tool_markup_final_text(final_text):
        loop_state.scratchpad["duplicate_batch_closure_raw_tool_markup_rejected"] = True
        return None, duration_ms, tokens_used
    if _is_internal_failure_final_text(final_text):
        loop_state.scratchpad["duplicate_batch_closure_invalid_final_text"] = str(
            final_text or ""
        ).strip()
        return None, duration_ms, tokens_used
    if not final_text:
        loop_state.termination_reason = ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} repeated tool batch",
            mode_state="duplicate_tool_calls",
            termination_reason=ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
        )
        return (
            AdaptiveToolLoopOutcome(
                profile_name=profile.profile_name,
                mode_name=profile.mode_name,
                termination_reason=ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
                state=loop_state,
                allowed_tools=allowed_tools,
                error_message=(
                    "Answer-only closure did not return a final answer after an "
                    "identical successful tool batch had already completed."
                ),
            ),
            duration_ms,
            tokens_used,
        )
    loop_state.termination_reason = ADAPTIVE_TERM_FINAL_TEXT
    return (
        AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
            state=loop_state,
            allowed_tools=allowed_tools,
            final_text=final_text,
        ),
        duration_ms,
        tokens_used,
    )
