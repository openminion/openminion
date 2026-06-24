from __future__ import annotations

from ...constants import (
    BRAIN_STATE_ACTIVE,
    BRAIN_STATE_DONE,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_FAILED,
    BRAIN_STATE_STOPPED,
    BRAIN_STATE_WAITING_USER,
)
from ...diagnostics.transitions import set_status_unchecked
from ...execution.continuation import (
    clear_continuation_reply,
    continuation_choice_message,
    has_pending_continuation_reply,
    parse_continuation_choice,
)
from ...execution.feasibility import (
    apply_viable_subset,
    build_resume_decision,
    clear_feasibility_state,
    extract_feasibility_report,
    feasibility_choice_message,
    has_pending_feasibility_choice,
    parse_feasibility_choice,
    serialize_feasibility_state,
)
from ...execution.lifecycle import set_phase
from ...loop.clarify import clear_llm_clarify_context
from ...schemas import iso_now, new_uuid
from ...tools.parser import normalize_tool_name_for_brain
from .context import TickRunContext, _runner_delegate


def handle_pending_replay(
    *,
    runner,
    state,
    logger,
    tick_ctx: TickRunContext,
):
    if (
        tick_ctx.user_input is not None
        and tick_ctx.user_input.strip()
        and has_pending_feasibility_choice(state)
    ):
        if state.trace_id is None:
            state.trace_id = tick_ctx.trace_id or new_uuid()
        runner.session_api.append_turn(
            tick_ctx.session_id, "user", tick_ctx.user_input, meta={"ts": iso_now()}
        )
        tick_ctx.skip_initial_append = True
        tick_ctx.skip_initial_interpret = True
        choice = parse_feasibility_choice(tick_ctx.user_input)
        report = extract_feasibility_report(
            getattr(state, "decision_feasibility_state", {})
        )
        if choice == "unclear":
            return _runner_delegate(
                "_respond_with_meta",
                runner,
                state=state,
                logger=logger,
                message=feasibility_choice_message(report),
                status=BRAIN_STATE_WAITING_USER,
            )
        if choice == "cancel":
            clear_feasibility_state(state)
            state.plan = None
            state.cursor = 0
            return _runner_delegate(
                "_respond_with_meta",
                runner,
                state=state,
                logger=logger,
                message="Understood. I won't continue that blocked plan.",
                status=BRAIN_STATE_DONE,
            )
        if choice == "retry":
            clear_feasibility_state(state)
            state.plan = None
            state.cursor = 0
            tick_ctx.user_input = str(state.goal or "").strip() or tick_ctx.user_input
        else:
            if report is None or not apply_viable_subset(state, report):
                return _runner_delegate(
                    "_respond_with_meta",
                    runner,
                    state=state,
                    logger=logger,
                    message=feasibility_choice_message(report),
                    status=BRAIN_STATE_WAITING_USER,
                )
            state.decision_feasibility_state = serialize_feasibility_state(
                report,
                awaiting_user_choice=False,
                reviewed=True,
                approved_subset=True,
            )
            state.decision_feasibility_report = report
            replay_decision = build_resume_decision(state)
            if replay_decision is None:
                return _runner_delegate(
                    "_respond_with_meta",
                    runner,
                    state=state,
                    logger=logger,
                    message="I no longer have a viable plan to continue.",
                    status=BRAIN_STATE_WAITING_USER,
                )
            tick_ctx.decision = replay_decision
            tick_ctx.skip_decide = True
            tick_ctx.consume_user_input_for_command = True
            set_status_unchecked(state, BRAIN_STATE_ACTIVE, reason="new_turn")
            tick_ctx.user_input = None

    if (
        tick_ctx.user_input is not None
        and tick_ctx.user_input.strip()
        and has_pending_continuation_reply(state)
    ):
        if state.trace_id is None:
            state.trace_id = tick_ctx.trace_id or new_uuid()
        runner.session_api.append_turn(
            tick_ctx.session_id, "user", tick_ctx.user_input, meta={"ts": iso_now()}
        )
        tick_ctx.skip_initial_append = True
        tick_ctx.skip_initial_interpret = True
        choice = parse_continuation_choice(tick_ctx.user_input)
        if choice == "unclear":
            return _runner_delegate(
                "_respond_with_meta",
                runner,
                state=state,
                logger=logger,
                message=continuation_choice_message(
                    getattr(state, "continuation_guard_reason", "")
                ),
                status=BRAIN_STATE_WAITING_USER,
            )
        if choice == "cancel":
            clear_continuation_reply(state, clear_guard=True)
            state.plan = None
            state.cursor = 0
            return _runner_delegate(
                "_respond_with_meta",
                runner,
                state=state,
                logger=logger,
                message="Understood. I won't continue that task.",
                status=BRAIN_STATE_DONE,
            )
        if choice == "retry":
            clear_continuation_reply(state, clear_guard=True)
            state.plan = None
            state.cursor = 0
            tick_ctx.user_input = str(state.goal or "").strip() or tick_ctx.user_input
        else:
            clear_continuation_reply(state, clear_guard=False)
            state_mode = str(getattr(state, "mode", "") or "").strip().lower()
            if hasattr(getattr(state, "mode", None), "value"):
                state_mode = str(getattr(state.mode, "value", "") or "").strip().lower()
            if state_mode == "guided" and state.plan is not None:
                tick_ctx.masked_resume_cursor = int(getattr(state, "cursor", 0) or 0)
                replay_decision = build_resume_decision(state)
                if replay_decision is None:
                    return _runner_delegate(
                        "_respond_with_meta",
                        runner,
                        state=state,
                        logger=logger,
                        message="I no longer have a viable plan to continue.",
                        status=BRAIN_STATE_WAITING_USER,
                    )
                tick_ctx.decision = replay_decision
                tick_ctx.skip_decide = True
                tick_ctx.consume_user_input_for_command = True
            set_status_unchecked(state, BRAIN_STATE_ACTIVE, reason="new_turn")
            tick_ctx.user_input = None

    if (
        tick_ctx.user_input is not None
        and tick_ctx.user_input.strip()
        and state.plan is not None
        and state.pending_confirmation_command is None
        and parse_continuation_choice(tick_ctx.user_input) == "continue"
        and not has_pending_feasibility_choice(state)
        and not has_pending_continuation_reply(state)
    ):
        if state.trace_id is None:
            state.trace_id = tick_ctx.trace_id or new_uuid()
        runner.session_api.append_turn(
            tick_ctx.session_id, "user", tick_ctx.user_input, meta={"ts": iso_now()}
        )
        tick_ctx.skip_initial_append = True
        tick_ctx.skip_initial_interpret = True
        tick_ctx.masked_resume_cursor = int(getattr(state, "cursor", 0) or 0)
        replay_decision = build_resume_decision(state)
        if replay_decision is None:
            return _runner_delegate(
                "_respond_with_meta",
                runner,
                state=state,
                logger=logger,
                message="I no longer have a viable plan to continue.",
                status=BRAIN_STATE_WAITING_USER,
            )
        tick_ctx.decision = replay_decision
        tick_ctx.skip_decide = True
        tick_ctx.consume_user_input_for_command = True
        tick_ctx.mask_pending_confirmation_in_output = True
        set_status_unchecked(state, BRAIN_STATE_ACTIVE, reason="new_turn")
        tick_ctx.user_input = None

    return None


def _is_explicit_tool_command(text: str) -> bool:
    parts = str(text or "").strip().split(maxsplit=2)
    if len(parts) < 2 or parts[0].lower() != "tool":
        return False
    return bool(normalize_tool_name_for_brain(parts[1]) or parts[1].strip())


def process_user_input(*, runner, state, logger, tick_ctx: TickRunContext):
    user_input = tick_ctx.user_input
    if user_input is not None and user_input.strip():
        previous_status = str(getattr(state, "status", "") or "").strip().lower()
        if (
            getattr(state, "pending_llm_clarify_context", None) is not None
            and not list(getattr(state, "unresolved_clarify_items", []) or [])
            and previous_status != BRAIN_STATE_WAITING_USER
        ):
            clear_llm_clarify_context(
                state,
                logger=logger,
                reason="fresh_turn_without_waiting_user",
                user_reply=user_input,
            )
        if not tick_ctx.skip_initial_append and state.trace_id is None:
            state.trace_id = tick_ctx.trace_id or new_uuid()
        if not tick_ctx.skip_initial_append:
            runner.session_api.append_turn(
                tick_ctx.session_id,
                "user",
                str(tick_ctx.original_user_input or user_input),
                meta={"ts": iso_now()},
            )
            raw_user_message = str(tick_ctx.original_user_input or user_input)
            if not _is_explicit_tool_command(raw_user_message):
                try:
                    from ...execution import extract_user_message_candidates

                    extract_user_message_candidates(
                        runner,
                        state=state,
                        user_message=raw_user_message,
                        logger=logger,
                    )
                except Exception:  # noqa: BLE001
                    pass
        if not tick_ctx.skip_initial_interpret:
            _runner_delegate(
                "_interpret",
                runner,
                state=state,
                user_input=user_input,
                logger=logger,
                reset_policy_name=tick_ctx.forced_reset_policy_name,
            )
        set_status_unchecked(state, BRAIN_STATE_ACTIVE, reason="new_turn")

        if _runner_delegate(
            "_clarify", runner, state=state, user_input=user_input, logger=logger
        ):
            if state.status == BRAIN_STATE_STOPPED:
                return _runner_delegate(
                    "_respond_with_meta",
                    runner,
                    state=state,
                    logger=logger,
                    message=(
                        "Clarification aborted because required inputs were not provided."
                    ),
                    status=BRAIN_STATE_STOPPED,
                )
            if state.status == BRAIN_STATE_FAILED:
                return _runner_delegate(
                    "_respond_with_meta",
                    runner,
                    state=state,
                    logger=logger,
                    message="Clarification failed.",
                    status=BRAIN_STATE_ERROR,
                )
            questions = state.unresolved_clarify_items
            if questions:
                msg = "Clarification is required before proceeding:\n" + "\n".join(
                    f"- {q.question}" for q in questions
                )
            else:
                msg = "Clarification is required before proceeding."
            res = _runner_delegate(
                "_respond_with_meta",
                runner,
                state=state,
                logger=logger,
                message=msg,
                status=BRAIN_STATE_WAITING_USER,
            )
            set_phase(runner, state=state, phase="CLARIFY")
            return res

        meta_after_interpret = _runner_delegate(
            "_evaluate_meta",
            runner,
            state=state,
            logger=logger,
            hook="after_interpret",
            user_input=user_input,
        )
        if meta_after_interpret is not None:
            _runner_delegate(
                "_apply_meta_directive",
                runner,
                state=state,
                directive=meta_after_interpret.directive,
                logger=logger,
                hook="after_interpret",
                meta_state=meta_after_interpret.meta_state.value,
            )
            override = _runner_delegate(
                "_meta_override_response",
                runner,
                state=state,
                logger=logger,
                directive=meta_after_interpret.directive,
                fallback_message="I need clarification before proceeding.",
            )
            if override is not None:
                return override
    elif state.trace_id is None:
        state.trace_id = tick_ctx.trace_id or new_uuid()

    if user_input is None and state.unresolved_clarify_items:
        if _runner_delegate(
            "_clarify", runner, state=state, user_input=None, logger=logger
        ):
            if state.status == BRAIN_STATE_STOPPED:
                return _runner_delegate(
                    "_respond_with_meta",
                    runner,
                    state=state,
                    logger=logger,
                    message=(
                        "Clarification aborted because required inputs were not provided."
                    ),
                    status=BRAIN_STATE_STOPPED,
                )
            if state.status == BRAIN_STATE_FAILED:
                return _runner_delegate(
                    "_respond_with_meta",
                    runner,
                    state=state,
                    logger=logger,
                    message="Clarification failed.",
                    status=BRAIN_STATE_ERROR,
                )
            return _runner_delegate(
                "_respond_with_meta",
                runner,
                state=state,
                logger=logger,
                message="Waiting for user responses to clarification questions.",
                status=BRAIN_STATE_WAITING_USER,
            )
    return None
