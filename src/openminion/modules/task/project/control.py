from __future__ import annotations

from collections.abc import Mapping

from openminion.modules.task.runtime.lifecycle import (
    TaskLifecycleRecord,
    TaskLifecycleState,
    TaskManager,
)

from .checkpoints import load_latest_project_checkpoint, replay_project_cycles
from .constants import (
    PROJECT_OPERATOR_GUIDANCE_MAX_ANSWERS,
    REPOSITORY_LIFECYCLE_TEXT_MAX_CHARS,
)
from .models import ProjectControlAction, ProjectControlResult


def _bounded_guidance_text(value: str | None, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    if len(normalized) > REPOSITORY_LIFECYCLE_TEXT_MAX_CHARS:
        raise ValueError(
            f"{field} exceeds {REPOSITORY_LIFECYCLE_TEXT_MAX_CHARS} characters"
        )
    return normalized


def _next_guidance_revision(metadata: dict[str, object]) -> int:
    revision = int(str(metadata.get("operator_guidance_revision") or 0)) + 1
    metadata["operator_guidance_revision"] = revision
    return revision


def _consumed_guidance_revision(task_manager: TaskManager, task_id: str) -> int:
    latest = load_latest_project_checkpoint(task_manager, task_id=task_id)
    if latest is None:
        return 0
    return int(str(latest.payload.get("operator_guidance_consumed_revision") or 0))


def _answer_project_input(
    task_manager: TaskManager,
    record: TaskLifecycleRecord,
    *,
    input_request_id: str | None,
    answer: str | None,
) -> TaskLifecycleRecord:
    request_id = _bounded_guidance_text(input_request_id, field="input_request_id")
    normalized_answer = _bounded_guidance_text(answer, field="answer")
    consumed_revision = _consumed_guidance_revision(task_manager, record.task_id)

    def answer_input(metadata: dict[str, object]) -> dict[str, object]:
        current_answers = metadata.get("operator_answers")
        answers = (
            [
                item
                for item in current_answers
                if isinstance(item, dict)
                and int(item.get("revision") or 0) > consumed_revision
            ]
            if isinstance(current_answers, list)
            else []
        )
        if len(answers) >= PROJECT_OPERATOR_GUIDANCE_MAX_ANSWERS:
            raise ValueError(
                "operator answers are full; let the project consume them first"
            )
        revision = _next_guidance_revision(metadata)
        answers.append(
            {
                "request_id": request_id,
                "answer": normalized_answer,
                "revision": revision,
            }
        )
        metadata["operator_answers"] = answers
        return metadata

    return task_manager.mutate_task_metadata(
        task_id=record.task_id,
        mutate=answer_input,
    )


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
        normalized_priority = _bounded_guidance_text(priority, field="priority")

        def reprioritize(metadata: dict[str, object]) -> dict[str, object]:
            revision = _next_guidance_revision(metadata)
            metadata["priority"] = normalized_priority
            metadata["priority_revision"] = revision
            return metadata

        record = task_manager.mutate_task_metadata(
            task_id=record.task_id,
            mutate=reprioritize,
        )
    elif action == ProjectControlAction.ANSWER_INPUT:
        record = _answer_project_input(
            task_manager,
            record,
            input_request_id=input_request_id,
            answer=answer,
        )
    elif action == ProjectControlAction.EXTEND_BUDGET:
        if extra_iterations < 1 and extra_wall_clock_ms < 1 and extra_tool_calls < 1:
            raise ValueError("extend-budget requires a positive budget delta")

        def extend_budget(metadata: dict[str, object]) -> dict[str, object]:
            raw_extensions = metadata.get("budget_extensions")
            if raw_extensions is None:
                current: dict[str, object] = {}
            elif isinstance(raw_extensions, Mapping):
                current = dict(raw_extensions)
            else:
                raise ValueError("budget_extensions must be a mapping")
            for key, delta in (
                ("extra_iterations", extra_iterations),
                ("extra_wall_clock_ms", extra_wall_clock_ms),
                ("extra_tool_calls", extra_tool_calls),
            ):
                current[key] = int(str(current.get(key) or 0)) + max(0, delta)
            metadata["budget_extensions"] = current
            return metadata

        record = task_manager.mutate_task_metadata(
            task_id=record.task_id,
            mutate=extend_budget,
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
