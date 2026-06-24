from __future__ import annotations

import hashlib

from openminion.modules.context.schemas import (
    TaskPlan,
    TaskPlanStepBlocked,
    TaskPlanStepCompleted,
)
from openminion.modules.task.schemas import (
    PlanDraft,
    PlanStepDraft,
    PlanStepStatus,
    StepUpdateInput,
    TaskAttachPlanOp,
    TaskCreateInput,
    TaskCreateOp,
    TaskOps,
    TaskStepUpdateOp,
)

PLAN_TASK_OPS_OUTPUT_KEY = "task.ops"
PLAN_TASK_OPS_TOUCHED_TASK_IDS_OUTPUT_KEY = "task.ops.touched_task_ids"
PLAN_TASK_CREATED_BY_MODE = "plan"


def stable_task_id_for_plan_id(plan_id: str) -> str:
    """Derive a deterministic task id from the model-authored plan id."""

    normalized = str(plan_id or "").strip()
    if not normalized:
        raise ValueError("plan_id is required for task id derivation")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"tsk_plan_{digest}"


def task_ops_for_plan_declare(plan: TaskPlan) -> TaskOps:
    task_id = stable_task_id_for_plan_id(plan.plan_id)
    return TaskOps(
        ops=[
            TaskCreateOp(
                input=TaskCreateInput(
                    task_id=task_id,
                    title=plan.objective,
                    created_by_mode=PLAN_TASK_CREATED_BY_MODE,
                )
            ),
            TaskAttachPlanOp(
                task_id=task_id,
                plan=PlanDraft(
                    plan_id=plan.plan_id,
                    plan_name=plan.objective,
                    root_goal_id=plan.root_goal_id,
                    steps=[
                        PlanStepDraft(
                            step_id=step.step_id,
                            title=step.description,
                            instruction=step.description,
                        )
                        for step in plan.steps
                    ],
                ),
            ),
        ]
    )


def task_ops_for_step_completed(completed: TaskPlanStepCompleted) -> TaskOps:
    task_id = stable_task_id_for_plan_id(completed.plan_id)
    return TaskOps(
        ops=[
            TaskStepUpdateOp(
                task_id=task_id,
                step_id=completed.step_id,
                input=StepUpdateInput(
                    status=PlanStepStatus.DONE,
                    note=completed.output_summary or completed.outcome or None,
                    idempotency_key=_step_idempotency_key(
                        completed.plan_id,
                        completed.step_id,
                        "completed",
                    ),
                    executing_mode=PLAN_TASK_CREATED_BY_MODE,
                ),
            )
        ]
    )


def task_ops_for_step_blocked(blocked: TaskPlanStepBlocked) -> TaskOps:
    task_id = stable_task_id_for_plan_id(blocked.plan_id)
    return TaskOps(
        ops=[
            TaskStepUpdateOp(
                task_id=task_id,
                step_id=blocked.step_id,
                input=StepUpdateInput(
                    status=PlanStepStatus.BLOCKED,
                    note=blocked.blocker_details or blocked.blocker_type,
                    idempotency_key=_step_idempotency_key(
                        blocked.plan_id,
                        blocked.step_id,
                        "blocked",
                    ),
                    executing_mode=PLAN_TASK_CREATED_BY_MODE,
                ),
            )
        ]
    )


def _step_idempotency_key(plan_id: str, step_id: str, action: str) -> str:
    raw = f"{plan_id}\\0{step_id}\\0{action}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"plan_step_{digest}"
