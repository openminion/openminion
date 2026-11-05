"""Bounded, user-grounded state for one request, persisted with WorkingState."""

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..constants import TASK_CONTEXT_MAX_CHARS


class TaskConditionOwner(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scope: Literal["conversation", "task", "subtask"] = "task"
    owner_id: str | None = Field(default=None, min_length=1, max_length=64)
    scope_quote: str | None = Field(default=None, min_length=1, max_length=2048)


class TaskCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    key: str = Field(min_length=1, max_length=80, pattern=r"^[\w.-]+$")
    quote: str = Field(min_length=1, max_length=1024)
    question_quote: str | None = Field(default=None, min_length=1, max_length=2048)
    scope: Literal["conversation", "task", "subtask"] | None = None
    owner_id: str | None = Field(default=None, min_length=1, max_length=64)
    scope_quote: str | None = Field(default=None, min_length=1, max_length=2048)


class TaskConditionRestore(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    key: str = Field(min_length=1, max_length=80, pattern=r"^[\w.-]+$")
    reference: str = Field(min_length=64, max_length=64)


class TaskContextUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: Literal["start", "update", "replace", "cancel", "pause", "complete", "resume"]
    expected_revision: int = Field(ge=0)
    user_quote: str = Field(min_length=1, max_length=2048)
    objective: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        description="Model-resolved task purpose from current task and latest user input; ground the change with user_quote.",
    )
    set_conditions: list[TaskCondition] = Field(default_factory=list, max_length=128)
    remove_conditions: list[str] = Field(default_factory=list, max_length=128)
    target_task_id: str | None = Field(default=None, min_length=1, max_length=64)
    target_revision: int | None = Field(default=None, ge=1)
    conversation_scope_quote: str | None = Field(default=None, min_length=1, max_length=2048)
    history_revisions: list[int] = Field(default_factory=list, max_length=8)
    restore_conditions: list[TaskConditionRestore] = Field(default_factory=list, max_length=16)



class TaskConditionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    quote: str = Field(min_length=1, max_length=1024)
    question_quote: str | None = Field(default=None, min_length=1, max_length=2048)
    source: str = Field(min_length=64, max_length=64)


class ResolvedTaskContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    revision: int = Field(ge=1)
    task_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=64)
    lifecycle: Literal["active", "paused", "completed", "cancelled"] = "active"
    condition_owners: dict[str, TaskConditionOwner] = Field(default_factory=dict, max_length=128)
    subtasks: dict[str, Literal["pending", "active", "completed", "cancelled"]] = Field(default_factory=dict, max_length=128)
    restored_from: dict[str, int] = Field(default_factory=dict, max_length=128)
    history_available: bool = False
    history_context: dict[str, str] = Field(default_factory=dict, max_length=8)
    objective: str = Field(min_length=1, max_length=TASK_CONTEXT_MAX_CHARS)
    objective_kind: Literal["resolved", "verbatim"] = "resolved"
    conditions: dict[str, str] = Field(default_factory=dict, max_length=128)
    condition_questions: dict[str, str] = Field(default_factory=dict, max_length=128)
    condition_history: dict[str, list[TaskConditionEvidence]] = Field(
        default_factory=dict, max_length=128
    )
    sources: dict[str, str] = Field(default_factory=dict, max_length=129)
    consumed_input: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def bounded_state(self) -> "ResolvedTaskContext":
        if set(self.restored_from) - set(self.conditions) or any(value < 1 for value in self.restored_from.values()):
            raise ValueError("Invalid historical restoration provenance")
        if set(self.condition_owners) - set(self.conditions):
            raise ValueError("Condition owner has no condition")
        for owner in self.condition_owners.values():
            if owner.scope == "subtask" and owner.owner_id not in self.subtasks:
                raise ValueError("Unknown subtask condition owner")
            if owner.scope != "subtask" and owner.owner_id is not None:
                raise ValueError("Only subtask conditions take an explicit owner id")
        size = sum(len(key) + len(value) for key, value in self.history_context.items())
        size += sum(len(owner.scope_quote or "") for owner in self.condition_owners.values())
        size += len(self.objective) + sum(
            len(key) + len(value) for key, value in self.conditions.items()
        )
        size += sum(len(value) for value in self.condition_questions.values())
        for key, history in self.condition_history.items():
            if key not in self.conditions or len(history) > 128:
                raise ValueError("Invalid condition history owner or capacity")
            size += len(key) + sum(
                len(item.quote) + len(item.question_quote or "") for item in history
            )
        if size > TASK_CONTEXT_MAX_CHARS:
            raise ValueError(
                "Current task conditions exceed the bounded context capacity"
            )
        return self
