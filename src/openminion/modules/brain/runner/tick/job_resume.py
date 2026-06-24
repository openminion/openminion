from types import SimpleNamespace
from typing import TYPE_CHECKING

from ...constants import (
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
    BRAIN_STATE_JOB_PENDING,
)
from ...execution.dispatch import (
    invoke_decision_direct,
    maybe_resume_task_backed_direct,
)
from ...schemas import iso_now, new_uuid
from ..resume import resolve_cron_resume_selection
from .context import _runner_delegate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..core import BrainRunner


def try_resume(
    *,
    runner: "BrainRunner",
    state,
    user_input: str | None,
    trace_id: str | None,
    logger,
    session_id: str,
):
    pending_delegate_job_id = str(getattr(state, "delegation_job_id", "") or "").strip()
    if (
        state.status == BRAIN_STATE_JOB_PENDING
        and pending_delegate_job_id
        and str(getattr(state, "active_mode_name", "") or "").strip()
        == BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED
    ):
        if user_input is not None and user_input.strip():
            state.trace_id = trace_id or new_uuid()
            runner.session_api.append_turn(
                session_id, "user", user_input, meta={"ts": iso_now()}
            )
        return invoke_decision_direct(
            runner,
            state=state,
            decision=SimpleNamespace(
                mode=BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
                reason_code="delegate_async_resume",
                confidence=1.0,
                sub_intents=[],
                rationale="",
                question=None,
                answer=None,
            ),
            user_input=user_input,
            logger=logger,
        ).to_step_output()

    if state.status == BRAIN_STATE_JOB_PENDING and state.pending_jobs:
        reconciled = _runner_delegate(
            "_reconcile_pending_jobs", runner, state=state, logger=logger
        )
        if reconciled is not None:
            return reconciled

    cron_resume_selection = resolve_cron_resume_selection(
        task_manager=getattr(runner, "task_manager", None),
        task_id_hint=getattr(state, "resume_task_id_hint", None),
        cron_job_id_hint=getattr(state, "resume_cron_job_id_hint", None),
    )
    resumable_task_result = maybe_resume_task_backed_direct(
        runner,
        state=state,
        user_input=user_input,
        logger=logger,
        preferred_task_id=cron_resume_selection.task_id,
    )
    if resumable_task_result is not None:
        return resumable_task_result.to_step_output()
    return None
