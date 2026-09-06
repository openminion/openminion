from datetime import datetime

from pydantic import Field

from .common import _StrictTaskModel


class TaskEvent(_StrictTaskModel):
    """Task event contract."""

    type: str
    at: datetime
    task_id: str | None = None
    plan_id: str | None = None
    step_id: str | None = None
    trace_id: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)
