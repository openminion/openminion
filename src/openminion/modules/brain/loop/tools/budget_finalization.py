from __future__ import annotations

from typing import Any

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.modules.brain.loop.constants import (
    BUDGET_FINALIZATION_STATUS_RETRY_PROMPT,
)
from openminion.modules.brain.schemas import FinalizationStatus
from openminion.modules.llm.schemas import Message
from pydantic import ValidationError

from .budget import _debit_llm_usage
from .contracts import (
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_FINAL_TEXT,
    ADAPTIVE_TERM_FINALIZATION_BLOCKED,
    ADAPTIVE_TERM_FINALIZATION_INCOMPLETE,
    AdaptiveToolLoopContext,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
)
from .runtime import (
    _normalize_finalization_status_response,
)
from .status import emit_adaptive_status


def _retry_answer_only_completion_if_needed(
    *,
    response: Any,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    runtime: Any,
    complete_kwargs: dict[str, Any],
    public_mode_tag: str,
    allowed_tools: list[str],
    stop_outcome: Any,
) -> tuple[Any, AdaptiveToolLoopOutcome | None]:
    if not list(getattr(response, "tool_calls", []) or []):
        return response, None
    retry_key = "budget_answer_only_tool_choice_none_retry_used"
    if bool(loop_state.scratchpad.get(retry_key, False)):
        return _normalize_finalization_status_response(response), None

    loop_state.scratchpad[retry_key] = True
    retry_messages = list(loop_state.messages)
    retry_messages.extend(list(getattr(response, "assistant_messages", []) or []))
    retry_messages.append(
        Message(
            role="system",
            content=(
                "Do not call tools. The budget finalization step is answer-only. "
                "Use the successful tool results already in context and return only "
                "the final user-facing answer now."
            ),
        )
    )
    emit_adaptive_status(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        detail_text=f"{public_mode_tag} answer-only retry after tool call",
        mode_state="budget_answer_only_retry",
    )
    try:
        retried_response = runtime.complete(
            messages=retry_messages,
            **complete_kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        loop_state.scratchpad["budget_answer_only_finalization_error"] = str(exc)
        return response, stop_outcome(
            loop_ctx=loop_ctx,
            profile=profile,
            loop_state=loop_state,
            allowed_tools=allowed_tools,
            public_mode_tag=public_mode_tag,
            reason="answer_only_finalization_retry_failed",
        )
    return _normalize_finalization_status_response(retried_response), None


def _reject_invalid_answer_only_final_text(
    *,
    final_text: str,
    response: Any,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    allowed_tools: list[str],
    has_tool_evidence: bool,
) -> AdaptiveToolLoopOutcome | None:
    normalized_final_text = str(final_text or "").strip()
    if (
        not normalized_final_text
        or list(getattr(response, "tool_calls", []) or [])
        or not has_tool_evidence
    ):
        return None
    if _looks_like_budget_raw_tool_payload_text(normalized_final_text):
        loop_state.scratchpad["budget_answer_only_finalization_raw_tool_rejected"] = (
            normalized_final_text
        )
        loop_state.termination_reason = ADAPTIVE_TERM_BUDGET_EXHAUSTED
        return AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            state=loop_state,
            allowed_tools=allowed_tools,
            error_message=(
                "Answer-only budget finalization produced raw tool markup instead "
                "of a user-facing answer."
            ),
        )
    if _looks_like_budget_execution_preface_draft(normalized_final_text):
        loop_state.scratchpad["budget_answer_only_finalization_rejected_text"] = (
            normalized_final_text
        )
        loop_state.termination_reason = ADAPTIVE_TERM_BUDGET_EXHAUSTED
        return AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            state=loop_state,
            allowed_tools=allowed_tools,
            error_message=(
                "Answer-only budget finalization produced execution-preface draft text."
            ),
        )
    return None


def _finalization_status_from_response(response: Any) -> dict[str, Any] | None:
    payload = getattr(response, STATE_KEY_FINALIZATION_STATUS, None)
    if not isinstance(payload, dict):
        return None
    try:
        return FinalizationStatus.model_validate(payload).model_dump(mode="json")
    except ValidationError:
        return None


def _termination_reason_for_status(status: str) -> str:
    if status == "blocked":
        return ADAPTIVE_TERM_FINALIZATION_BLOCKED
    if status == "incomplete":
        return ADAPTIVE_TERM_FINALIZATION_INCOMPLETE
    return ADAPTIVE_TERM_FINAL_TEXT


def _recover_budget_finalization_status(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    runtime: Any,
    model: str,
    max_output_tokens: int | None,
    metadata: dict[str, Any] | None,
    final_text: str,
    public_mode_tag: str,
) -> dict[str, Any] | None:
    loop_state.scratchpad["budget_finalization_status_retry_used"] = True
    retry_messages = list(loop_state.messages)
    retry_messages.append(Message(role="assistant", content=final_text))
    retry_messages.append(
        Message(role="system", content=BUDGET_FINALIZATION_STATUS_RETRY_PROMPT)
    )
    emit_adaptive_status(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        detail_text=f"{public_mode_tag} budget finalization status retry",
        mode_state="budget_finalization_status_retry",
    )
    try:
        retry_response = runtime.complete(
            messages=retry_messages,
            tools=[],
            model=model,
            tool_choice="none",
            max_output_tokens=int(max_output_tokens)
            if max_output_tokens is not None
            else None,
            metadata=metadata,
        )
    except Exception:  # noqa: BLE001
        return None
    retry_response = _normalize_finalization_status_response(retry_response)
    _debit_llm_usage(loop_ctx, retry_response)
    loop_state.llm_calls += 1
    return _finalization_status_from_response(retry_response)


def _looks_like_budget_execution_preface_draft(text: str) -> bool:
    from .postprocess.rules import _looks_like_execution_preface_draft

    return _looks_like_execution_preface_draft(text)


def _looks_like_budget_raw_tool_payload_text(text: str) -> bool:
    from .postprocess.rules import _looks_like_unexecutable_tool_payload_text

    return _looks_like_unexecutable_tool_payload_text(text)
