from ..constants import (
    BRAIN_STATE_JOB_PENDING,
    BRAIN_STATE_STOPPED,
    BRAIN_STATE_WAITING_USER,
)
from ..schemas import StepOutput, WorkingState


def guard_waiting_state(
    *, state: WorkingState, user_input: str | None
) -> StepOutput | None:
    if user_input:
        return None
    if state.status == BRAIN_STATE_WAITING_USER:
        return StepOutput(
            session_id=state.session_id,
            status=state.status,
            message="Waiting for user input.",
            working_state=state,
        )
    if state.status == BRAIN_STATE_JOB_PENDING:
        return StepOutput(
            session_id=state.session_id,
            status=state.status,
            message="Async job is still pending.",
            working_state=state,
        )
    if state.status == BRAIN_STATE_STOPPED:
        return StepOutput(
            session_id=state.session_id,
            status=state.status,
            message="Execution is stopped.",
            working_state=state,
        )
    return None
