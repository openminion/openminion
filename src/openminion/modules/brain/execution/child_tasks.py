from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.brain.schemas import BudgetCounters, WorkingState

if TYPE_CHECKING:
    from openminion.modules.brain.execution.loop_contracts import (
        ExecutionContext,
        ExecutionResult,
    )


class FailureAction(str, Enum):
    ABORT = "abort"
    CONTINUE = "continue"


class SubtaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subtask_id: str = ""
    goal: str = Field(..., min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    constraints: str = ""
    suggested_mode: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    priority: int = 0


class DecomposeControlSubtask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    suggested_mode: str | None = None
    priority: int = 0


class DecomposeControlPayload(BaseModel):
    """Model-visible decompose tool payload.

    Empty ``subtasks`` is a typed no-op: the model called the control tool but
    declined to decompose. Runtime handling must not invent subtasks.
    """

    model_config = ConfigDict(extra="forbid")

    subtasks: list[DecomposeControlSubtask] = Field(default_factory=list)


class DecomposePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subtasks: list[SubtaskSpec] = Field(..., min_length=2)


class SubtaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subtask_id: str = Field(..., min_length=1)
    goal: str = Field(..., min_length=1)
    status: Literal["completed", "failed", "skipped", "cancelled"]
    mode_used: str = Field(..., min_length=1)
    output: str = ""
    error: str | None = None
    tokens_used: int = Field(default=0, ge=0)


class ChildTaskResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    subtask_id: str = Field(..., min_length=1)
    task_id: str | None = None
    result: SubtaskResult
    was_promoted: bool


@dataclass(slots=True)
class ChildContext:
    prompt: str
    goal: str
    summary: str = ""
    constraints: list[str] | None = None
    active_skill_id: str | None = None
    delegation_context: Any | None = None


SubtaskExecutor = Callable[[SubtaskSpec, BudgetCounters, int, int], ChildTaskResult]


@runtime_checkable
class ExecutionStrategy(Protocol):
    def execute(
        self,
        *,
        ctx: ExecutionContext,
        subtasks: list[SubtaskSpec],
        budgets: list[BudgetCounters],
        run_subtask: SubtaskExecutor,
        failure_policy: FailurePolicy,
        progress_monitor: ProgressMonitor,
        cancellation_policy: CancellationPolicy,
    ) -> list[ChildTaskResult]: ...


@runtime_checkable
class BudgetAllocator(Protocol):
    def allocate(
        self,
        *,
        budget: BudgetCounters,
        subtask_count: int,
    ) -> list[BudgetCounters]: ...


@runtime_checkable
class SubtaskModeResolver(Protocol):
    def resolve(
        self,
        *,
        subtask: SubtaskSpec,
        available_routes: list[str],
    ) -> str: ...


@runtime_checkable
class ResultSynthesizer(Protocol):
    def synthesize(
        self,
        *,
        ctx: ExecutionContext,
        results: list[SubtaskResult],
    ) -> ExecutionResult: ...


@runtime_checkable
class ChildTaskPromoter(Protocol):
    def should_promote(self, subtask: SubtaskSpec) -> bool: ...

    def promote(
        self,
        subtask: SubtaskSpec,
        parent_task_id: str,
        task_service: Any,
    ) -> str: ...


@runtime_checkable
class TaskWaitPolicy(Protocol):
    def wait_for_child(
        self,
        task_id: str,
        task_service: Any,
        timeout_ms: int | None,
    ) -> ChildTaskResult: ...


@runtime_checkable
class ChildResultCollector(Protocol):
    def collect(self, results: list[ChildTaskResult]) -> list[SubtaskResult]: ...


@runtime_checkable
class FailurePolicy(Protocol):
    def on_failure(
        self,
        *,
        subtask: SubtaskSpec,
        result: ChildTaskResult,
    ) -> FailureAction: ...


@runtime_checkable
class ContextInheritancePolicy(Protocol):
    def build_child_context(
        self,
        *,
        parent_state: WorkingState,
        subtask: SubtaskSpec,
    ) -> ChildContext: ...


@runtime_checkable
class ProgressMonitor(Protocol):
    def is_stalled(
        self,
        *,
        results: list[ChildTaskResult],
        attempts: int,
    ) -> bool: ...


@runtime_checkable
class CancellationPolicy(Protocol):
    def should_cancel(
        self,
        *,
        ctx: ExecutionContext,
        results: list[ChildTaskResult],
        attempts: int,
    ) -> bool: ...


__all__ = [
    "BudgetAllocator",
    "CancellationPolicy",
    "ChildContext",
    "ChildResultCollector",
    "ChildTaskPromoter",
    "ChildTaskResult",
    "ContextInheritancePolicy",
    "DecomposeControlPayload",
    "DecomposeControlSubtask",
    "DecomposePayload",
    "ExecutionStrategy",
    "FailureAction",
    "FailurePolicy",
    "ProgressMonitor",
    "ResultSynthesizer",
    "SubtaskExecutor",
    "SubtaskModeResolver",
    "SubtaskResult",
    "SubtaskSpec",
    "TaskWaitPolicy",
]
