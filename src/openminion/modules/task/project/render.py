from __future__ import annotations

from .models import ProjectControlResult, ProjectRun
from .operator import ProjectOperatorInboxItem


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


def render_project_operator_inbox_item(item: ProjectOperatorInboxItem) -> str:
    lines = [
        f"task_id: {item.task_id}",
        f"state: {item.state.value}",
        f"resume_action: {item.resume_action.value}",
        f"project_run_id: {item.project_run_id or '-'}",
        f"goal_id: {item.goal_id or '-'}",
        f"checkpoint: {item.last_checkpoint_id or '-'}",
        f"milestone: {item.current_milestone or '-'}",
        f"cycles: {item.committed_cycle_count}",
        f"cycles_remaining: {item.remaining_cycle_count}",
    ]
    if item.current_step_ref:
        lines.append(f"current_step: {item.current_step_ref}")
    if item.blocker:
        lines.append(f"blocker: {item.blocker}")
    if item.resume_hint:
        lines.append(f"resume_hint: {item.resume_hint}")
    if item.artifact_refs:
        lines.append(f"artifacts: {', '.join(item.artifact_refs)}")
    if item.progress_refs:
        lines.append(f"progress_refs: {', '.join(item.progress_refs)}")
    if item.verifier_refs:
        lines.append(f"verifier_refs: {', '.join(item.verifier_refs)}")
    if item.effect_refs:
        lines.append(f"effect_refs: {', '.join(item.effect_refs)}")
    if item.next_wake_job_id:
        lines.append(f"next_wake: {item.next_wake_job_id}")
    if item.claim_owner_id:
        lines.append(
            f"claim: owner={item.claim_owner_id} fence={item.claim_fence_token} "
            f"expires={item.claim_expires_at}"
        )
    return "\n".join(lines)


__all__ = [
    "render_project_control_result",
    "render_project_operator_inbox_item",
    "render_project_run_summary",
]
