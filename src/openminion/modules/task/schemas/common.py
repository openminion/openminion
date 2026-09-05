from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class _StrictTaskModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    WAITING = "WAITING"
    DONE = "DONE"
    CANCELED = "CANCELED"


class PlanStepStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    DONE = "DONE"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
