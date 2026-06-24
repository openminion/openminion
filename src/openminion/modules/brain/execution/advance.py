from __future__ import annotations

from ..constants import (
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_FAILURES,
    BRAIN_FRESHNESS_POLICY_CONSTRAINT,
)
from ..diagnostics.transitions import transition
from ..diagnostics.events import CanonicalEventLogger
from ..execution.intent_state import update_intent_execution_states
from ..schemas import ActionResult, PostActionJudgment, WorkingState
from .post_action import (
    apply_post_action_judgment,
    clear_post_action_user_message,
    evaluate_post_action_judgment,
)
from .delegation import _runner_delegate


class _NullLogger:
    def emit(self, *args, **kwargs) -> None:
        del args, kwargs


def _build_replan_transition(runner, *, state: WorkingState, logger):
    def _transition_to_replan(*, reason: str) -> bool:
        retained_limit = int(
            getattr(runner.options, "adaptive_replan_retained_step_outputs", 0) or 0
        )
        transitioned, retained_count = transition_to_replan_state(
            state=state,
            max_replans=int(getattr(runner.options, "max_replans", 0) or 0),
            retained_step_outputs=retained_limit,
        )
        if not transitioned:
            return False
        if logger is not None:
            logger.emit(
                "brain.plan_revision.context_retained",
                {
                    "reason": reason,
                    "retained_step_output_count": retained_count,
                    "retained_step_output_limit": int(
                        getattr(
                            runner.options,
                            "adaptive_replan_retained_step_outputs",
                            0,
                        )
                        or 0
                    ),
                },
                trace_id=state.trace_id,
                status="info",
            )
        return True

    return _transition_to_replan


def _current_plan_command(state: WorkingState):
    current_plan = getattr(state, "plan", None)
    current_command = (
        current_plan.steps[state.cursor]
        if current_plan is not None and state.cursor < len(current_plan.steps)
        else None
    )
    return current_plan, current_command


def _handle_missing_plan(
    *,
    state: WorkingState,
    logger: CanonicalEventLogger | None,
) -> None:
    transition(state, "step_failed_waiting", logger=logger)
    state.post_action_user_message = (
        "I no longer have an active plan for that result, so I need guidance."
    )


def _handle_consecutive_failure_limit(
    runner,
    *,
    state: WorkingState,
    action_result: ActionResult,
    logger: CanonicalEventLogger | None,
) -> bool:
    if action_result.status in BRAIN_ACTION_STATUS_FAILURES:
        state.consecutive_step_failures += 1
    else:
        state.consecutive_step_failures = 0

    if state.consecutive_step_failures < runner.options.plan_consecutive_failure_limit:
        return False

    if logger is not None:
        logger.emit(
            "brain.loop_safety.consecutive_failures",
            {
                "count": state.consecutive_step_failures,
                "limit": runner.options.plan_consecutive_failure_limit,
                "status": action_result.status,
            },
            trace_id=state.trace_id,
        )
    transition(state, "retries_exhausted", logger=logger)
    state.post_action_user_message = (
        "Too many consecutive step failures occurred, so I paused for guidance."
    )
    return True


def _handle_time_sensitive_failure(
    runner,
    *,
    state: WorkingState,
    action_result: ActionResult,
    current_command,
    logger: CanonicalEventLogger | None,
) -> bool:
    if action_result.status != BRAIN_ACTION_STATUS_FAILED:
        return False
    if not current_command or not _runner_delegate(
        "_is_time_sensitive_tool_command", runner, current_command
    ):
        return False

    retries_exhausted = (
        state.retries_for_step.get(current_command.command_id, 0)
        >= runner.options.max_retries_per_step
    )
    replans_exhausted = state.replans_used >= runner.options.max_replans
    if not retries_exhausted and not replans_exhausted:
        return False

    if logger is not None:
        logger.emit(
            "tool.time_sensitive_failed",
            {
                "command_id": current_command.command_id,
                "tool_name": getattr(current_command, "tool_name", "unknown"),
                "query": getattr(state, "last_user_input", "")
                or getattr(state, "goal", ""),
                "reason": "retries_exhausted"
                if retries_exhausted
                else "replans_exhausted",
                "error_code": action_result.error.code
                if action_result.error
                else "UNKNOWN",
            },
            trace_id=state.trace_id,
            status="error",
            error={
                "code": "TIME_SENSITIVE_TOOL_FAILED",
                "message": "Time-sensitive tool failed after exhausting retries",
            },
        )
    transition(state, "retries_exhausted", logger=logger)
    if BRAIN_FRESHNESS_POLICY_CONSTRAINT not in state.constraints:
        state.constraints.append(BRAIN_FRESHNESS_POLICY_CONSTRAINT)
    state.post_action_user_message = (
        action_result.summary
        or "A time-sensitive step failed after exhausting recovery budget."
    )
    return True


def _policy_replay_success_judgment(
    *,
    current_command,
    action_result: ActionResult,
) -> PostActionJudgment | None:
    if action_result.status != "success" or current_command is None:
        return None
    inputs = getattr(current_command, "inputs", None)
    if not isinstance(inputs, dict):
        return None
    if str(inputs.get("confirmation_source", "") or "").strip() != "policy_replay":
        return None
    return PostActionJudgment(
        outcome="advance",
        reason=(
            "Confirmed policy-replay command succeeded; continue the approved "
            "batch without invoking semantic post-action judgment mid-flight."
        ),
    )


def transition_to_replan_state(
    *,
    state: WorkingState,
    max_replans: int,
    retained_step_outputs: int,
) -> tuple[bool, int]:
    if state.replans_used >= max_replans:
        transition(state, "retries_exhausted")
        return False, 0

    retained = []
    if retained_step_outputs > 0:
        retained = list(getattr(state, "step_outputs", []) or [])[
            -retained_step_outputs:
        ]

    state.replans_used += 1
    state.step_outputs = retained
    state.plan = None
    state.cursor = 0
    transition(state, "step_advanced")
    state.consecutive_step_failures = 0
    state.last_checkpoint_cursor = -1
    return True, len(retained)


def advance_after_action(
    runner,
    *,
    state: WorkingState,
    action_result: ActionResult,
    force_replan: bool = False,
    logger: CanonicalEventLogger | None = None,
) -> None:
    clear_post_action_user_message(state=state)
    _transition_to_replan = _build_replan_transition(runner, state=state, logger=logger)

    current_plan, current_command = _current_plan_command(state)
    step_key = current_command.command_id if current_command is not None else "unknown"
    current_step_index = state.cursor

    if current_command is not None:
        update_intent_execution_states(
            runner,
            state=state,
            command=current_command,
            action_result=action_result,
            current_step_index=current_step_index,
        )

    if force_replan:
        _transition_to_replan(reason="adaptive_or_meta_replan")
        return

    if current_plan is None:
        _handle_missing_plan(state=state, logger=logger)
        return

    if _handle_consecutive_failure_limit(
        runner,
        state=state,
        action_result=action_result,
        logger=logger,
    ):
        return

    if _handle_time_sensitive_failure(
        runner,
        state=state,
        action_result=action_result,
        current_command=current_command,
        logger=logger,
    ):
        return
    judgment = _policy_replay_success_judgment(
        current_command=current_command,
        action_result=action_result,
    )
    if judgment is None:
        judgment = evaluate_post_action_judgment(
            runner,
            state=state,
            logger=logger or _NullLogger(),
            fact_kind="action_result",
            action_result=action_result,
            current_command=current_command,
            current_step_index=current_step_index,
            total_steps=len(current_plan.steps),
            runtime_facts={
                "consecutive_step_failures": int(
                    getattr(state, "consecutive_step_failures", 0) or 0
                ),
            },
        )
    if judgment is None:
        transition(state, "judgment_ask_user", logger=logger)
        state.post_action_user_message = (
            "Post-action judgment is unavailable; user guidance is required."
        )
        return

    apply_post_action_judgment(
        state=state,
        judgment=judgment,
        step_key=step_key,
        total_steps=len(current_plan.steps),
        max_retries_per_step=int(
            getattr(runner.options, "max_retries_per_step", 0) or 0
        ),
        transition_to_replan=_transition_to_replan,
    )
