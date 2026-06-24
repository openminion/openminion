from __future__ import annotations

from typing import Any

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.modules.brain.constants import (
    BRAIN_STATE_WAITING_USER,
    STOP_NOOP_GUARD,
    STOP_USER_DECLINED,
    STOP_USER_TIMEOUT,
)
from openminion.modules.brain.schemas import (
    AdaptiveBudgetConfig,
    AskUserCommand,
)
from openminion.modules.llm.schemas import Message

from .budget import _debit_llm_usage
from .budget_extension import (
    apply_extension,
    check_safety_rails,
    compose_pause_question,
    get_session_extensions_used,
    mark_pending_extension,
    record_session_extension,
)
from .contracts import (
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_FINAL_TEXT,
    ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
    ADAPTIVE_TERM_LLM_ERROR,
    ADAPTIVE_TERM_NEEDS_USER,
    AdaptiveToolLoopContext,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
)
from .status import emit_adaptive_status


def _effective_cap(
    profile: AdaptiveToolLoopProfile, loop_state: AdaptiveToolLoopState
) -> int:
    """AIB-06: read the dynamic iteration cap."""
    dynamic = int(getattr(loop_state, "effective_max_iterations", 0) or 0)
    if dynamic > 0:
        return dynamic
    return int(profile.max_iterations)


def _adaptive_budget_config(
    profile: AdaptiveToolLoopProfile,
) -> AdaptiveBudgetConfig | None:
    raw = getattr(profile, "adaptive_budget_config", None)
    if isinstance(raw, AdaptiveBudgetConfig):
        return raw
    if isinstance(raw, dict):
        return AdaptiveBudgetConfig.model_validate(raw)
    return None


def _emit_budget_event(
    loop_ctx: AdaptiveToolLoopContext,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    _emit_budget_progress(loop_ctx=loop_ctx, event_type=event_type, payload=payload)
    state = getattr(loop_ctx, "state", None)
    if state is None:
        return
    session_id = str(getattr(state, "session_id", "") or "").strip()
    if not session_id:
        return
    session_api = getattr(loop_ctx, "session_api", None)
    if session_api is None:
        runner = getattr(loop_ctx, "_runner", None)
        session_api = getattr(runner, "session_api", None)
    append_event = getattr(session_api, "append_event", None)
    if not callable(append_event):
        return
    trace_id = str(getattr(state, "trace_id", "") or "").strip()
    try:
        append_event(
            session_id,
            event_type,
            dict(payload),
            actor_type="agent",
            actor_id=str(getattr(state, "agent_id", "") or "").strip() or None,
            trace={"trace_id": trace_id} if trace_id else None,
            importance=2,
            redaction="none",
            status="ok",
        )
    except Exception:  # noqa: BLE001 - budget telemetry must not break the loop
        return


def _emit_budget_progress(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    runner = getattr(loop_ctx, "_runner", None)
    callback = getattr(runner, "_progress_callback", None)
    if not callable(callback):
        return
    state = getattr(loop_ctx, "state", None)
    trace_id = str(getattr(state, "trace_id", "") or "").strip() if state else ""
    progress_payload = {
        "kind": "budget_event",
        "event_type": str(event_type or "").strip(),
        "trace_id": trace_id,
        **dict(payload),
    }
    try:
        callback(progress_payload)
    except Exception:  # noqa: BLE001 - observability must not break execution
        return


def _event_type_for_budget_stop(reason: str) -> str:
    return {
        STOP_NOOP_GUARD: "budget.noop_guard",
        STOP_USER_DECLINED: "budget.user_declined",
        STOP_USER_TIMEOUT: "budget.user_timeout",
    }.get(reason, "budget.exhausted")


def _emit_high_watermark_if_needed(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    loop_state: AdaptiveToolLoopState,
    cap: int,
) -> None:
    if cap < 64 or bool(loop_state.scratchpad.get("aib.high_watermark_emitted")):
        return
    loop_state.scratchpad["aib.high_watermark_emitted"] = True
    _emit_budget_event(
        loop_ctx,
        "budget.high_watermark",
        {"cap": int(cap), "hard_cap": 128},
    )


def _budget_stop_outcome(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    allowed_tools: frozenset[str],
    public_mode_tag: str,
    reason: str,
) -> AdaptiveToolLoopOutcome:
    loop_state.termination_reason = ADAPTIVE_TERM_BUDGET_EXHAUSTED
    _emit_budget_event(
        loop_ctx,
        _event_type_for_budget_stop(reason),
        {
            "cap": _effective_cap(profile, loop_state),
            "extensions_used": int(getattr(loop_state, "extensions_used", 0) or 0),
            "reason": reason,
        },
    )
    emit_adaptive_status(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        detail_text=f"{public_mode_tag} budget exhausted",
        mode_state="budget_exhausted",
        termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    )
    return AdaptiveToolLoopOutcome(
        profile_name=profile.profile_name,
        mode_name=profile.mode_name,
        termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
        state=loop_state,
        allowed_tools=allowed_tools,
    )


def _step_summaries_from_state(loop_ctx: AdaptiveToolLoopContext) -> tuple[str, ...]:
    state = getattr(loop_ctx, "state", None)
    items = list(getattr(state, "step_outputs", []) or []) if state is not None else []
    return tuple(
        summary
        for item in items
        if (summary := str(getattr(item, "summary", "") or "").strip())
    )


def _active_work_summary_from_state(loop_ctx: AdaptiveToolLoopContext) -> str:
    state = getattr(loop_ctx, "state", None)
    pending = getattr(state, "pending_turn_context", None)
    return str(getattr(pending, "active_work_summary", "") or "").strip()


def _max_steps_hint_from_state(loop_ctx: AdaptiveToolLoopContext) -> int | None:
    state = getattr(loop_ctx, "state", None)
    raw = getattr(state, "decision_max_steps_hint", None)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _answer_only_finalization_contract_requested(
    loop_ctx: AdaptiveToolLoopContext,
    loop_state: AdaptiveToolLoopState,
) -> bool:
    texts = [
        str(getattr(message, "content", "") or "")
        for message in list(getattr(loop_state, "messages", []) or [])
        if str(getattr(message, "role", "") or "").strip().lower() == "user"
    ]
    state = getattr(loop_ctx, "state", None)
    if state is not None:
        texts.extend(
            [
                str(getattr(state, "last_user_input", "") or ""),
                str(getattr(state, "goal", "") or ""),
            ]
        )
    return any(STATE_KEY_FINALIZATION_STATUS in text for text in texts)


def _maybe_extend_iteration_budget(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    allowed_tools: frozenset[str],
    public_mode_tag: str,
) -> AdaptiveToolLoopOutcome | bool:
    config = _adaptive_budget_config(profile)
    if config is None:
        return False
    if int(getattr(loop_state, "effective_max_iterations", 0) or 0) <= 0:
        loop_state.effective_max_iterations = int(profile.max_iterations)
    state = getattr(loop_ctx, "state", None)
    session_extensions_used = (
        get_session_extensions_used(state=state) if state is not None else 0
    )
    stop_reason = check_safety_rails(
        config=config,
        loop_state=loop_state,
        session_extensions_used=session_extensions_used,
        tokens_used=0,
        max_total_llm_tokens=0,
    )
    if stop_reason is not None:
        return _budget_stop_outcome(
            loop_ctx=loop_ctx,
            profile=profile,
            loop_state=loop_state,
            allowed_tools=allowed_tools,
            public_mode_tag=public_mode_tag,
            reason=stop_reason,
        )

    mode = str(getattr(config, "mode", "") or "interactive").strip().lower()
    if mode == "autonomous":
        old_cap = _effective_cap(profile, loop_state)
        new_cap = apply_extension(config=config, loop_state=loop_state)
        if state is not None:
            record_session_extension(state=state)
        _emit_budget_event(
            loop_ctx,
            "budget.extended",
            {
                "by": int(new_cap) - int(old_cap),
                "total": int(new_cap),
                "extensions_used": int(getattr(loop_state, "extensions_used", 0) or 0),
                "trigger": "auto",
            },
        )
        _emit_high_watermark_if_needed(
            loop_ctx=loop_ctx,
            loop_state=loop_state,
            cap=int(new_cap),
        )
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} budget extended",
            mode_state="budget_extended",
        )
        return True

    if state is not None:
        cap = _effective_cap(profile, loop_state)
        question = compose_pause_question(
            config=config,
            loop_state=loop_state,
            active_work_summary=_active_work_summary_from_state(loop_ctx),
            step_summaries=_step_summaries_from_state(loop_ctx),
            max_steps_hint=_max_steps_hint_from_state(loop_ctx),
        )
        command = AskUserCommand(
            title="Iteration budget reached",
            question=question,
            inputs={"adaptive_budget_extension": True, "cap": cap},
            success_criteria={"extension_approved": True},
            timeout_ms=int(config.idle_timeout_s) * 1000,
        )
        state.pending_confirmation_command = command
        state.post_action_user_message = question
        state.status = BRAIN_STATE_WAITING_USER
        mark_pending_extension(
            state=state,
            cap_at_pause=cap,
            extend_by=int(config.extend_by),
            idle_timeout_s=int(config.idle_timeout_s),
        )
        _emit_budget_event(
            loop_ctx,
            "budget.exhausted",
            {
                "cap": cap,
                "extensions_used": int(getattr(loop_state, "extensions_used", 0) or 0),
                "reason": "awaiting_user_extension_approval",
            },
        )
    loop_state.termination_reason = ADAPTIVE_TERM_NEEDS_USER
    emit_adaptive_status(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        detail_text=f"{public_mode_tag} awaiting budget extension approval",
        mode_state="needs_user",
        termination_reason=ADAPTIVE_TERM_NEEDS_USER,
    )
    return AdaptiveToolLoopOutcome(
        profile_name=profile.profile_name,
        mode_name=profile.mode_name,
        termination_reason=ADAPTIVE_TERM_NEEDS_USER,
        state=loop_state,
        allowed_tools=allowed_tools,
    )


def _general_profile_name(profile: AdaptiveToolLoopProfile) -> bool:
    return str(profile.profile_name or "").strip() == "general_adaptive_v1"


def _llm_budget_available_for_answer_only(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    reserve_final_answer: bool = False,
) -> bool:
    state = loop_ctx.state
    budgets = getattr(state, "budgets_remaining", None)
    if budgets is not None and int(getattr(budgets, "tokens", 1) or 0) <= 0:
        return False
    if not reserve_final_answer and int(
        getattr(state, "llm_calls_used", 0) or 0
    ) >= int(getattr(state, "llm_calls_max", 0) or 0):
        return False
    if (
        not reserve_final_answer
        and profile.max_llm_calls_per_loop is not None
        and loop_state.llm_calls >= int(profile.max_llm_calls_per_loop)
    ):
        return False
    return True


def _tool_budget_exhausted_for_answer_only(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
) -> bool:
    budgets = getattr(loop_ctx.state, "budgets_remaining", None)
    if (
        budgets is not None
        and int(getattr(budgets, "tool_calls", 1) or 0) <= 0
        and loop_state.total_tool_calls > 0
    ):
        return True
    return bool(
        profile.max_tool_calls_per_loop is not None
        and loop_state.total_tool_calls >= int(profile.max_tool_calls_per_loop)
    )


def _has_tool_evidence_for_answer_only(
    loop_ctx: AdaptiveToolLoopContext,
    loop_state: AdaptiveToolLoopState,
) -> bool:
    if int(getattr(loop_state, "total_tool_calls", 0) or 0) > 0:
        return True
    for message in list(getattr(loop_state, "messages", []) or []):
        if str(getattr(message, "role", "") or "").strip().lower() == "tool":
            if str(getattr(message, "content", "") or "").strip():
                return True
    state = getattr(loop_ctx, "state", None)
    if state is None:
        return False
    last_result = getattr(state, "last_result", None)
    if str(getattr(last_result, "status", "") or "").strip().lower() == "success":
        return True
    for item in list(getattr(state, "step_outputs", []) or []):
        if str(getattr(item, "summary", "") or "").strip():
            return True
        outputs = getattr(item, "outputs", None)
        if isinstance(outputs, dict) and outputs:
            return True
    return False


def _force_budget_answer_only_finalization(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    runtime: Any,
    model: str,
    max_output_tokens: int | None,
    metadata: dict[str, Any] | None,
    allowed_tools: frozenset[str],
    public_mode_tag: str,
) -> AdaptiveToolLoopOutcome | None:
    if not _general_profile_name(profile) and not _has_tool_evidence_for_answer_only(
        loop_ctx, loop_state
    ):
        return None
    if not _llm_budget_available_for_answer_only(
        loop_ctx=loop_ctx,
        profile=profile,
        loop_state=loop_state,
        reserve_final_answer=True,
    ):
        return None
    loop_state.scratchpad["budget_answer_only_finalization_forced"] = True
    if not _budget_finalization_has_substantive_user_message(loop_state.messages):
        original_request = _budget_finalization_original_request(loop_ctx)
        if original_request:
            loop_state.messages.append(
                Message(
                    role="user",
                    content=(
                        "Original user request for this turn:\n"
                        f"{original_request}\n\n"
                        "Use this request and the existing tool results as the task "
                        "context for the final answer. Do not infer or substitute a "
                        "different task."
                    ),
                )
            )
    loop_state.messages.append(
        Message(
            role="system",
            content=(
                "The tool budget or a per-tool limit has been reached. Do not call "
                "more tools. This must be the final answer for the current turn. "
                "Use the successful tool results already available and write the "
                "best user-facing final answer now. Do not narrate future steps, "
                "do not say you will continue, and preserve any explicit output "
                "format, headings, citation requirements, and exact-date "
                "requirements the user requested. If evidence is partial, say "
                "that briefly and still answer. If the turn has a typed "
                "finalization_status contract, preserve it."
            ),
        )
    )
    emit_adaptive_status(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        detail_text=f"{public_mode_tag} answer-only budget finalization",
        mode_state="budget_answer_only_finalization",
    )
    try:
        response = runtime.complete(
            messages=loop_state.messages,
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
            detail_text=f"{public_mode_tag} answer-only budget finalization failed",
            mode_state="llm_error",
            termination_reason=ADAPTIVE_TERM_LLM_ERROR,
        )
        return AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_LLM_ERROR,
            state=loop_state,
            allowed_tools=allowed_tools,
            error_message=str(exc),
        )
    _debit_llm_usage(loop_ctx, response)
    loop_state.llm_calls += 1
    for assistant_message in list(getattr(response, "assistant_messages", []) or []):
        loop_state.messages.append(assistant_message)
    if not bool(getattr(response, "ok", False)):
        error = getattr(response, "error", None)
        error_message = str(getattr(error, "message", "") or "LLM returned not-ok")
        loop_state.termination_reason = ADAPTIVE_TERM_LLM_ERROR
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} answer-only budget finalization error",
            mode_state="llm_error",
            termination_reason=ADAPTIVE_TERM_LLM_ERROR,
        )
        return AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_LLM_ERROR,
            state=loop_state,
            allowed_tools=allowed_tools,
            error_message=error_message,
        )
    final_text = str(getattr(response, "output_text", "") or "").strip()
    has_finalization_contract = bool(
        getattr(response, STATE_KEY_FINALIZATION_STATUS, None)
    ) or (
        f"<{STATE_KEY_FINALIZATION_STATUS}>" in final_text
        and f"</{STATE_KEY_FINALIZATION_STATUS}>" in final_text
    )
    if _answer_only_finalization_contract_requested(loop_ctx, loop_state) and not (
        has_finalization_contract
    ):
        loop_state.termination_reason = ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING
        return AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
            state=loop_state,
            allowed_tools=allowed_tools,
            error_message=(
                "This act turn required typed finalization_status contract."
            ),
        )
    if final_text and not list(getattr(response, "tool_calls", []) or []):
        loop_state.termination_reason = ADAPTIVE_TERM_FINAL_TEXT
        return AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
            state=loop_state,
            allowed_tools=allowed_tools,
            final_text=final_text,
        )
    loop_state.termination_reason = ADAPTIVE_TERM_BUDGET_EXHAUSTED
    return AdaptiveToolLoopOutcome(
        profile_name=profile.profile_name,
        mode_name=profile.mode_name,
        termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
        state=loop_state,
        allowed_tools=allowed_tools,
        error_message="Answer-only budget finalization did not produce final text.",
    )


def _budget_finalization_has_substantive_user_message(
    messages: list[Message],
) -> bool:
    for message in messages:
        if str(getattr(message, "role", "") or "").strip().lower() != "user":
            continue
        content = str(getattr(message, "content", "") or "").strip()
        if not content:
            continue
        if content.startswith(
            "Continue the active task using the existing conversation and tool results."
        ):
            continue
        return True
    return False


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


def _force_circular_pattern_answer_only_finalization(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    runtime: Any,
    model: str,
    max_output_tokens: int | None,
    metadata: dict[str, Any] | None,
    allowed_tools: frozenset[str],
    public_mode_tag: str,
) -> AdaptiveToolLoopOutcome | None:
    if not _general_profile_name(profile):
        return None
    if not _llm_budget_available_for_answer_only(
        loop_ctx=loop_ctx,
        profile=profile,
        loop_state=loop_state,
    ):
        return None
    tool_results = [
        item
        for item in list(loop_state.scratchpad.get("adaptive.tool_results", []) or [])
        if isinstance(item, dict) and bool(item.get("ok"))
    ]
    if not tool_results:
        return None
    loop_state.scratchpad["circular_pattern_answer_only_finalization_forced"] = True
    loop_state.messages.append(
        Message(
            role="system",
            content=(
                "You have repeated the same tool pattern. Do not call more tools. "
                "This must be the final answer for the current turn. Use the "
                "successful tool results already available and write the best "
                "user-facing final answer now. Do not narrate future steps, do "
                "not say you will continue, and preserve any explicit output "
                "format or headings the user requested. If evidence is partial, "
                "say that briefly and still answer."
            ),
        )
    )
    emit_adaptive_status(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        detail_text=f"{public_mode_tag} answer-only circular-pattern finalization",
        mode_state="circular_pattern_answer_only_finalization",
    )
    try:
        response = runtime.complete(
            messages=loop_state.messages,
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
    _debit_llm_usage(loop_ctx, response)
    loop_state.llm_calls += 1
    for assistant_message in list(getattr(response, "assistant_messages", []) or []):
        loop_state.messages.append(assistant_message)
    if list(getattr(response, "tool_calls", []) or []):
        return None
    final_text = str(getattr(response, "output_text", "") or "").strip()
    if not final_text:
        return None
    loop_state.termination_reason = ADAPTIVE_TERM_FINAL_TEXT
    return AdaptiveToolLoopOutcome(
        profile_name=profile.profile_name,
        mode_name=profile.mode_name,
        termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
        state=loop_state,
        allowed_tools=allowed_tools,
        final_text=final_text,
    )
