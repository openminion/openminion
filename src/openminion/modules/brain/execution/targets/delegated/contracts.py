from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.brain.schemas import (
    ActionResult,
    DelegationContext,
    JobHandle,
    WorkingState,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.child_tasks import (
    CancellationPolicy as BaseCancellationPolicy,
    ContextInheritancePolicy,
    FailurePolicy,
    ResultSynthesizer,
)

if TYPE_CHECKING:
    from openminion.modules.brain.schemas.commands import AgentCommand


class ClarificationAction(str, Enum):
    FAIL = "fail"
    BUBBLE_UP = "bubble_up"
    AUTO_ANSWER = "auto_answer"


class DelegatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_agent_id: str = Field(..., min_length=1)
    target_capability: str | None = None
    goal: str = Field(..., min_length=1)
    constraints: str = ""
    synthesize_result: bool = False
    timeout_ms: int | None = Field(default=None, ge=1)
    delegation_context: DelegationContext | None = None


CancellationPolicy = BaseCancellationPolicy


@dataclass(slots=True)
class DelegationExecution:
    action_result: ActionResult
    command: "AgentCommand"
    job: JobHandle | None = None


@runtime_checkable
class AgentResolver(Protocol):
    def resolve(
        self,
        *,
        target_agent_id: str | None,
        target_capability: str | None,
        registry: Any,
    ) -> str: ...


@runtime_checkable
class DelegationStrategy(Protocol):
    def execute(
        self,
        *,
        ctx: ExecutionContext,
        payload: DelegatePayload,
        resolved_agent_id: str,
        delegation_context: Any,
        idempotency_key: str,
    ) -> DelegationExecution: ...


@runtime_checkable
class A2AStatusMapper(Protocol):
    def map_result(
        self,
        *,
        ctx: ExecutionContext,
        payload: DelegatePayload,
        resolved_agent_id: str,
        action_result: ActionResult,
    ) -> ExecutionResult: ...

    def map_job_status(
        self,
        *,
        ctx: ExecutionContext,
        payload: DelegatePayload,
        resolved_agent_id: str,
        job_id: str,
        job_status: str,
        summary: str = "",
        outputs: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> ExecutionResult: ...


@runtime_checkable
class ClarificationPolicy(Protocol):
    def on_clarification_needed(
        self,
        *,
        delegate_result: ActionResult,
        original_context: ExecutionContext,
    ) -> ClarificationAction: ...


@runtime_checkable
class AgentDiscoveryProvider(Protocol):
    def get_registry(self, *, ctx: ExecutionContext) -> Any: ...


@runtime_checkable
class AsyncCancellationPolicy(BaseCancellationPolicy, Protocol):
    def cancel_async(
        self,
        *,
        ctx: ExecutionContext,
        job_id: str,
        task_id: str | None = None,
    ) -> ExecutionResult: ...


@runtime_checkable
class DelegationTaskTracker(Protocol):
    def create_linked_task(
        self,
        *,
        ctx: ExecutionContext,
        job_id: str,
        target_agent_id: str,
        goal: str,
    ) -> str: ...

    def mark_done(self, *, task_id: str) -> None: ...

    def mark_failed(self, *, task_id: str, message: str) -> None: ...

    def mark_cancelled(self, *, task_id: str) -> None: ...


@runtime_checkable
class IdempotencyKeyGenerator(Protocol):
    def generate(
        self,
        *,
        session_id: str,
        trace_id: str,
        goal: str,
    ) -> str: ...


@runtime_checkable
class DelegationObserver(Protocol):
    def emit(
        self,
        *,
        ctx: ExecutionContext,
        mode_state: str,
        label: str,
        target_agent_id: str | None = None,
    ) -> None: ...


@runtime_checkable
class BudgetPolicy(Protocol):
    def check_budget(self, *, state: WorkingState) -> bool: ...

    def deduct(self, *, state: WorkingState) -> None: ...


__all__ = [
    "A2AStatusMapper",
    "AgentDiscoveryProvider",
    "AgentResolver",
    "AsyncCancellationPolicy",
    "BudgetPolicy",
    "CancellationPolicy",
    "ClarificationAction",
    "ClarificationPolicy",
    "DelegationExecution",
    "ContextInheritancePolicy",
    "DelegatePayload",
    "DelegationObserver",
    "DelegationStrategy",
    "DelegationTaskTracker",
    "FailurePolicy",
    "IdempotencyKeyGenerator",
    "ResultSynthesizer",
]
