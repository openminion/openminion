from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openminion.modules.task.autonomy import AutonomyRunStatus
from openminion.modules.task.runtime.lifecycle import (
    TaskLifecycleRecord,
    TaskLifecycleState,
)

from .models import ProjectRun


class ProjectOperatorWorkState(StrEnum):
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ProjectOperatorResumeAction(StrEnum):
    CONTINUE = "continue"
    APPROVE = "approve"
    ANSWER_INPUT = "answer-input-request"
    INSPECT_BLOCKER = "inspect-blocker"
    NONE = "none"


class ProjectOperatorInboxItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    state: ProjectOperatorWorkState
    project_run_id: str | None = None
    autonomy_run_id: str | None = None
    goal_id: str | None = None
    phase: str | None = None
    current_step_ref: str | None = None
    last_checkpoint_id: str | None = None
    blocker: str | None = None
    resume_action: ProjectOperatorResumeAction
    resume_hint: str | None = None
    artifact_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _waiting_and_blocked_need_resume_hint(self) -> "ProjectOperatorInboxItem":
        if self.state in {
            ProjectOperatorWorkState.WAITING,
            ProjectOperatorWorkState.BLOCKED,
        } and not (self.resume_hint or self.blocker):
            raise ValueError("waiting or blocked work requires resume_hint or blocker")
        return self


def build_project_operator_inbox_item(
    project_run: ProjectRun,
    *,
    task_record: TaskLifecycleRecord | None = None,
    current_step_ref: str | None = None,
    next_resume_action: str | None = None,
    artifact_refs: tuple[str, ...] = (),
) -> ProjectOperatorInboxItem:
    state = _operator_state(project_run.status, task_record)
    return ProjectOperatorInboxItem(
        task_id=project_run.task_id,
        state=state,
        project_run_id=project_run.project_run_id,
        autonomy_run_id=project_run.autonomy_run_id,
        goal_id=project_run.goal_id,
        phase=project_run.phase.value,
        current_step_ref=current_step_ref,
        last_checkpoint_id=project_run.last_checkpoint_id,
        blocker=project_run.blocked_reason,
        resume_action=_resume_action(project_run.status),
        resume_hint=_resume_hint(
            task_record=task_record,
            next_resume_action=next_resume_action,
            blocked_reason=project_run.blocked_reason,
        ),
        artifact_refs=artifact_refs,
    )


def _operator_state(
    status: AutonomyRunStatus,
    task_record: TaskLifecycleRecord | None,
) -> ProjectOperatorWorkState:
    if task_record is not None:
        if task_record.state == TaskLifecycleState.CANCELLED:
            return ProjectOperatorWorkState.CANCELLED
        if task_record.state == TaskLifecycleState.FAILED:
            return ProjectOperatorWorkState.FAILED
        if task_record.state == TaskLifecycleState.DONE:
            return ProjectOperatorWorkState.COMPLETED
        if task_record.state == TaskLifecycleState.PAUSED:
            return ProjectOperatorWorkState.WAITING

    if status in {
        AutonomyRunStatus.WAITING_FOR_APPROVAL,
        AutonomyRunStatus.WAITING_FOR_INPUT,
    }:
        return ProjectOperatorWorkState.WAITING
    if status == AutonomyRunStatus.BLOCKED:
        return ProjectOperatorWorkState.BLOCKED
    if status == AutonomyRunStatus.COMPLETED:
        return ProjectOperatorWorkState.COMPLETED
    if status == AutonomyRunStatus.CANCELLED:
        return ProjectOperatorWorkState.CANCELLED
    if status == AutonomyRunStatus.FAILED:
        return ProjectOperatorWorkState.FAILED
    return ProjectOperatorWorkState.RUNNING


def _resume_action(status: AutonomyRunStatus) -> ProjectOperatorResumeAction:
    if status == AutonomyRunStatus.WAITING_FOR_APPROVAL:
        return ProjectOperatorResumeAction.APPROVE
    if status == AutonomyRunStatus.WAITING_FOR_INPUT:
        return ProjectOperatorResumeAction.ANSWER_INPUT
    if status == AutonomyRunStatus.BLOCKED:
        return ProjectOperatorResumeAction.INSPECT_BLOCKER
    if status in {AutonomyRunStatus.QUEUED, AutonomyRunStatus.RUNNING}:
        return ProjectOperatorResumeAction.CONTINUE
    return ProjectOperatorResumeAction.NONE


def _resume_hint(
    *,
    task_record: TaskLifecycleRecord | None,
    next_resume_action: str | None,
    blocked_reason: str | None,
) -> str | None:
    explicit = (next_resume_action or "").strip()
    if explicit:
        return explicit
    blocker = (blocked_reason or "").strip()
    if blocker:
        return blocker
    if task_record is not None and task_record.state == TaskLifecycleState.PAUSED:
        return "resume project task"
    return None


__all__ = [
    "ProjectOperatorInboxItem",
    "ProjectOperatorResumeAction",
    "ProjectOperatorWorkState",
    "build_project_operator_inbox_item",
]
