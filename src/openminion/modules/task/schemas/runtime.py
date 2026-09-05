from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field

from .common import _StrictTaskModel


class ResumePointer(_StrictTaskModel):
    """Stable cursor for pausing and resuming execution exactly once."""

    task_id: str
    plan_id: str
    step_id: str
    attempt: int = Field(default=1, ge=1)
    trace_id: str
    turn_id: str | None = None
    pack_id: str | None = None


class PendingAction(_StrictTaskModel):
    """Approval checkpoint returned by runtime when policy blocks execution."""

    pending_action_id: str = Field(default_factory=lambda: f"pa_{uuid4().hex[:10]}")
    policy_request_id: str
    state: Literal["NEEDS_APPROVAL"] = "NEEDS_APPROVAL"
    reason: str | None = None
    cursor: ResumePointer
    created_at: datetime
    resolved_at: datetime | None = None
    decision_id: str | None = None


class DecisionDigest(_StrictTaskModel):
    """Short execution summary suitable for prompt injection."""

    mode: Literal["PLAN", "EXECUTE"]
    current_task_id: str | None = None
    current_step_id: str | None = None
    summary: str
