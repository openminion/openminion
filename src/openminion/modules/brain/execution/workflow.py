from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..constants import (
    BRAIN_STATE_JOB_PENDING,
    BRAIN_STATE_WAITING_USER,
)
from ..diagnostics.transitions import transition
from ..schemas import ActionResult, FixItem, JobHandle
from .loop_contracts import ExecutionContext, ExecutionResult


SUPPORTED_STRUCTURED_FIX_ACTIONS = frozenset(
    {
        "replan",
        "ask_user",
        "retry_with_precondition",
        "skip_next_step",
    }
)


@dataclass(slots=True)
class WorkflowStep:
    value: Any
    index: int
    total: int


@dataclass(slots=True)
class WorkflowPlan:
    steps: list[Any]
    cursor: int = 0
    initial_result: ExecutionResult | None = None

    def has_next_step(self) -> bool:
        return 0 <= self.cursor < len(self.steps)

    def current_step(self) -> WorkflowStep:
        return WorkflowStep(
            value=self.steps[self.cursor],
            index=self.cursor,
            total=len(self.steps),
        )

    def advance(self) -> None:
        self.cursor += 1


@dataclass(slots=True)
class StepResult:
    step: WorkflowStep
    action_result: ActionResult | None = None
    job: JobHandle | None = None
    reflect_report: Any | None = None
    mode_result: ExecutionResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    is_pending: bool = False


@dataclass(slots=True)
class StepJudgment:
    disposition: str
    mode_result: ExecutionResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class WorkflowGovernanceAction:
    action: str
    title: str
    target_command_id: str | None = None
    question: str = ""
    precondition: str = ""
    confidence: float = 0.0


def extract_workflow_governance_actions(
    fixes: list[FixItem] | None,
) -> list[WorkflowGovernanceAction]:
    actions: list[WorkflowGovernanceAction] = []
    for fix in fixes or []:
        action = str(getattr(fix, "action", "") or "").strip().lower()
        if action not in SUPPORTED_STRUCTURED_FIX_ACTIONS:
            continue
        actions.append(
            WorkflowGovernanceAction(
                action=action,
                title=str(getattr(fix, "title", "") or "").strip() or action,
                target_command_id=str(
                    getattr(fix, "target_command_id", "") or ""
                ).strip()
                or None,
                question=str(getattr(fix, "question", "") or "").strip(),
                precondition=str(getattr(fix, "precondition", "") or "").strip(),
                confidence=float(getattr(fix, "confidence", 0.0) or 0.0),
            )
        )
    return actions


class WorkflowMode(ABC):
    mode_name: str

    @abstractmethod
    def initialize(self, ctx: ExecutionContext) -> WorkflowPlan: ...

    @abstractmethod
    def execute_step(self, ctx: ExecutionContext, step: WorkflowStep) -> StepResult: ...

    @abstractmethod
    def judge_step(
        self,
        ctx: ExecutionContext,
        step: WorkflowStep,
        result: StepResult,
    ) -> StepJudgment: ...

    @abstractmethod
    def finalize(self, ctx: ExecutionContext) -> ExecutionResult: ...

    def reconcile_pending(self, ctx: ExecutionContext) -> list[StepResult]:
        del ctx
        return []

    def resume(self, ctx: ExecutionContext) -> WorkflowPlan | None:
        del ctx
        return None

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        workflow = self.resume(ctx)
        if workflow is not None and workflow.initial_result is not None:
            return workflow.initial_result
        if workflow is not None and self._has_pending_jobs(ctx):
            pending_results = self.reconcile_pending(ctx)
            if not pending_results and self._has_pending_jobs(ctx):
                return self._pause_for_pending(ctx)
            for result in pending_results:
                if result.mode_result is not None:
                    return result.mode_result
                judgment = self.judge_step(ctx, result.step, result)
                if judgment.mode_result is not None:
                    return judgment.mode_result
                if judgment.disposition == "replan":
                    workflow = None
                    break
                if judgment.disposition == "close":
                    return self.finalize(ctx)
                workflow.advance()
                ctx.state.cursor = workflow.cursor
        if workflow is None:
            workflow = self.initialize(ctx)
            if workflow.initial_result is not None:
                return workflow.initial_result
            ctx.state.cursor = workflow.cursor
        while workflow.has_next_step():
            if self._budget_exhausted(ctx):
                return self._budget_exit(ctx)
            step = workflow.current_step()
            result = self.execute_step(ctx, step)
            if result.mode_result is not None:
                return result.mode_result
            if result.is_pending:
                self._register_pending_job(ctx, step, result)
                return self._pause_for_pending(ctx, step=step, result=result)
            judgment = self.judge_step(ctx, step, result)
            if judgment.mode_result is not None:
                return judgment.mode_result
            if judgment.disposition == "replan":
                workflow = self.initialize(ctx)
                if workflow.initial_result is not None:
                    return workflow.initial_result
                ctx.state.cursor = workflow.cursor
                continue
            if judgment.disposition == "close":
                break
            workflow.advance()
            ctx.state.cursor = workflow.cursor
        return self.finalize(ctx)

    def _has_pending_jobs(self, ctx: ExecutionContext) -> bool:
        return bool(getattr(ctx.state, "pending_jobs", []) or [])

    def _budget_exhausted(self, ctx: ExecutionContext) -> bool:
        budgets = getattr(ctx.state, "budgets_remaining", None)
        if budgets is None:
            return False
        return (
            int(getattr(budgets, "ticks", 1) or 0) <= 0
            or int(getattr(budgets, "time_ms", 1) or 0) <= 0
        )

    def _budget_exit(self, ctx: ExecutionContext) -> ExecutionResult:
        transition(ctx.state, "budget_exhausted", logger=ctx.logger)
        return ExecutionResult.from_step_output(
            ctx.respond(
                message="Turn budget exhausted. Narrow scope or continue in a new turn.",
                status=BRAIN_STATE_WAITING_USER,
            )
        )

    def _register_pending_job(
        self,
        ctx: ExecutionContext,
        step: WorkflowStep,
        result: StepResult,
    ) -> None:
        del step
        job = result.job
        if job is None:
            return
        existing = {
            str(getattr(item, "task_id", "") or "")
            for item in getattr(ctx.state, "pending_jobs", []) or []
        }
        if str(getattr(job, "task_id", "") or "") not in existing:
            ctx.state.pending_jobs.append(job)
        transition(ctx.state, "job_scheduled", logger=ctx.logger)

    def _pause_for_pending(
        self,
        ctx: ExecutionContext,
        *,
        step: WorkflowStep | None = None,
        result: StepResult | None = None,
    ) -> ExecutionResult:
        del step
        job = result.job if result is not None else None
        if job is None and getattr(ctx.state, "pending_jobs", None):
            job = ctx.state.pending_jobs[-1]
        message = (
            f"Started async job {job.task_id}; status is {job.status}."
            if job is not None
            else "Waiting for an async job to complete."
        )
        transition(ctx.state, "job_scheduled", logger=ctx.logger)
        return ExecutionResult.from_step_output(
            ctx.respond(message=message, status=BRAIN_STATE_JOB_PENDING)
        )


__all__ = [
    "SUPPORTED_STRUCTURED_FIX_ACTIONS",
    "StepJudgment",
    "StepResult",
    "WorkflowMode",
    "WorkflowGovernanceAction",
    "WorkflowPlan",
    "WorkflowStep",
    "extract_workflow_governance_actions",
]
