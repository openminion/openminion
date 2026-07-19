from __future__ import annotations

from openminion.modules.task.runtime.lifecycle import (
    TaskLifecycleRecord,
    TaskLifecycleState,
    TaskManager,
)

from .project_checkpoints import replay_project_cycles
from .project_models import ProjectControlAction, ProjectControlResult


def apply_project_control(
    task_manager: TaskManager,
    *,
    task_id: str,
    action: ProjectControlAction,
    priority: str | None = None,
    input_request_id: str | None = None,
    answer: str | None = None,
    extra_iterations: int = 0,
    extra_wall_clock_ms: int = 0,
    extra_tool_calls: int = 0,
) -> ProjectControlResult:
    record = task_manager.get_task(task_id)
    if record is None:
        raise KeyError(f"task not found: {task_id}")

    if (
        action == ProjectControlAction.PAUSE
        and record.state == TaskLifecycleState.ACTIVE
    ):
        record = task_manager.transition_task(
            task_id=record.task_id,
            to_state=TaskLifecycleState.PAUSED,
        )
    elif (
        action == ProjectControlAction.RESUME
        and record.state == TaskLifecycleState.PAUSED
    ):
        record = task_manager.transition_task(
            task_id=record.task_id,
            to_state=TaskLifecycleState.ACTIVE,
        )
    elif action == ProjectControlAction.CANCEL:
        record = task_manager.transition_task(
            task_id=record.task_id,
            to_state=TaskLifecycleState.CANCELLED,
        )
    elif action == ProjectControlAction.REPRIORITIZE:
        normalized_priority = str(priority or "").strip()
        if not normalized_priority:
            raise ValueError("priority is required for reprioritize")
        metadata = dict(record.metadata)
        metadata["priority"] = normalized_priority
        record = task_manager.update_task_metadata(
            task_id=record.task_id,
            metadata=metadata,
        )
    elif action == ProjectControlAction.ANSWER_INPUT:
        request_id = str(input_request_id or "").strip()
        normalized_answer = str(answer or "").strip()
        if not request_id:
            raise ValueError("input_request_id is required for answer-input-request")
        if not normalized_answer:
            raise ValueError("answer is required for answer-input-request")
        metadata = dict(record.metadata)
        answers = list(metadata.get("operator_answers", []) or [])
        answers.append({"request_id": request_id, "answer": normalized_answer})
        metadata["operator_answers"] = answers
        record = task_manager.update_task_metadata(
            task_id=record.task_id,
            metadata=metadata,
        )
    elif action == ProjectControlAction.EXTEND_BUDGET:
        if extra_iterations < 1 and extra_wall_clock_ms < 1 and extra_tool_calls < 1:
            raise ValueError("extend-budget requires a positive budget delta")
        metadata = dict(record.metadata)
        current = dict(metadata.get("budget_extensions", {}) or {})
        current["extra_iterations"] = int(current.get("extra_iterations") or 0) + max(
            0, int(extra_iterations)
        )
        current["extra_wall_clock_ms"] = int(
            current.get("extra_wall_clock_ms") or 0
        ) + max(0, int(extra_wall_clock_ms))
        current["extra_tool_calls"] = int(current.get("extra_tool_calls") or 0) + max(
            0, int(extra_tool_calls)
        )
        metadata["budget_extensions"] = current
        record = task_manager.update_task_metadata(
            task_id=record.task_id,
            metadata=metadata,
        )

    return build_project_control_result(task_manager, record, action=action)


def build_project_control_result(
    task_manager: TaskManager,
    record: TaskLifecycleRecord,
    *,
    action: ProjectControlAction,
) -> ProjectControlResult:
    cycles = replay_project_cycles(task_manager, task_id=record.task_id)
    metadata = record.metadata
    operator_answers = list(metadata.get("operator_answers", []) or [])
    return ProjectControlResult(
        action=action,
        task_id=record.task_id,
        state=record.state,
        project_run_id=str(metadata.get("project_run_id") or "") or None,
        autonomy_run_id=str(metadata.get("autonomy_run_id") or "") or None,
        goal_id=str(metadata.get("goal_id") or "") or None,
        last_checkpoint_id=str(metadata.get("last_checkpoint_id") or "") or None,
        resume_count=int(metadata.get("resume_count") or 0),
        priority=str(metadata.get("priority") or "") or None,
        operator_answer_count=len(operator_answers),
        budget_extensions={
            str(key): int(value)
            for key, value in dict(metadata.get("budget_extensions", {}) or {}).items()
        },
        cycle_count=len(cycles),
    )


__all__ = [
    "apply_project_control",
    "build_project_control_result",
]
