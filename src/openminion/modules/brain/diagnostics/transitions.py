from __future__ import annotations

from typing import TYPE_CHECKING

from openminion.modules.brain.constants import (
    BRAIN_STATE_ACTIVE,
    BRAIN_STATE_CONTINUE,
    BRAIN_STATE_DONE,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_FAILED,
    BRAIN_STATE_JOB_PENDING,
    BRAIN_STATE_STOPPED,
    BRAIN_STATE_WAITING_USER,
)

if TYPE_CHECKING:
    from openminion.modules.brain.schemas.state import WorkingState
    from openminion.modules.brain.diagnostics.events import (
        CanonicalEventLogger,
    )

_A = BRAIN_STATE_ACTIVE
_W = BRAIN_STATE_WAITING_USER
_J = BRAIN_STATE_JOB_PENDING
_D = BRAIN_STATE_DONE
_E = BRAIN_STATE_ERROR
_S = BRAIN_STATE_STOPPED
_F = BRAIN_STATE_FAILED
_C = BRAIN_STATE_CONTINUE

TRANSITIONS: dict[tuple[str, str], str] = {
    (_A, "confirmation_required"): _W,
    (_A, "clarify_requested"): _W,
    (_A, "checkpoint_reached"): _W,
    (_A, "budget_exhausted"): _W,
    (_A, "step_failed_waiting"): _W,
    (_A, "feasibility_needs_user"): _W,
    (_A, "judgment_ask_user"): _W,
    (_A, "mode_paused"): _W,
    (_A, "retries_exhausted"): _W,
    (_A, "rlm_unavailable"): _W,
    (_A, "tool_args_invalid"): _W,
    (_A, "task_completed"): _D,
    (_A, "step_advanced"): _A,
    (_A, "step_retrying"): _A,
    (_A, "job_scheduled"): _J,
    (_A, "fatal_error"): _E,
    (_A, "execution_stopped"): _S,
    (_A, "task_failed"): _F,
    (_W, "user_input_received"): _A,
    (_W, "user_cancelled"): _D,
    (_W, "user_stopped"): _S,
    (_W, "confirmation_denied"): _W,
    (_W, "task_completed"): _D,
    (_W, "execution_stopped"): _S,
    (_W, "fatal_error"): _E,
    (_J, "job_completed"): _A,
    (_J, "job_plan_completed"): _D,
    (_J, "job_failed"): _W,
    (_J, "job_still_pending"): _J,
    (_D, "closure_replan"): _A,
    (_D, "closure_retry"): _A,
    (_D, "closure_needs_user"): _W,
    (_D, "closure_stopped"): _S,
    (_C, "next_tick"): _A,
}


class IllegalTransitionError(RuntimeError):
    """Raised when a transition is attempted that is not in the table."""

    def __init__(self, *, current: str, event: str, allowed: list[str]) -> None:
        self.current = current
        self.event = event
        self.allowed = allowed
        allowed_str = ", ".join(allowed) if allowed else "(none)"
        super().__init__(
            f"Illegal brain-status transition: status={current!r}, "
            f"event={event!r}. Allowed events from {current!r}: {allowed_str}"
        )


def allowed_events(status: str) -> list[str]:
    """Return sorted list of event names legal from *status*."""
    return sorted(ev for (st, ev) in TRANSITIONS if st == status)


def transition(
    state: "WorkingState",
    event: str,
    *,
    logger: "CanonicalEventLogger | None" = None,
) -> None:
    """Perform a guarded runtime status transition."""
    key = (state.status, event)
    if key not in TRANSITIONS:
        targets_for_event = {
            tgt for (src, ev), tgt in TRANSITIONS.items() if ev == event
        }
        if state.status in targets_for_event:
            return
        raise IllegalTransitionError(
            current=state.status,
            event=event,
            allowed=allowed_events(state.status),
        )
    old = state.status
    state.status = TRANSITIONS[key]
    if logger is not None and callable(getattr(logger, "emit", None)):
        logger.emit(
            "brain.state.transition",
            {
                "from_status": old,
                "to_status": state.status,
                "event": event,
            },
        )


def set_status_unchecked(
    state: "WorkingState",
    status: str,
    *,
    reason: str = "",
) -> None:
    """Set status without transition guard."""
    state.status = status
