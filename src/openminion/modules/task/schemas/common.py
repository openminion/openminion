# ruff: noqa: F401
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


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
