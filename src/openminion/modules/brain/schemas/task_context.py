"""Bounded current-request conditions persisted with WorkingState."""

from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ..constants import TASK_CONTEXT_MAX_CHARS


class TaskCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    key: str = Field(min_length=1, max_length=80, pattern=r"^[\w.-]+$")
    quote: str = Field(min_length=1, max_length=1024)
    question_quote: str | None = Field(default=None, min_length=1, max_length=2048)


class TaskContextUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: Literal["start", "update", "replace", "cancel"]
    expected_revision: int = Field(ge=0)
    user_quote: str = Field(min_length=1, max_length=2048)
    objective: str | None = Field(default=None, min_length=1, max_length=2048)
    set_conditions: list[TaskCondition] = Field(default_factory=list, max_length=128)
    remove_conditions: list[str] = Field(default_factory=list, max_length=128)


class ResolvedTaskContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    revision: int = Field(ge=1)
    task_id: str = Field(
        default_factory=lambda: uuid4().hex, min_length=1, max_length=64
    )
    objective: str = Field(min_length=1, max_length=TASK_CONTEXT_MAX_CHARS)
    objective_kind: Literal["resolved", "verbatim"] = "resolved"
    conditions: dict[str, str] = Field(default_factory=dict, max_length=128)
    condition_questions: dict[str, str] = Field(default_factory=dict, max_length=128)
    sources: dict[str, str] = Field(default_factory=dict, max_length=129)
    consumed_input: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def bounded_state(self) -> "ResolvedTaskContext":
        if set(self.condition_questions) - set(self.conditions):
            raise ValueError("Answer context has no condition")
        size = len(self.objective) + sum(
            len(key) + len(value) for key, value in self.conditions.items()
        )
        size += sum(len(value) for value in self.condition_questions.values())
        if size > TASK_CONTEXT_MAX_CHARS:
            raise ValueError(
                "Current task conditions exceed the bounded context capacity"
            )
        return self
