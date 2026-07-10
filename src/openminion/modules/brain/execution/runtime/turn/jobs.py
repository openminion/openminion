"""Turn-job runtime implementation for async reconciliation."""

from typing import TYPE_CHECKING, Any

from ....constants import (
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_DISPOSITION_CLOSE,
    BRAIN_DISPOSITIONS_RETRYING,
    BRAIN_JOB_STATUS_PENDING,
    BRAIN_JOB_STATUS_RUNNING,
    BRAIN_STATE_ACTIVE,
    BRAIN_STATE_DONE,
    BRAIN_STATE_JOB_PENDING,
    BRAIN_STATE_WAITING_USER,
    MissionStatus,
)
from ....diagnostics.events import CanonicalEventLogger
from ....diagnostics.transitions import transition
from ...intent_state import update_intent_execution_states
from ...mission import (
    mission_is_active,
    set_mission_status,
    update_mission_task,
)
from ....schemas import ActionError, ActionResult, StepOutput, WorkingState
from ...closure import final_close_message
from ...memory import extract_success_memories
from ...delegation import _runner_delegate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....runner import BrainRunner


def reconcile_pending_jobs(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
) -> Any:
    if state.status != BRAIN_STATE_JOB_PENDING or not state.pending_jobs:
        return None
    job = state.pending_jobs[0]
    from ... import poll_async_job as poll_async_job_barrel

    polled = poll_async_job_barrel(runner, state=state, job=job)
    if not isinstance(polled, dict):
        return _pending_response(
            runner=runner, state=state, logger=logger, job=job, message_status="pending"
        )
    raw_status = str(polled.get("status", BRAIN_JOB_STATUS_PENDING)).strip().lower()
    if raw_status in {
        BRAIN_JOB_STATUS_PENDING,
        BRAIN_JOB_STATUS_RUNNING,
        BRAIN_STATE_ACTIVE,
    }:
        _mark_job_still_pending(
            state=state, logger=logger, runner=runner, job=job, raw_status=raw_status
        )
        return _pending_response(
            runner=runner,
            state=state,
            logger=logger,
            job=job,
            message_status=job.status,
        )
    state.pending_jobs = state.pending_jobs[1:]
    summary, outputs = _job_result_payload(polled)
    if raw_status in {"success", "completed", "done"}:
        return _handle_completed_job(
            runner=runner,
            state=state,
            logger=logger,
            job=job,
            summary=summary,
            outputs=outputs,
        )
    return _handle_failed_job(
        runner=runner,
        state=state,
        logger=logger,
        job=job,
        polled=polled,
        summary=summary,
        outputs=outputs,
    )


def _pending_response(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    job: Any,
    message_status: str,
) -> Any:
    return _runner_delegate(
        "_respond_with_meta",
        runner,
        state=state,
        logger=logger,
        message=f"Async job {job.task_id} is still {message_status}.",
        status=BRAIN_STATE_JOB_PENDING,
    )


def _mark_job_still_pending(
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
    runner: "BrainRunner",
    job: Any,
    raw_status: str,
) -> None:
    if mission_is_active(state) and state.mission is not None:
        set_mission_status(
            mission=state.mission,
            status=MissionStatus.AWAITING_ASYNC,
            reason=f"async job {job.task_id} is still pending",
            route_action=str(getattr(state.mission, "latest_route_action", "") or ""),
        )
        update_mission_task(runner=runner, mission=state.mission, to_state="paused")
        logger.emit(
            "brain.mission.async_pending",
            {
                "mission_id": state.mission.mission_id,
                "task_id": job.task_id,
                "job_status": raw_status,
            },
            trace_id=state.trace_id,
        )
    state.pending_jobs[0].status = (
        BRAIN_JOB_STATUS_RUNNING
        if raw_status == BRAIN_JOB_STATUS_RUNNING
        else BRAIN_JOB_STATUS_PENDING
    )


def _job_result_payload(polled: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    summary = str(polled.get("summary", "") or "").strip()
    outputs = polled.get("outputs") if isinstance(polled.get("outputs"), dict) else {}
    return summary, outputs


def _handle_completed_job(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    job: Any,
    summary: str,
    outputs: dict[str, Any],
) -> Any:
    _resume_mission_after_success(state=state, logger=logger, runner=runner, job=job)
    logger.emit(
        "job.completed",
        {
            "task_id": job.task_id,
            "command_id": job.command_id,
            "provider": job.provider,
        },
        trace_id=state.trace_id,
        task_id=job.task_id,
    )
    action_result = ActionResult(
        command_id=job.command_id,
        status=BRAIN_ACTION_STATUS_SUCCESS,
        summary=summary or f"Async job {job.task_id} completed.",
        outputs=outputs,
    )
    current_command = _current_pending_job_plan_command(state)
    update_intent_execution_states(
        runner,
        state=state,
        command=current_command,
        action_result=action_result,
        current_step_index=state.cursor,
    )
    _advance_completed_step(state=state, logger=logger, job=job)
    if state.plan is not None and state.cursor >= len(state.plan.steps):
        return _handle_completed_plan(
            runner=runner, state=state, logger=logger, action_result=action_result
        )
    transition(state, "job_completed", logger=logger)
    _runner_delegate("_save_state", runner, state)
    return StepOutput(
        session_id=state.session_id,
        status=state.status,
        message=None,
        working_state=state,
        action_result=action_result,
    )


def _resume_mission_after_success(
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
    runner: "BrainRunner",
    job: Any,
) -> None:
    if mission_is_active(state) and state.mission is not None:
        set_mission_status(
            mission=state.mission,
            status=MissionStatus.ACTIVE,
            reason=f"async job {job.task_id} resumed mission execution",
            route_action=str(getattr(state.mission, "latest_route_action", "") or ""),
        )
        update_mission_task(runner=runner, mission=state.mission)
        logger.emit(
            "brain.mission.async_resumed",
            {"mission_id": state.mission.mission_id, "task_id": job.task_id},
            trace_id=state.trace_id,
        )


def _current_pending_job_plan_command(state: WorkingState) -> Any | None:
    if state.plan is None or state.cursor >= len(state.plan.steps):
        return None
    return state.plan.steps[state.cursor]


def _advance_completed_step(
    *, state: WorkingState, logger: CanonicalEventLogger, job: Any
) -> None:
    if state.plan is None or state.cursor >= len(state.plan.steps):
        return
    if state.plan.steps[state.cursor].command_id != job.command_id:
        return
    logger.emit(
        "plan.step.completed",
        {
            "command_id": job.command_id,
            "cursor": state.cursor,
            "final_step": state.cursor + 1 >= len(state.plan.steps),
            "async_reconciled": True,
        },
        trace_id=state.trace_id,
    )
    state.cursor += 1


def _handle_completed_plan(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    action_result: ActionResult,
) -> Any:
    transition(state, "job_plan_completed", logger=logger)
    judgment = _runner_delegate(
        "_evaluate_turn_closure",
        runner,
        state=state,
        action_result=action_result,
        logger=logger,
        completion_reason="async_plan_completed",
    )
    disposition = _runner_delegate(
        "_apply_closure_judgment", runner, state=state, judgment=judgment
    )
    if disposition == BRAIN_DISPOSITION_CLOSE:
        extract_success_memories(
            runner,
            state=state,
            action_result=action_result,
            judgment=judgment,
            logger=logger,
        )
        return _runner_delegate(
            "_respond_with_meta",
            runner,
            state=state,
            logger=logger,
            message=final_close_message(
                state=state,
                judgment=judgment,
                action_result=action_result,
                fallback_message="Completed.",
            ),
            status=BRAIN_STATE_DONE,
            action_result=action_result,
        )
    if disposition in BRAIN_DISPOSITIONS_RETRYING:
        _runner_delegate("_save_state", runner, state)
        return StepOutput(
            session_id=state.session_id,
            status=state.status,
            message=None,
            working_state=state,
            action_result=action_result,
        )
    return _runner_delegate(
        "_respond_with_meta",
        runner,
        state=state,
        logger=logger,
        message=judgment.reason or "I need clarification before closing this task.",
        status=BRAIN_STATE_WAITING_USER,
        action_result=action_result,
    )


def _handle_failed_job(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    job: Any,
    polled: dict[str, Any],
    summary: str,
    outputs: dict[str, Any],
) -> Any:
    logger.emit(
        "job.failed",
        {
            "task_id": job.task_id,
            "command_id": job.command_id,
            "provider": job.provider,
        },
        trace_id=state.trace_id,
        task_id=job.task_id,
    )
    logger.emit(
        "plan.step.failed",
        {
            "command_id": job.command_id,
            "cursor": state.cursor,
            "reason_code": "async_job_failed",
            "async_reconciled": True,
        },
        trace_id=state.trace_id,
    )
    failed_result = ActionResult(
        command_id=job.command_id,
        status=BRAIN_ACTION_STATUS_FAILED,
        summary=summary or "async_job_failed",
        outputs=outputs,
        error=ActionError(
            code=str((polled.get("error") or {}).get("code", "ASYNC_JOB_FAILED")),
            message=str(
                (polled.get("error") or {}).get(
                    "message", f"Async job {job.task_id} failed."
                )
            ),
        ),
    )
    update_intent_execution_states(
        runner,
        state=state,
        command=_current_pending_job_plan_command(state),
        action_result=failed_result,
        current_step_index=state.cursor,
    )
    transition(state, "job_failed", logger=logger)
    return _runner_delegate(
        "_respond_with_meta",
        runner,
        state=state,
        logger=logger,
        message=summary or f"Async job {job.task_id} failed.",
        status=BRAIN_STATE_WAITING_USER,
        action_result=failed_result,
    )
