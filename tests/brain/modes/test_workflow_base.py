from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.workflow import (
    StepJudgment,
    StepResult,
    WorkflowMode,
    WorkflowPlan,
    WorkflowStep,
)
from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    JobHandle,
    RespondDecision,
    StepOutput,
    WorkingState,
)


@dataclass
class _FakeServices:
    def save_state(self, *, state: WorkingState) -> None:
        del state

    def emit_phase_status(self, *, state: WorkingState, **kwargs) -> None:
        del state, kwargs

    def respond_with_meta(
        self,
        *,
        state: WorkingState,
        logger: Any,
        message: str,
        status: str,
        action_result=None,
        kind="assistant",
    ):
        del logger, kind
        return StepOutput(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
        )

    def direct_response(self, *, user_input, decision):
        del user_input, decision
        return "ok"


def _ctx(*, ticks: int = 5, time_ms: int = 10_000) -> ExecutionContext:
    state = WorkingState(
        session_id="s-workflow",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=ticks,
            tool_calls=5,
            a2a_calls=5,
            tokens=1000,
            time_ms=time_ms,
        ),
    )
    return ExecutionContext(
        state=state,
        decision=RespondDecision(
            confidence=0.9,
            reason_code="workflow",
            respond_kind="answer",
            answer="ok",
        ),
        user_input="go",
        logger=SimpleNamespace(),
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=_FakeServices(),
    )


class _WorkflowUnderTest(WorkflowMode):
    mode_name = "workflow-test"

    def __init__(
        self,
        *,
        initialize_plan: WorkflowPlan | None = None,
        resume_plan: WorkflowPlan | None = None,
        step_results: list[StepResult] | None = None,
        judgments: list[StepJudgment] | None = None,
        pending_results: list[StepResult] | None = None,
        final_result: ExecutionResult | None = None,
    ) -> None:
        self.calls: list[str] = []
        self._initialize_plan = initialize_plan or WorkflowPlan(["a", "b", "c"])
        self._resume_plan = resume_plan
        self._step_results = deque(step_results or [])
        self._judgments = deque(judgments or [])
        self._pending_results = list(pending_results or [])
        self._final_result = final_result or ExecutionResult(
            status="done",
            working_state=_ctx().state,
            message="final",
        )

    def initialize(self, ctx: ExecutionContext) -> WorkflowPlan:
        self.calls.append("initialize")
        return WorkflowPlan(
            list(self._initialize_plan.steps), cursor=self._initialize_plan.cursor
        )

    def execute_step(self, ctx: ExecutionContext, step: WorkflowStep) -> StepResult:
        self.calls.append(f"execute:{step.index}")
        if self._step_results:
            return self._step_results.popleft()
        return StepResult(
            step=step,
            action_result=ActionResult(
                command_id="cmd", status="success", summary="ok"
            ),
        )

    def judge_step(
        self, ctx: ExecutionContext, step: WorkflowStep, result: StepResult
    ) -> StepJudgment:
        del result
        self.calls.append(f"judge:{step.index}")
        if self._judgments:
            return self._judgments.popleft()
        return StepJudgment(disposition="continue")

    def finalize(self, ctx: ExecutionContext) -> ExecutionResult:
        self.calls.append("finalize")
        return ExecutionResult(
            status=self._final_result.status,
            working_state=ctx.state,
            message=self._final_result.message,
        )

    def reconcile_pending(self, ctx: ExecutionContext) -> list[StepResult]:
        self.calls.append("reconcile")
        return list(self._pending_results)

    def resume(self, ctx: ExecutionContext) -> WorkflowPlan | None:
        self.calls.append("resume")
        if self._resume_plan is None:
            return None
        return WorkflowPlan(
            list(self._resume_plan.steps), cursor=self._resume_plan.cursor
        )


def _success_result(step: WorkflowStep) -> StepResult:
    return StepResult(
        step=step,
        action_result=ActionResult(
            command_id=f"cmd-{step.index}", status="success", summary="ok"
        ),
    )


def test_workflow_base_initializes_fresh_plan() -> None:
    ctx = _ctx()
    workflow = _WorkflowUnderTest()
    result = workflow.execute(ctx)
    assert result.status == "done"
    assert workflow.calls[0:2] == ["resume", "initialize"]


def test_workflow_base_resumes_from_existing_cursor() -> None:
    ctx = _ctx()
    workflow = _WorkflowUnderTest(
        resume_plan=WorkflowPlan(["a", "b", "c", "d", "e"], cursor=2)
    )
    workflow.execute(ctx)
    assert workflow.calls[0] == "resume"
    assert "initialize" not in workflow.calls
    assert "execute:2" in workflow.calls


def test_workflow_base_budget_exit() -> None:
    ctx = _ctx(ticks=0)
    workflow = _WorkflowUnderTest()
    result = workflow.execute(ctx)
    assert result.status == "waiting_user"
    assert "budget exhausted" in str(result.message).lower()


def test_workflow_base_replans_when_judgment_requests_it() -> None:
    ctx = _ctx()
    first_step = WorkflowStep("a", 0, 1)
    workflow = _WorkflowUnderTest(
        step_results=[_success_result(first_step)],
        judgments=[
            StepJudgment(disposition="replan"),
            StepJudgment(disposition="close"),
        ],
    )
    workflow.execute(ctx)
    assert workflow.calls.count("initialize") >= 2


def test_workflow_base_closes_when_judgment_requests_it() -> None:
    ctx = _ctx()
    first_step = WorkflowStep("a", 0, 1)
    workflow = _WorkflowUnderTest(
        step_results=[_success_result(first_step)],
        judgments=[StepJudgment(disposition="close")],
    )
    result = workflow.execute(ctx)
    assert result.status == "done"
    assert workflow.calls[-1] == "finalize"


def test_workflow_base_pauses_for_async_dispatch() -> None:
    ctx = _ctx()
    pending_job = JobHandle(
        task_id="job-1", command_id="cmd-1", provider="tool", status="running"
    )
    step = WorkflowStep("a", 0, 1)
    workflow = _WorkflowUnderTest(
        step_results=[StepResult(step=step, job=pending_job, is_pending=True)],
    )
    result = workflow.execute(ctx)
    assert result.status == "job_pending"
    assert ctx.state.pending_jobs[-1].task_id == "job-1"


def test_workflow_base_reconciles_completed_pending_results() -> None:
    ctx = _ctx()
    ctx.state.pending_jobs.append(
        JobHandle(
            task_id="job-1", command_id="cmd-1", provider="tool", status="running"
        )
    )
    step = WorkflowStep("a", 0, 1)
    workflow = _WorkflowUnderTest(
        resume_plan=WorkflowPlan(["a"], cursor=0),
        pending_results=[_success_result(step)],
        judgments=[StepJudgment(disposition="close")],
    )
    result = workflow.execute(ctx)
    assert result.status == "done"
    assert workflow.calls[0:2] == ["resume", "reconcile"]


def test_workflow_base_stays_paused_when_pending_jobs_unfinished() -> None:
    ctx = _ctx()
    ctx.state.pending_jobs.append(
        JobHandle(
            task_id="job-1", command_id="cmd-1", provider="tool", status="running"
        )
    )
    workflow = _WorkflowUnderTest(
        resume_plan=WorkflowPlan(["a"], cursor=0),
        pending_results=[],
    )
    result = workflow.execute(ctx)
    assert result.status == "job_pending"
    assert workflow.calls == ["resume", "reconcile"]


def test_workflow_base_replans_after_pending_reconcile() -> None:
    ctx = _ctx()
    ctx.state.pending_jobs.append(
        JobHandle(
            task_id="job-1", command_id="cmd-1", provider="tool", status="running"
        )
    )
    step = WorkflowStep("a", 0, 1)
    workflow = _WorkflowUnderTest(
        resume_plan=WorkflowPlan(["a"], cursor=0),
        pending_results=[_success_result(step)],
        judgments=[
            StepJudgment(disposition="replan"),
            StepJudgment(disposition="close"),
        ],
    )
    workflow.execute(ctx)
    assert workflow.calls.count("initialize") >= 1


def test_workflow_base_call_order_is_predictable() -> None:
    ctx = _ctx()
    workflow = _WorkflowUnderTest()
    workflow.execute(ctx)
    assert workflow.calls[0:5] == [
        "resume",
        "initialize",
        "execute:0",
        "judge:0",
        "execute:1",
    ]
