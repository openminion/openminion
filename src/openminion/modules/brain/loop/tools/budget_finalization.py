from __future__ import annotations

from typing import Any

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.modules.brain.loop.constants import (
    BUDGET_FINALIZATION_STATUS_RETRY_PROMPT,
    FINALIZED_ANSWER_RECOVERY_GUIDANCE,
)
from openminion.modules.brain.schemas import FinalizationStatus
from openminion.modules.brain.loop.tools.structured_llm import (
    structured_mode_response,
)
from openminion.modules.llm.contracts import detect_raw_tool_payload_json
from openminion.modules.llm.schemas import Message, ToolSpec
from pydantic import Field, ValidationError

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
    _extract_visible_response_text,
    _normalize_finalization_status_response,
    _normalize_submit_output_final_answer_response,
)
from .status import emit_adaptive_status


class _FinalizedAnswer(FinalizationStatus):
    final_answer: str = Field(min_length=1)


def _budget_finalization_original_request(loop_ctx: AdaptiveToolLoopContext) -> str:
    state = getattr(loop_ctx, "state", None)
    candidates = (
        getattr(loop_ctx, "user_input", ""),
        getattr(state, "last_user_input", "") if state is not None else "",
        getattr(state, "goal", "") if state is not None else "",
        getattr(state, "pending_confirmation_last_user_input", "")
        if state is not None
        else "",
    )
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _last_user_message_text(messages: list[Message]) -> str:
    for message in reversed(messages):
        if str(getattr(message, "role", "") or "").strip().lower() != "user":
            continue
        text = str(getattr(message, "content", "") or "").strip()
        if text:
            return text
    return ""


def _retry_answer_only_completion_if_needed(
    *,
    response: Any,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    runtime: Any,
    messages: list[Message],
    complete_kwargs: dict[str, Any],
    public_mode_tag: str,
    allowed_tools: list[str],
    stop_outcome: Any,
) -> tuple[Any, AdaptiveToolLoopOutcome | None]:
    has_tool_attempt = (
        bool(list(getattr(response, "tool_calls", []) or []))
        or detect_raw_tool_payload_json(_extract_visible_response_text(response))
        or getattr(response, "empty_payload_recovered", False) is True
    )
    if not has_tool_attempt:
        return response, None
    retry_key = "budget_answer_only_tool_choice_none_retry_used"
    if bool(loop_state.scratchpad.get(retry_key, False)):
        return _normalize_finalization_status_response(response), None

    loop_state.scratchpad[retry_key] = True
    _debit_llm_usage(loop_ctx, response)
    loop_state.llm_calls += 1
    retry_messages = list(messages)
    retry_messages.extend(list(getattr(response, "assistant_messages", []) or []))
    retry_messages.append(
        Message(
            role="system",
            content=(
                "The budget finalization step is answer-only. Call submit_output "
                "once with the complete user-facing answer in final_answer and its "
                "truthful typed status. Preserve the original request's exact "
                "labels, headings, and ordering."
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
        retry_kwargs = dict(complete_kwargs)
        retry_kwargs["tools"] = [_finalized_answer_tool()]
        retry_kwargs["tool_choice"] = {
            "type": "function",
            "function": {"name": "submit_output"},
        }
        retried_response = runtime.complete(
            messages=retry_messages,
            **retry_kwargs,
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
    retried_response = _normalize_submit_output_final_answer_response(retried_response)
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
    return None


def _finalization_status_from_response(response: Any) -> dict[str, Any] | None:
    payload = getattr(response, STATE_KEY_FINALIZATION_STATUS, None)
    if not isinstance(payload, dict):
        tool_calls = list(getattr(response, "tool_calls", []) or [])
        if len(tool_calls) != 1 or tool_calls[0].name != "submit_output":
            return None
        payload = tool_calls[0].arguments
    try:
        return FinalizationStatus.model_validate(payload).model_dump(mode="json")
    except ValidationError:
        return None


def _finalized_answer_tool() -> ToolSpec:
    return ToolSpec(
        name="submit_output",
        description="Submit the final user-facing answer and its typed status.",
        input_schema=_FinalizedAnswer.model_json_schema(),
        strict=True,
    )


def _finalization_status_tool() -> ToolSpec:
    return ToolSpec(
        name="submit_output",
        description="Submit the typed finalization status for the prior answer.",
        input_schema=FinalizationStatus.model_json_schema(),
        strict=True,
    )


def _submit_output_response(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    loop_state: AdaptiveToolLoopState,
    runtime: Any,
    messages: list[Message],
    tool: ToolSpec,
    model: str,
    max_output_tokens: int | None,
    metadata: dict[str, Any] | None,
) -> Any | None:
    try:
        response = runtime.complete(
            messages=messages,
            tools=[tool],
            model=model,
            tool_choice={"type": "function", "function": {"name": "submit_output"}},
            max_output_tokens=max_output_tokens,
            metadata=metadata,
        )
    except (RuntimeError, TypeError, ValueError):
        return None
    _debit_llm_usage(loop_ctx, response)
    loop_state.llm_calls += 1
    return response


def _recover_finalized_answer(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    runtime: Any,
    model: str,
    max_output_tokens: int | None,
    metadata: dict[str, Any] | None,
    public_mode_tag: str,
) -> _FinalizedAnswer | None:
    structured = structured_mode_response(
        loop_ctx,
        prompt=FINALIZED_ANSWER_RECOVERY_GUIDANCE,
        schema=_FinalizedAnswer,
        purpose="summarize",
        max_tokens=max_output_tokens or 4000,
    )
    if isinstance(structured, _FinalizedAnswer):
        loop_state.llm_calls += 1
        return structured
    messages = list(loop_state.messages)
    messages.append(
        Message(
            role="system",
            content=(
                "Successful tool results are already available. Do not call more "
                "tools. Call submit_output once with the complete user-facing "
                "answer in final_answer and the truthful typed status. Preserve "
                "the user's exact requested labels, headings, and ordering."
            ),
        )
    )
    emit_adaptive_status(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        detail_text=f"{public_mode_tag} structured final-answer recovery",
        mode_state="structured_final_answer_recovery",
    )
    response = _submit_output_response(
        loop_ctx=loop_ctx,
        loop_state=loop_state,
        runtime=runtime,
        messages=messages,
        tool=_finalized_answer_tool(),
        model=model,
        max_output_tokens=max_output_tokens,
        metadata=metadata,
    )
    if response is None:
        return None
    tool_calls = list(getattr(response, "tool_calls", []) or [])
    if len(tool_calls) != 1 or tool_calls[0].name != "submit_output":
        return None
    try:
        return _FinalizedAnswer.model_validate(tool_calls[0].arguments)
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
    structured = structured_mode_response(
        loop_ctx,
        prompt=BUDGET_FINALIZATION_STATUS_RETRY_PROMPT,
        schema=FinalizationStatus,
        purpose="summarize",
        max_tokens=max_output_tokens or 1200,
    )
    if isinstance(structured, FinalizationStatus):
        loop_state.llm_calls += 1
        return structured.model_dump(mode="json")
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
    retry_response = _submit_output_response(
        loop_ctx=loop_ctx,
        loop_state=loop_state,
        runtime=runtime,
        messages=retry_messages,
        tool=_finalization_status_tool(),
        model=model,
        max_output_tokens=max_output_tokens,
        metadata=metadata,
    )
    if retry_response is None:
        return None
    retry_response = _normalize_finalization_status_response(retry_response)
    return _finalization_status_from_response(retry_response)


def _looks_like_budget_raw_tool_payload_text(text: str) -> bool:
    from .postprocess.rules import _looks_like_unexecutable_tool_payload_text

    return _looks_like_unexecutable_tool_payload_text(text)
