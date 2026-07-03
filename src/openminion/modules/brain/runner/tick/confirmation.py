from __future__ import annotations

from ...constants import (
    BRAIN_STATE_ACTIVE,
    BRAIN_STATE_DONE,
    BRAIN_STATE_WAITING_USER,
)
from ...loop.tools.budget_extension import (
    approve_pending_extension,
    clear_pending_extension,
    get_pending_extension,
    is_pending_extension_expired,
)
from ...loop.tools.confirmation import (
    apply_session_confirmation_grant,
    extract_confirmation_replay_queue,
    is_session_confirmation_response,
    strip_confirmation_replay_queue,
)
from ...loop.tools.direct_reasons import is_explicit_direct_tool_reason
from ...diagnostics.transitions import set_status_unchecked, transition
from ...execution.continuation import continuation_choice_message
from ...execution import (
    apply_post_action_judgment,
    clear_post_action_user_message,
    final_close_message,
    transition_to_replan_state,
)
from ...schemas import (
    Plan,
    new_uuid,
    refresh_command_identity,
)
from .context import (
    TickRunContext,
    _apply_pending_confirmation_metadata_for_replay,
    _clear_pending_confirmation_metadata,
    _grant_once_from_confirmation,
    _parse_confirmation_response,
    _runner_delegate,
)


def _is_adaptive_budget_extension(command) -> bool:
    inputs = getattr(command, "inputs", None)
    return isinstance(inputs, dict) and bool(inputs.get("adaptive_budget_extension"))


def _process_adaptive_budget_extension_reply(
    *,
    runner,
    state,
    logger,
    tick_ctx: TickRunContext,
    confirmation_reply: str,
):
    command = state.pending_confirmation_command
    pending = get_pending_extension(state=state)
    if pending is None:
        state.pending_confirmation_command = None
        _clear_pending_confirmation_metadata(state)
        clear_post_action_user_message(state=state)
        tick_ctx.consume_user_input_for_command = True
        return _runner_delegate(
            "_respond_with_meta",
            runner,
            state=state,
            logger=logger,
            message=(
                "That budget-extension request is no longer active. "
                "Please ask me to continue if you still want me to proceed."
            ),
            status=BRAIN_STATE_WAITING_USER,
        )

    cap_at_pause = int(pending.get("cap_at_pause", 0) or 0)
    extend_by = int(pending.get("extend_by", 0) or 0)
    if is_pending_extension_expired(pending):
        clear_pending_extension(state=state)
        state.pending_confirmation_command = None
        _clear_pending_confirmation_metadata(state)
        clear_post_action_user_message(state=state)
        tick_ctx.consume_user_input_for_command = True
        logger.emit(
            "budget.user_timeout",
            {
                "cap": cap_at_pause,
                "extend_by": extend_by,
                "reason": "user_timeout",
            },
            trace_id=state.trace_id,
        )
        return _runner_delegate(
            "_respond_with_meta",
            runner,
            state=state,
            logger=logger,
            message=(
                "That budget-extension request expired. "
                "Please ask me to continue if you still want me to proceed."
            ),
            status=BRAIN_STATE_WAITING_USER,
        )

    if confirmation_reply == "affirm":
        approved = approve_pending_extension(state=state)
        if approved is None:
            approved = {}
        state.pending_confirmation_command = None
        _clear_pending_confirmation_metadata(state)
        clear_post_action_user_message(state=state)
        tick_ctx.consume_user_input_for_command = True
        tick_ctx.user_input = None
        tick_ctx.skip_decide = False
        target_cap = int(approved.get("target_cap", 0) or 0)
        session_extensions_used = int(approved.get("session_extensions_used", 0) or 0)
        logger.emit(
            "budget.extended",
            {
                "by": max(0, target_cap - cap_at_pause),
                "total": target_cap,
                "extensions_used": 1,
                "session_extensions_used": session_extensions_used,
                "trigger": "user",
            },
            trace_id=state.trace_id,
        )
        return None

    if confirmation_reply == "deny":
        clear_pending_extension(state=state)
        state.pending_confirmation_command = None
        _clear_pending_confirmation_metadata(state)
        clear_post_action_user_message(state=state)
        tick_ctx.consume_user_input_for_command = True
        logger.emit(
            "budget.user_declined",
            {
                "cap": cap_at_pause,
                "extend_by": extend_by,
                "reason": "user_declined",
            },
            trace_id=state.trace_id,
        )
        return _runner_delegate(
            "_respond_with_meta",
            runner,
            state=state,
            logger=logger,
            message=(
                "Understood. I won't extend the iteration budget for this turn. "
                "You can narrow the request or ask me to continue with a smaller scope."
            ),
            status=BRAIN_STATE_WAITING_USER,
        )

    question = str(getattr(command, "question", "") or "").strip()
    return _runner_delegate(
        "_respond_with_meta",
        runner,
        state=state,
        logger=logger,
        message=question or "Continue for more iterations? Reply yes or no.",
        status=BRAIN_STATE_WAITING_USER,
    )


def _transition_to_replan_after_deny(*, runner, state, reason: str) -> bool:
    retained_limit = int(
        getattr(
            runner.options,
            "adaptive_replan_retained_step_outputs",
            0,
        )
        or 0
    )
    transitioned, _retained_count = transition_to_replan_state(
        state=state,
        max_replans=int(getattr(runner.options, "max_replans", 0) or 0),
        retained_step_outputs=retained_limit,
    )
    del reason
    return transitioned


def process(*, runner, state, logger, tick_ctx: TickRunContext):
    if (
        state.pending_confirmation_command is not None
        and tick_ctx.user_input is not None
    ):
        confirmation_text = str(tick_ctx.user_input or "")
        confirmation_reply = _parse_confirmation_response(runner, confirmation_text)
        session_grant = is_session_confirmation_response(confirmation_text)
        if _is_adaptive_budget_extension(state.pending_confirmation_command):
            budget_result = _process_adaptive_budget_extension_reply(
                runner=runner,
                state=state,
                logger=logger,
                tick_ctx=tick_ctx,
                confirmation_reply=confirmation_reply,
            )
            if budget_result is not None:
                return budget_result
            if confirmation_reply == "affirm":
                return None
            session_grant = False
        if confirmation_reply == "affirm" or session_grant:
            confirmed = state.pending_confirmation_command.model_copy(deep=True)
            state.pending_confirmation_command = None
            prior_reason_code = str(
                getattr(state, "decision_reason_code", "") or ""
            ).strip()
            explicit_direct_tool_replay = is_explicit_direct_tool_reason(
                prior_reason_code
            )
            queued_replay_commands = extract_confirmation_replay_queue(confirmed)
            replay_commands = [strip_confirmation_replay_queue(confirmed)] + [
                strip_confirmation_replay_queue(command)
                for command in queued_replay_commands
            ]
            replay_plan_commands = []
            for replay_command in replay_commands:
                if session_grant:
                    apply_session_confirmation_grant(state, replay_command)
                replay_inputs = (
                    dict(replay_command.inputs)
                    if isinstance(getattr(replay_command, "inputs", None), dict)
                    else {}
                )
                grant_id, grant_supported = _grant_once_from_confirmation(
                    runner,
                    state=state,
                    command=replay_command,
                    logger=logger,
                )
                if grant_supported and not grant_id:
                    state.pending_confirmation_command = confirmed
                    return _runner_delegate(
                        "_respond_with_meta",
                        runner,
                        state=state,
                        logger=logger,
                        message=(
                            "I could not apply your confirmation yet. "
                            "Please try again in a moment."
                        ),
                        status=BRAIN_STATE_WAITING_USER,
                    )
                replay_inputs["confirmation_source"] = "policy_replay"
                if grant_id:
                    replay_inputs["confirmation_grant_id"] = grant_id
                elif not grant_supported:
                    replay_inputs["confirmation_grant_id"] = (
                        f"local-confirmation-{new_uuid()}"
                    )
                replay_command.inputs = replay_inputs
                replay_plan_commands.append(refresh_command_identity(replay_command))
            if state.plan is not None and 0 <= state.cursor < len(state.plan.steps):
                state.plan.steps = (
                    list(state.plan.steps[: state.cursor])
                    + [
                        command.model_copy(deep=True)
                        for command in replay_plan_commands
                    ]
                    + list(state.plan.steps[state.cursor + 1 :])
                )
            else:
                state.plan = Plan(
                    objective=state.goal or "confirmation_replay",
                    steps=[
                        command.model_copy(deep=True)
                        for command in replay_plan_commands
                    ],
                    stop_conditions=(
                        ["single command completed"]
                        if explicit_direct_tool_replay
                        else []
                    ),
                    assumptions=[],
                    risk_summary="confirmation_replay",
                    success_criteria=(
                        {"status": "success"} if explicit_direct_tool_replay else {}
                    ),
                )
                state.cursor = 0
            _apply_pending_confirmation_metadata_for_replay(state)
            _clear_pending_confirmation_metadata(state)
            tick_ctx.decision = None
            from ...schemas import ActDecision

            replay_reason_code = "confirmation_replay"
            if is_explicit_direct_tool_reason(prior_reason_code):
                replay_reason_code = prior_reason_code
            replay_decision = ActDecision(
                confidence=1.0,
                reason_code=replay_reason_code,
                sub_intents=list(getattr(state, "decision_sub_intents", []) or []),
                rationale=str(getattr(state, "decision_rationale", "") or "").strip(),
            )
            replay_decision._seeded_commands = [
                command.model_copy(deep=True) for command in replay_plan_commands
            ]
            tick_ctx.decision = replay_decision
            tick_ctx.skip_decide = True
            tick_ctx.consume_user_input_for_command = True
            tick_ctx.user_input = None
            tick_ctx.original_user_input = None
            tick_ctx.has_new_user_input = False
            if state.status == BRAIN_STATE_WAITING_USER:
                transition(state, "user_input_received", logger=logger)
            elif state.status != BRAIN_STATE_ACTIVE:
                set_status_unchecked(
                    state,
                    BRAIN_STATE_ACTIVE,
                    reason="confirmation_replay",
                )
            logger.emit(
                "brain.confirm_replay",
                {
                    "command_id": replay_plan_commands[0].command_id,
                    "kind": replay_plan_commands[0].kind,
                    "replay_count": len(replay_plan_commands),
                },
                trace_id=state.trace_id,
            )
        elif confirmation_reply == "deny":
            denied_command = None
            denied_cursor = int(getattr(state, "cursor", 0) or 0)
            denied_total_steps = 1
            if state.plan is not None and 0 <= state.cursor < len(state.plan.steps):
                denied_command = state.plan.steps[state.cursor]
                denied_total_steps = len(state.plan.steps)
            elif state.pending_confirmation_command is not None:
                denied_command = state.pending_confirmation_command
            state.pending_confirmation_command = None
            _clear_pending_confirmation_metadata(state)
            tick_ctx.consume_user_input_for_command = True
            clear_post_action_user_message(state=state)
            logger.emit(
                "plan.step.denied",
                {
                    "cursor": denied_cursor,
                    "reason": "user_denied_confirmation",
                    "command_id": str(
                        getattr(denied_command, "command_id", "") or ""
                    ).strip(),
                },
                trace_id=state.trace_id,
            )

            judgment = _runner_delegate(
                "_evaluate_post_action_judgment",
                runner,
                state=state,
                logger=logger,
                fact_kind="confirmation_denied",
                action_result=None,
                current_command=denied_command,
                current_step_index=denied_cursor,
                total_steps=denied_total_steps,
                runtime_facts={
                    "confirmation_reply": "deny",
                    "confirmation_denied": True,
                },
            )
            if judgment is None:
                return _runner_delegate(
                    "_respond_with_meta",
                    runner,
                    state=state,
                    logger=logger,
                    message=(
                        "Post-confirmation judgment is unavailable after the "
                        "denied confirmation."
                    ),
                    status=BRAIN_STATE_WAITING_USER,
                )
            apply_post_action_judgment(
                state=state,
                judgment=judgment,
                step_key=str(getattr(denied_command, "command_id", "") or "").strip(),
                total_steps=denied_total_steps,
                max_retries_per_step=int(
                    getattr(runner.options, "max_retries_per_step", 0) or 0
                ),
                transition_to_replan=lambda *, reason: _transition_to_replan_after_deny(
                    runner=runner,
                    state=state,
                    reason=reason,
                ),
            )
            tick_ctx.user_input = None
            if state.status == BRAIN_STATE_DONE:
                judgment_result = _runner_delegate(
                    "_evaluate_turn_closure",
                    runner,
                    state=state,
                    action_result=None,
                    logger=logger,
                    completion_reason="plan_completed_after_denied_step",
                )
                disposition = _runner_delegate(
                    "_apply_closure_judgment",
                    runner,
                    state=state,
                    judgment=judgment_result,
                )
                if disposition == "close":
                    return _runner_delegate(
                        "_respond_with_meta",
                        runner,
                        state=state,
                        logger=logger,
                        message=final_close_message(
                            state=state,
                            judgment=judgment_result,
                            action_result=None,
                            fallback_message=(
                                "Understood. I skipped the denied final step and "
                                "there is no remaining plan work."
                            ),
                        ),
                        status=BRAIN_STATE_DONE,
                    )
                if disposition == "continue":
                    transition(state, "confirmation_denied", logger=logger)
                    return _runner_delegate(
                        "_respond_with_meta",
                        runner,
                        state=state,
                        logger=logger,
                        message=continuation_choice_message(judgment_result.reason),
                        status=BRAIN_STATE_WAITING_USER,
                    )
                if disposition == "replan":
                    tick_ctx.user_input = None
                else:
                    return _runner_delegate(
                        "_respond_with_meta",
                        runner,
                        state=state,
                        logger=logger,
                        message=(
                            judgment_result.reason
                            or "I need guidance before closing this task."
                        ),
                        status=BRAIN_STATE_WAITING_USER,
                    )
            elif state.status in {BRAIN_STATE_WAITING_USER, "stopped"}:
                return _runner_delegate(
                    "_respond_with_meta",
                    runner,
                    state=state,
                    logger=logger,
                    message=(
                        str(
                            getattr(state, "post_action_user_message", "") or ""
                        ).strip()
                        or "Understood. I stopped after that denied confirmation."
                    ),
                    status=state.status,
                )
        else:
            state.pending_confirmation_command = None
            _clear_pending_confirmation_metadata(state)

    if state.pending_confirmation_command is None and not tick_ctx.skip_decide:
        _clear_pending_confirmation_metadata(state)
    return None
