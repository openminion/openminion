from __future__ import annotations

from openminion.modules.task.autonomy import AutonomyRun, now_ms
from openminion.modules.task.runtime.lifecycle import (
    TaskLifecycleRecord,
    TaskLifecycleState,
    TaskManager,
)

from .project_models import (
    ProjectCheckpoint,
    ProjectCycleDecision,
    ProjectCycleRecord,
    ProjectRun,
    ProjectVerificationState,
)


_OPEN_PROJECT_TASK_STATES = {
    TaskLifecycleState.ACTIVE,
    TaskLifecycleState.PAUSED,
}


def build_project_run_projection(
    autonomy_run: AutonomyRun,
    *,
    objective_ledger_ref: str,
    evidence_ledger_ref: str,
    resume_packet_ref: str,
    operator_decision_log_ref: str,
    capability_plan_ref: str,
    metrics_summary_ref: str,
    task_record: TaskLifecycleRecord | None = None,
    project_run_id: str | None = None,
    verification_state: ProjectVerificationState = (
        ProjectVerificationState.NOT_STARTED
    ),
    next_wakeup_at_ms: int | None = None,
    blocked_reason: str | None = None,
) -> ProjectRun:
    task_id = autonomy_run.task_id
    goal_id = autonomy_run.goal_id
    workspace_ref = autonomy_run.workspace_ref
    if not task_id:
        raise ValueError("project run projection requires autonomy_run.task_id")
    if not goal_id:
        raise ValueError("project run projection requires autonomy_run.goal_id")
    if not workspace_ref:
        raise ValueError("project run projection requires autonomy_run.workspace_ref")
    if task_record is not None and task_record.task_id != task_id:
        raise ValueError("task_record.task_id must match autonomy_run.task_id")

    return ProjectRun(
        project_run_id=project_run_id or f"prun_{autonomy_run.run_id}",
        autonomy_run_id=autonomy_run.run_id,
        task_id=task_id,
        goal_id=goal_id,
        objective_ledger_ref=objective_ledger_ref,
        evidence_ledger_ref=evidence_ledger_ref,
        resume_packet_ref=resume_packet_ref,
        operator_decision_log_ref=operator_decision_log_ref,
        capability_plan_ref=capability_plan_ref,
        metrics_summary_ref=metrics_summary_ref,
        workspace_ref=workspace_ref,
        status=autonomy_run.status,
        phase=autonomy_run.phase,
        created_at_ms=autonomy_run.created_at_ms,
        updated_at_ms=autonomy_run.updated_at_ms,
        last_checkpoint_id=autonomy_run.checkpoint_id,
        next_wakeup_at_ms=next_wakeup_at_ms,
        blocked_reason=blocked_reason
        or (autonomy_run.last_error.message if autonomy_run.last_error else None),
        verification_state=verification_state,
        task_state=task_record.state if task_record is not None else None,
    )


def find_open_project_worker(
    task_manager: TaskManager,
    *,
    project_run_id: str,
    exclude_task_id: str | None = None,
) -> TaskLifecycleRecord | None:
    normalized_project_id = str(project_run_id or "").strip()
    if not normalized_project_id:
        raise ValueError("project_run_id is required")
    excluded = str(exclude_task_id or "").strip() or None
    for record in task_manager.lifecycle_repository.list(limit=1000):
        if record.state not in _OPEN_PROJECT_TASK_STATES:
            continue
        if excluded is not None and record.task_id == excluded:
            continue
        if str(record.metadata.get("project_run_id") or "") == normalized_project_id:
            return record
    return None


def link_project_run_to_task(
    task_manager: TaskManager,
    project_run: ProjectRun,
) -> TaskLifecycleRecord:
    record = task_manager.get_task(project_run.task_id)
    if record is None:
        raise KeyError(f"task not found: {project_run.task_id}")
    if record.state not in _OPEN_PROJECT_TASK_STATES:
        raise ValueError("project run can only link to an active or paused task")
    existing = find_open_project_worker(
        task_manager,
        project_run_id=project_run.project_run_id,
        exclude_task_id=project_run.task_id,
    )
    if existing is not None:
        raise ValueError(
            "open project worker already exists: "
            f"{project_run.project_run_id} on task {existing.task_id}"
        )

    metadata = dict(record.metadata)
    metadata.update(
        {
            "project_run_id": project_run.project_run_id,
            "autonomy_run_id": project_run.autonomy_run_id,
            "goal_id": project_run.goal_id,
            "project_status": project_run.status.value,
            "project_phase": project_run.phase.value,
            "verification_state": project_run.verification_state.value,
        }
    )
    return task_manager.update_task_metadata(
        task_id=project_run.task_id,
        metadata=metadata,
    )


def save_project_run_checkpoint(
    task_manager: TaskManager,
    project_run: ProjectRun,
    *,
    checkpoint_id: str,
    payload: dict[str, object] | None = None,
) -> ProjectCheckpoint:
    link_project_run_to_task(task_manager, project_run)
    checkpoint = ProjectCheckpoint(
        checkpoint_id=checkpoint_id,
        project_run=project_run.model_copy(
            update={"last_checkpoint_id": checkpoint_id}
        ),
        payload=dict(payload or {}),
    )
    task_manager.save_checkpoint(
        project_run.task_id,
        checkpoint_id,
        checkpoint.model_dump(mode="json"),
    )
    return checkpoint


def load_latest_project_checkpoint(
    task_manager: TaskManager,
    *,
    task_id: str,
) -> ProjectCheckpoint | None:
    latest = task_manager.get_latest_checkpoint(task_id)
    if latest is None:
        return None
    _checkpoint_id, state = latest
    if state.get("kind") != "project_run":
        raise ValueError("latest checkpoint is not a project_run checkpoint")
    return ProjectCheckpoint.model_validate(state)


def resume_project_run_from_latest_checkpoint(
    task_manager: TaskManager,
    *,
    task_id: str,
) -> ProjectRun:
    checkpoint = load_latest_project_checkpoint(task_manager, task_id=task_id)
    if checkpoint is None:
        raise KeyError(f"project checkpoint not found: {task_id}")
    record = task_manager.get_task(task_id)
    if record is None:
        raise KeyError(f"task not found: {task_id}")
    metadata = dict(record.metadata)
    metadata["resume_count"] = int(metadata.get("resume_count") or 0) + 1
    metadata["last_resume_checkpoint_id"] = checkpoint.checkpoint_id
    task_manager.update_task_metadata(task_id=task_id, metadata=metadata)
    return checkpoint.project_run


def record_project_cycle(
    task_manager: TaskManager,
    project_run: ProjectRun,
    *,
    cycle_id: str,
    milestone: str,
    intended_action: str,
    evidence_refs: tuple[str, ...],
    validation_refs: tuple[str, ...],
    decision: ProjectCycleDecision,
    decision_reason: str | None = None,
    checkpoint_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> ProjectCycleRecord:
    normalized_reason = str(decision_reason or "").strip() or None
    if decision != ProjectCycleDecision.CONTINUE and normalized_reason is None:
        raise ValueError("decision_reason is required unless decision is continue")
    effective_checkpoint_id = (
        str(checkpoint_id or "").strip()
        or f"{project_run.project_run_id}:{str(cycle_id).strip()}"
    )
    record = ProjectCycleRecord(
        cycle_id=cycle_id,
        project_run_id=project_run.project_run_id,
        task_id=project_run.task_id,
        goal_id=project_run.goal_id,
        milestone=milestone,
        intended_action=intended_action,
        evidence_refs=evidence_refs,
        validation_refs=validation_refs,
        checkpoint_id=effective_checkpoint_id,
        decision=decision,
        decision_reason=normalized_reason,
        created_at_ms=now_ms(),
    )
    checkpoint_payload: dict[str, object] = {
        "cycle": record.model_dump(mode="json"),
    }
    if payload:
        checkpoint_payload["payload"] = dict(payload)
    save_project_run_checkpoint(
        task_manager,
        project_run,
        checkpoint_id=effective_checkpoint_id,
        payload=checkpoint_payload,
    )
    return record


def replay_project_cycles(
    task_manager: TaskManager,
    *,
    task_id: str,
) -> tuple[ProjectCycleRecord, ...]:
    cycles: list[ProjectCycleRecord] = []
    for checkpoint_id in task_manager.list_checkpoints(task_id):
        state = task_manager.get_checkpoint(task_id, checkpoint_id)
        if not state or state.get("kind") != "project_run":
            continue
        checkpoint = ProjectCheckpoint.model_validate(state)
        raw_cycle = checkpoint.payload.get("cycle")
        if isinstance(raw_cycle, dict):
            cycles.append(ProjectCycleRecord.model_validate(raw_cycle))
    return tuple(cycles)


__all__ = [
    "build_project_run_projection",
    "find_open_project_worker",
    "link_project_run_to_task",
    "load_latest_project_checkpoint",
    "record_project_cycle",
    "replay_project_cycles",
    "resume_project_run_from_latest_checkpoint",
    "save_project_run_checkpoint",
]
