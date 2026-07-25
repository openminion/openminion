# ruff: noqa: F403,F405
from .common import *
from .common import _StrictTaskModel


class TaskDigestTask(_StrictTaskModel):
    """Compact task line for context packs."""

    task_id: str
    title: str
    status: TaskStatus
    next_step_id: str | None = None
    next_step_title: str | None = None
    due_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskDigest(_StrictTaskModel):
    """Bounded task snapshot used by openminion-context."""

    agent_id: str
    session_id: str
    generated_at: datetime
    tasks_ready: list[TaskDigestTask] = Field(default_factory=list)
    tasks_active: list[TaskDigestTask] = Field(default_factory=list)
    current_task: TaskDigestTask | None = None
    blockers: list[str] = Field(default_factory=list)
    max_items: int = Field(default=5, ge=1)
