from __future__ import annotations

from time import perf_counter
from typing import Callable

from ..schemas import (
    PlanRecord,
    PlanDraft,
    StepUpdateInput,
    TaskAttachPlanOp,
    TaskCreateInput,
    TaskCreateOp,
    TaskOps,
    TaskRecord,
    TaskStatus,
    TaskStatusTransitionOp,
    TaskStepUpdateOp,
)

TaskEventEmitter = Callable[..., None]


def apply_task_ops(
    task_ops: TaskOps,
    *,
    trace_id: str | None,
    create_task: Callable[[TaskCreateInput], TaskRecord],
    attach_plan: Callable[[str, PlanDraft], PlanRecord],
    step_update: Callable[[str, str, StepUpdateInput], PlanRecord],
    transition_task: Callable[[str, TaskStatus], TaskRecord],
    emit: TaskEventEmitter,
    unsupported_error: type[Exception],
) -> list[str]:
    started = perf_counter()
    touched: list[str] = []
    try:
        for op in task_ops.ops:
            if isinstance(op, TaskCreateOp):
                created = create_task(op.input)
                touched.append(created.task_id)
                continue
            if isinstance(op, TaskAttachPlanOp):
                attach_plan(op.task_id, op.plan)
                touched.append(op.task_id)
                continue
            if isinstance(op, TaskStepUpdateOp):
                step_update(op.task_id, op.step_id, op.input)
                touched.append(op.task_id)
                continue
            if isinstance(op, TaskStatusTransitionOp):
                transition_task(op.task_id, op.status)
                touched.append(op.task_id)
                continue
            raise unsupported_error(f"unsupported operation payload: {op!r}")
    finally:
        emit(
            "task.ops.applied",
            trace_id=trace_id,
            payload={
                "op_count": len(task_ops.ops),
                "touched_count": len(touched),
                "duration_ms": int((perf_counter() - started) * 1000),
            },
        )
    return touched
