from __future__ import annotations

from openminion.modules.task.runtime.lifecycle import TaskManager

from .project_models import (
    ProjectBudgetCheckResult,
    ProjectBudgetPolicy,
    ProjectPermissionDecision,
)
from .project_policy import load_project_policy_state


def evaluate_project_budget(
    task_manager: TaskManager,
    *,
    task_id: str,
    iterations: int = 0,
    wall_clock_ms: int = 0,
    tool_calls: int = 0,
    tokens: int = 0,
) -> ProjectBudgetCheckResult:
    state = load_project_policy_state(task_manager, task_id=task_id)
    if state is None:
        return ProjectBudgetCheckResult(
            decision=ProjectPermissionDecision.APPROVAL_REQUIRED,
            reason="project budget policy is not configured",
        )
    record = task_manager.get_task(task_id)
    if record is None:
        raise KeyError(f"task not found: {task_id}")
    limits = _project_budget_limits_with_extensions(state.budget, record.metadata)
    used = {
        "iterations": max(0, int(iterations)),
        "wall_clock_ms": max(0, int(wall_clock_ms)),
        "tool_calls": max(0, int(tool_calls)),
        "tokens": max(0, int(tokens)),
    }
    remaining = {
        key: max(0, limit - used[key]) for key, limit in limits.items() if limit > 0
    }
    for key, limit in limits.items():
        if limit > 0 and used[key] > limit:
            return ProjectBudgetCheckResult(
                decision=ProjectPermissionDecision.BUDGET_EXCEEDED,
                reason=f"{key} budget exceeded",
                limits=limits,
                used=used,
                remaining=remaining,
            )
    return ProjectBudgetCheckResult(
        decision=ProjectPermissionDecision.ALLOWED,
        reason="within project budget policy",
        limits=limits,
        used=used,
        remaining=remaining,
    )


def _project_budget_limits_with_extensions(
    budget: ProjectBudgetPolicy,
    metadata: dict[str, object],
) -> dict[str, int]:
    extensions = (
        metadata.get("budget_extensions")
        if isinstance(metadata.get("budget_extensions"), dict)
        else {}
    )
    return {
        "iterations": int(budget.max_iterations)
        + _metadata_int(extensions, "extra_iterations"),
        "wall_clock_ms": int(budget.max_wall_clock_ms)
        + _metadata_int(extensions, "extra_wall_clock_ms"),
        "tool_calls": int(budget.max_tool_calls)
        + _metadata_int(extensions, "extra_tool_calls"),
        "tokens": int(budget.max_tokens),
    }


def _metadata_int(metadata: object, key: str) -> int:
    if not isinstance(metadata, dict):
        return 0
    try:
        return max(0, int(metadata.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


__all__ = ["evaluate_project_budget"]
