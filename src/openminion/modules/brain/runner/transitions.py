from ..constants import (
    BRAIN_STATE_JOB_PENDING,
    BRAIN_STATE_STOPPED,
    BRAIN_STATE_WAITING_USER,
)
from ..schemas import StepOutput, WorkingState

_WAITING_MESSAGES = {
    BRAIN_STATE_WAITING_USER: "Waiting for user input.",
    BRAIN_STATE_JOB_PENDING: "Async job is still pending.",
    BRAIN_STATE_STOPPED: "Execution is stopped.",
}


def guard_waiting_state(
    *, state: WorkingState, user_input: str | None
) -> StepOutput | None:
    if user_input:
        return None
    message = _WAITING_MESSAGES.get(state.status)
    return (
        StepOutput(
            session_id=state.session_id,
            status=state.status,
            message=message,
            working_state=state,
        )
        if message
        else None
    )
