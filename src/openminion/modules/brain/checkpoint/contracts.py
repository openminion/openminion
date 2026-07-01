from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)


@runtime_checkable
class CheckpointConsumer(Protocol):
    CHECKPOINT_VERSION: int

    def snapshot_state(self) -> dict[str, Any]:
        """Return a JSON-serializable payload to persist."""

    def restore_state(self, payload: dict[str, Any]) -> None:
        """Restore internal state from a persisted payload."""


class CheckpointEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(..., ge=1)
    owner: str = Field(..., min_length=1)
    cursor: int = Field(default=0, ge=0)
    timestamp_ms: int = Field(..., ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: str = Field(..., min_length=1)
    completion_pct: float = Field(..., ge=0.0, le=1.0)
    partial_results: list[str] = Field(default_factory=list)
    last_checkpoint_id: str | None = None
    message: str = ""


@runtime_checkable
class TaskBackedModeContract(Protocol):
    def checkpoint(self, ctx: ExecutionContext, state: dict[str, Any]) -> str: ...

    def resume(self, ctx: ExecutionContext, checkpoint_id: str) -> dict[str, Any]: ...

    def report_progress(
        self, ctx: ExecutionContext, progress: TaskProgress
    ) -> None: ...

    def emit_partial_result(self, ctx: ExecutionContext, result: str) -> None: ...

    def cancel(self, ctx: ExecutionContext, reason: str) -> ExecutionResult: ...


__all__ = [
    "CheckpointConsumer",
    "CheckpointEnvelope",
    "TaskBackedModeContract",
    "TaskProgress",
]
