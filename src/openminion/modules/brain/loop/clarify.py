from typing import TYPE_CHECKING

from ..constants import (
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_COMMAND_KIND_ASK_USER,
    BRAIN_STATE_ACTIVE,
    BRAIN_STATE_WAITING_USER,
)
from ..diagnostics.transitions import transition
from ..diagnostics.events import CanonicalEventLogger
from ..state import clear_clarify_state, stale_clarify_state_should_clear
from ..schemas import (
    ActionResult,
    AskUserCommand,
    ClarifyContext,
    Decision,
    StepOutput,
    WorkingState,
    new_uuid,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner

from ..execution.delegation import _runner_delegate


def clarify(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    user_input: str | None,
    logger: CanonicalEventLogger,
) -> bool:
    """BCM-02: Explicit clarification phase."""
    state.phase = "CLARIFY"
    config = runner.options.clarify_config

    # 1. Process responses to existing questions
    if stale_clarify_state_should_clear(state, user_input=user_input):
        logger.emit(
            "brain.clarify.stale_state_cleared",
            {
                "reason_code": str(
                    getattr(state, "decision_reason_code", "") or ""
                ).strip()
                or "internal_failure",
            },
            trace_id=state.trace_id,
            status="warning",
        )
        clear_clarify_state(state)
        return False

    if (
        user_input
        and str(user_input).strip()
        and not state.unresolved_clarify_items
        and getattr(state, "pending_llm_clarify_context", None) is not None
    ):
        logger.emit(
            "brain.clarify.context_consumed",
            _llm_clarify_event_payload(
                state,
                reason="next_user_turn",
                user_reply=user_input,
            ),
            trace_id=state.trace_id,
        )

    if user_input and state.unresolved_clarify_items:
        question = state.unresolved_clarify_items[0]
        answer = user_input.strip()
        state.clarify_responses[question.id] = answer
        state.clarify_resume_cursor = question.id
        state.unresolved_clarify_items = state.unresolved_clarify_items[1:]
        state.pending_clarify_items = list(state.unresolved_clarify_items)
        logger.emit(
            "brain.clarify.answered",
            {"count": 1, "question_id": question.id},
            trace_id=state.trace_id,
        )
        if state.unresolved_clarify_items:
            transition(state, "clarify_requested", logger=logger)
            return True
        return False
    elif not user_input and state.unresolved_clarify_items:
        readiness_state = str(
            getattr(getattr(state, "request_readiness", None), "state", "") or ""
        ).strip()
        if (
            bool(getattr(getattr(runner, "options", None), "request_handoff_enabled", False))
            and readiness_state == "needs_user"
        ):
            transition(state, "clarify_requested", logger=logger)
            return True
        if config.handle_unanswered_policy == "error":
            transition(state, "task_failed", logger=logger)
            return True
        if config.handle_unanswered_policy == "assume_default":
            logger.emit(
                "brain.assumptions.used",
                {"count": len(state.unresolved_clarify_items)},
                trace_id=state.trace_id,
            )
            state.unresolved_clarify_items = []
            state.pending_clarify_items = []
            return False
        if config.handle_unanswered_policy == "abort":
            state.unresolved_clarify_items = []
            state.pending_clarify_items = []
            transition(state, "execution_stopped", logger=logger)
            logger.emit(
                "brain.clarify.aborted",
                {
                    "reason_code": "clarify_unanswered_abort",
                    "source": "clarify",
                },
                trace_id=state.trace_id,
            )
            return True
        return True

    # RCL-06/RCL-11: Heuristic conversational clarification block removed.
    if user_input:
        logger.emit(
            "brain.clarify.llm.requested",
            {"strategy": "llm", "action": "defer_to_decide_llm"},
            trace_id=state.trace_id,
        )
    return False


def clear_llm_clarify_context(
    state: WorkingState,
    *,
    logger: CanonicalEventLogger | None = None,
    reason: str,
    user_reply: str | None = None,
) -> None:
    if getattr(state, "pending_llm_clarify_context", None) is None:
        return
    payload = _llm_clarify_event_payload(state, reason=reason, user_reply=user_reply)
    state.pending_llm_clarify_context = None
    if logger is not None:
        logger.emit(
            "brain.clarify.context_cleared",
            payload,
            trace_id=state.trace_id,
        )


def sync_llm_clarify_context_from_decision(
    *,
    state: WorkingState,
    decision: Decision | object | None,
    user_input: str | None,
    logger: CanonicalEventLogger,
) -> None:
    existing = getattr(state, "pending_llm_clarify_context", None)
    has_new_input = bool(str(user_input or "").strip())
    decision_mode = str(getattr(decision, "route", "") or "").strip()
    respond_kind = str(getattr(decision, "respond_kind", "") or "").strip()
    replacement = getattr(decision, "clarify_context", None)
    fallback = _fallback_llm_clarify_context(
        state=state,
        decision=decision,
        user_input=user_input,
    )

    if (
        decision_mode == "respond"
        and respond_kind == "clarify"
        and (replacement is not None or fallback is not None)
    ):
        state.pending_llm_clarify_context = (
            replacement.model_copy(deep=True)
            if replacement is not None
            else fallback.model_copy(deep=True)
        )
        logger.emit(
            "brain.clarify.context_stored",
            _llm_clarify_event_payload(
                state,
                reason=(
                    "decision_sidecar"
                    if replacement is not None
                    else "decision_question_fallback"
                ),
                user_reply=user_input,
            ),
            trace_id=state.trace_id,
        )
        return

    if has_new_input and existing is not None:
        clear_llm_clarify_context(
            state,
            logger=logger,
            reason="consumed_without_refresh",
            user_reply=user_input,
        )


def _fallback_llm_clarify_context(
    *,
    state: WorkingState,
    decision: Decision | object | None,
    user_input: str | None,
) -> ClarifyContext | None:
    decision_mode = str(getattr(decision, "route", "") or "").strip()
    respond_kind = str(getattr(decision, "respond_kind", "") or "").strip()
    if decision_mode != "respond" or respond_kind != "clarify":
        return None

    question = str(getattr(decision, "question", "") or "").strip()
    if not question:
        return None

    existing = getattr(state, "pending_llm_clarify_context", None)
    original_user_input = ""
    inferred_goal = ""
    known_context: dict[str, str] = {}
    if existing is not None:
        original_user_input = str(
            getattr(existing, "original_user_input", "") or ""
        ).strip()
        inferred_goal = str(getattr(existing, "inferred_goal", "") or "").strip()
        known_context = dict(getattr(existing, "known_context", {}) or {})

    if not original_user_input:
        original_user_input = str(user_input or "").strip()
    if not original_user_input:
        return None

    return ClarifyContext(
        original_user_input=original_user_input,
        inferred_goal=inferred_goal,
        known_context=known_context,
        unresolved_question=question,
        clarify_question=question,
    )


def _llm_clarify_event_payload(
    state: WorkingState,
    *,
    reason: str,
    user_reply: str | None = None,
) -> dict[str, object]:
    pending = getattr(state, "pending_llm_clarify_context", None)
    known_context = getattr(pending, "known_context", {}) if pending is not None else {}
    return {
        "reason": str(reason or "").strip() or "unknown",
        "has_pending_context": pending is not None,
        "original_user_input": str(
            getattr(pending, "original_user_input", "") or ""
        ).strip(),
        "inferred_goal": str(getattr(pending, "inferred_goal", "") or "").strip(),
        "known_context_keys": sorted(
            str(key).strip()
            for key in dict(known_context or {}).keys()
            if str(key).strip()
        ),
        "clarify_question": str(getattr(pending, "clarify_question", "") or "").strip(),
        "unresolved_question": str(
            getattr(pending, "unresolved_question", "") or ""
        ).strip(),
        "user_reply": str(user_reply or "").strip(),
    }


def is_response_to_clarification(
    *, state: WorkingState, user_input: str | None
) -> bool:
    if not user_input or not state.unresolved_clarify_items:
        return False
    if stale_clarify_state_should_clear(state, user_input=user_input):
        return False
    return bool(user_input.strip())


def process_clarification_response(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    user_input: str,
    logger: CanonicalEventLogger,
    clarify_request,
):
    if not state.unresolved_clarify_items and clarify_request.questions:
        state.unresolved_clarify_items = list(clarify_request.questions)
        state.pending_clarify_items = list(state.unresolved_clarify_items)

    logger.emit(
        "brain.clarify.answered",
        {
            "session_id": clarify_request.session_id,
            "trace_id": clarify_request.trace_id,
            "answer_provided": bool(str(user_input or "").strip()),
        },
        trace_id=state.trace_id,
    )

    if state.unresolved_clarify_items:
        question = state.unresolved_clarify_items[0]
        state.clarify_responses[question.id] = user_input.strip()
        state.clarify_resume_cursor = question.id
        state.unresolved_clarify_items = state.unresolved_clarify_items[1:]
        state.pending_clarify_items = list(state.unresolved_clarify_items)

    return StepOutput(
        session_id=state.session_id,
        status=BRAIN_STATE_ACTIVE,
        message="Clarification response processed",
        working_state=state,
    )


def enter_clarify_mode(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    clarify_request,
    logger: CanonicalEventLogger,
):
    logger.emit(
        "brain.clarify.requested",
        {
            "session_id": clarify_request.session_id,
            "trace_id": clarify_request.trace_id,
            "questions_count": len(clarify_request.questions),
            "blocking_questions": sum(
                1 for q in clarify_request.questions if q.is_blocking
            ),
        },
        trace_id=state.trace_id,
    )

    if clarify_request.questions:
        questions_text = [f"- {q.question}" for q in clarify_request.questions[:2]]
        question_text = (
            "Please clarify:\n" + "\n".join(questions_text) + "\n\nYour response:"
        )

        command = AskUserCommand(
            kind=BRAIN_COMMAND_KIND_ASK_USER,
            title="Clarification needed",
            question=question_text,
            success_criteria={"received_clarification": True},
        )

        result = ActionResult(
            command_id=command.command_id,
            status=BRAIN_ACTION_STATUS_NEEDS_USER,
            summary=question_text,
        )

        state.unresolved_clarify_items = list(clarify_request.questions)
        state.pending_clarify_items = list(state.unresolved_clarify_items)
        if clarify_request.questions:
            state.clarify_resume_cursor = clarify_request.questions[0].id

        return _runner_delegate(
            "_respond_with_meta",
            runner,
            state=state,
            logger=logger,
            message=question_text,
            status=BRAIN_STATE_WAITING_USER,
            action_result=result,
        )

    default_msg = "Additional information needed before proceeding."
    result = ActionResult(
        command_id=new_uuid(),
        status=BRAIN_ACTION_STATUS_NEEDS_USER,
        summary=default_msg,
    )

    return _runner_delegate(
        "_respond_with_meta",
        runner,
        state=state,
        logger=logger,
        message=default_msg,
        status=BRAIN_STATE_WAITING_USER,
        action_result=result,
    )
