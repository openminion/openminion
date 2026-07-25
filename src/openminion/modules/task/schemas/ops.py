# ruff: noqa: F403,F405
from .common import *
from .common import _StrictTaskModel
from .plan import PlanDraft, StepUpdateInput
from .task import TaskCreateInput

class TaskCreateOp(_StrictTaskModel):
    op: Literal["task.create"] = "task.create"
    input: TaskCreateInput

class TaskAttachPlanOp(_StrictTaskModel):
    op: Literal["task.attach_plan"] = "task.attach_plan"
    task_id: str
    plan: PlanDraft

class TaskStepUpdateOp(_StrictTaskModel):
    op: Literal["task.step_update"] = "task.step_update"
    task_id: str
    step_id: str
    input: StepUpdateInput

class TaskStatusTransitionOp(_StrictTaskModel):
    op: Literal["task.status_transition"] = "task.status_transition"
    task_id: str
    status: TaskStatus

TaskOp = Annotated[
    TaskCreateOp | TaskAttachPlanOp | TaskStepUpdateOp | TaskStatusTransitionOp,
    Field(discriminator="op"),
]


class TaskOps(_StrictTaskModel):
    """Batch of durable operations emitted by planner/executor."""

    ops: list[TaskOp] = Field(default_factory=list)
