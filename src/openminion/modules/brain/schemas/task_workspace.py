"""Durable foreground task ownership, archived tasks and execution provenance."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .task_context import ResolvedTaskContext, TaskConditionEvidence


class TaskExecutionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1)
    subtask_id: str | None = Field(default=None, min_length=1, max_length=64)


class DetachedTaskExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    binding: TaskExecutionBinding
    handle: dict[str, Any]


class TaskWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    journal_head: str | None = None
    detached_jobs: dict[str, DetachedTaskExecution] = Field(
        default_factory=dict, max_length=128
    )
    archived: dict[str, ResolvedTaskContext] = Field(
        default_factory=dict, max_length=64
    )
    conversation_conditions: dict[str, TaskConditionEvidence] = Field(
        default_factory=dict, max_length=128
    )

    @model_validator(mode="after")
    def bounded_workspace(self) -> "TaskWorkspace":
        if any(key != task.task_id for key, task in self.archived.items()):
            raise ValueError("Archived task identity mismatch")
        size = sum(len(task.model_dump_json()) for task in self.archived.values())
        size += sum(
            len(key) + len(item.model_dump_json())
            for key, item in self.conversation_conditions.items()
        )
        size += sum(
            len(key) + len(item.model_dump_json())
            for key, item in self.detached_jobs.items()
        )
        if size > 512_000:
            raise ValueError(
                "Task workspace capacity exceeded; start a separate session"
            )
        return self
