# ruff: noqa: F403,F405
from .common import *
from .common import _StrictTaskModel


class TaskCreateInput(_StrictTaskModel):
    """Payload for creating a durable task."""

    task_id: str | None = None
    title: str = Field(min_length=1)
    description: str | None = None
    due_at: datetime | None = None
    scheduled_at: datetime | None = None
    wait_at: datetime | None = None
    labels: list[str] = Field(default_factory=list)
    created_by_mode: str | None = None


class TaskRecord(_StrictTaskModel):
    """Canonical task entity persisted by the task module."""

    task_id: str
    title: str
    description: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    due_at: datetime | None = None
    scheduled_at: datetime | None = None
    wait_at: datetime | None = None
    labels: list[str] = Field(default_factory=list)
    created_by_mode: str | None = None
    executing_mode: str | None = None
    current_plan_id: str | None = None
    next_step_id: str | None = None
    created_at: datetime
    updated_at: datetime
