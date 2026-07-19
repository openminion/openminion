from __future__ import annotations

from .project_models import ProjectControlResult, ProjectRun


def render_project_run_summary(project_run: ProjectRun) -> str:
    lines = [
        f"project_run_id: {project_run.project_run_id}",
        f"autonomy_run_id: {project_run.autonomy_run_id}",
        f"task_id: {project_run.task_id}",
        f"goal_id: {project_run.goal_id}",
        f"status: {project_run.status.value}",
        f"phase: {project_run.phase.value}",
        f"verification: {project_run.verification_state.value}",
        f"workspace: {project_run.workspace_ref}",
    ]
    if project_run.last_checkpoint_id:
        lines.append(f"checkpoint: {project_run.last_checkpoint_id}")
    if project_run.blocked_reason:
        lines.append(f"blocked_reason: {project_run.blocked_reason}")
    return "\n".join(lines)


def render_project_control_result(result: ProjectControlResult) -> str:
    lines = [
        f"task_id: {result.task_id}",
        f"action: {result.action.value}",
        f"state: {result.state.value}",
        f"project_run_id: {result.project_run_id or '-'}",
        f"goal_id: {result.goal_id or '-'}",
        f"checkpoint: {result.last_checkpoint_id or '-'}",
        f"cycles: {result.cycle_count}",
        f"resume_count: {result.resume_count}",
    ]
    if result.priority:
        lines.append(f"priority: {result.priority}")
    if result.operator_answer_count:
        lines.append(f"operator_answers: {result.operator_answer_count}")
    if result.budget_extensions:
        lines.append(f"budget_extensions: {result.budget_extensions}")
    return "\n".join(lines)


__all__ = [
    "render_project_control_result",
    "render_project_run_summary",
]
