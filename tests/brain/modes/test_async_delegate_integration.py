from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.execution.targets.delegated.handler import DelegateMode
from openminion.modules.brain.execution.targets.delegated.strategies import (
    AsyncJobStrategy,
)
from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    JobHandle,
    WorkingState,
)
from openminion.modules.task import TaskLifecycleState, TaskManager


class _FakeA2AAPI:
    def __init__(self) -> None:
        self.poll_response: dict[str, Any] = {
            "status": "running",
            "summary": "still working",
        }
        self.poll_calls: list[str] = []
        self.cancel_calls: list[str] = []

    def poll_task(
        self, *, task_id: str, session_id: str, trace_id: str
    ) -> dict[str, Any]:
        del session_id, trace_id
        self.poll_calls.append(task_id)
        return dict(self.poll_response)

    def cancel_task(
        self, *, task_id: str, session_id: str, trace_id: str
    ) -> dict[str, Any]:
        del session_id, trace_id
        self.cancel_calls.append(task_id)
        return {
            "status": "cancelled",
            "summary": "Delegation cancelled.",
            "error": {
                "code": "A2A_JOB_CANCELLED",
                "message": "Delegation cancelled.",
            },
        }


@dataclass
class _FakeRunner:
    agent_registry: Any
    a2a_api: Any
    task_manager: TaskManager


@dataclass
class _FakeServices:
    runner: _FakeRunner
    command_calls: list[Any]
    statuses: list[dict[str, Any]]

    def save_state(self, *, state: WorkingState) -> None:
        del state

    def emit_phase_status(self, *, state: WorkingState, **kwargs) -> None:
        del state
        self.statuses.append(dict(kwargs))

    def respond_with_meta(
        self,
        *,
        state: WorkingState,
        logger: Any,
        message: str,
        status: str,
        action_result: ActionResult | None = None,
        kind: str = "assistant",
    ):
        del logger, kind
        state.status = status
        if action_result is not None:
            state.last_result = action_result
        return SimpleNamespace(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
        )

    def direct_response(self, *, user_input, decision):
        del user_input, decision
        return ""

    def plan(self, *, state, user_input, logger, decision=None):
        del state, user_input, logger, decision
        raise AssertionError("delegate async integration should not call ctx.plan()")

    def approve_command(self, *, state, command, logger):
        del state, logger
        return command

    def act_command(self, *, state, command, logger):
        del state, logger
        self.command_calls.append(command)
        return (
            ActionResult(
                command_id=command.command_id,
                status="success",
                summary="Async delegate started.",
            ),
            JobHandle(
                task_id="job-1",
                command_id=command.command_id,
                provider="a2actl",
                status="running",
            ),
        )

    def assess_plan_feasibility(self, *, state, user_input, logger):
        del state, user_input, logger
        return None

    def evaluate_meta(self, **kwargs):
        del kwargs
        return None

    def apply_meta_directive(self, **kwargs):
        del kwargs

    def meta_override_response(self, **kwargs):
        del kwargs
        return None

    def meta_tool_restriction_reason(self, *, command, directive):
        del command, directive
        return None

    def command_has_side_effects(self, *, command):
        del command
        return True

    def resolve_verification_mode(self, *, current, candidate):
        return candidate if candidate is not None else current

    def verify(self, *, state, command, action_result, mode, logger):
        del state, command, action_result, mode, logger
        return True

    def improve(self, *, state, report, logger):
        del state, report, logger

    def compact(self, *, state, logger, content=""):
        del state, logger, content

    def evaluate_turn_closure(self, **kwargs):
        del kwargs
        return None

    def apply_closure_judgment(self, *, state, judgment):
        del state, judgment
        return "close"

    def extract_success_memories(self, **kwargs):
        del kwargs
        return []

    def transition_task(
        self,
        *,
        task_id: str,
        to_state: str,
        failure_reason: str | None = None,
    ):
        return self.runner.task_manager.transition_task(
            task_id=task_id,
            to_state=to_state,
            failure_reason=failure_reason,
        )


def _state() -> WorkingState:
    return WorkingState(
        session_id="s-delegate-async",
        agent_id="router-agent",
        goal="Delegate research task",
        budgets_remaining=BudgetCounters(
            ticks=8,
            tool_calls=5,
            a2a_calls=2,
            tokens=5000,
            time_ms=120000,
        ),
        trace_id="trace-delegate-async",
    )


def _ctx(task_manager: TaskManager, a2a_api: _FakeA2AAPI):
    services = _FakeServices(
        runner=_FakeRunner(
            agent_registry={"agent.research": {"state": "healthy"}},
            a2a_api=a2a_api,
            task_manager=task_manager,
        ),
        command_calls=[],
        statuses=[],
    )
    decision = SimpleNamespace(
        mode="delegate",
        confidence=0.9,
        reason_code="delegate_specialist",
        target_agent_id="agent.research",
        target_capability=None,
        goal="Investigate topic X",
        constraints="be concise",
        synthesize_result=False,
        timeout_ms=2500,
        sub_intents=[],
        rationale="",
        question=None,
        answer=None,
    )
    logger = SimpleNamespace(emit=lambda *args, **kwargs: None)
    return ExecutionContext(
        state=_state(),
        decision=decision,
        user_input="ask the research agent to investigate topic x",
        logger=logger,
        options=SimpleNamespace(decompose_cancel_requested=False),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=services,
    ), services


def test_async_delegate_flow_starts_pending_then_resumes_to_done() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(
            db_path=Path(tmp) / "task" / "tasks.db"
        )
        a2a_api = _FakeA2AAPI()
        ctx, services = _ctx(task_manager, a2a_api)
        mode = DelegateMode(strategy=AsyncJobStrategy())

        initial = mode.execute(ctx)

        assert initial.status == "job_pending"
        assert len(services.command_calls) == 1
        assert services.command_calls[0].expect_async is True
        assert ctx.state.delegation_job_id == "job-1"
        assert ctx.state.delegation_task_id
        linked = task_manager.get_task(str(ctx.state.delegation_task_id))
        assert linked is not None
        assert linked.metadata["job_id"] == "job-1"
        assert linked.metadata["target_agent_id"] == "agent.research"
        assert linked.metadata["kind"] == "delegation"
        assert linked.metadata["parent_session_id"] == ctx.state.session_id

        a2a_api.poll_response = {
            "status": "completed",
            "outputs": {"answer": "Research complete."},
        }
        resumed = mode.resume(ctx)

        assert resumed.status == "done"
        assert resumed.message == "Research complete."
        assert a2a_api.poll_calls == ["job-1"]
        updated = task_manager.get_task(linked.task_id)
        assert updated is not None
        assert updated.state == TaskLifecycleState.DONE
        async_states = [
            item.get("mode_state")
            for item in services.statuses
            if str(item.get("mode_label", "")).startswith("[delegated-async]")
        ]
        assert {"job_started", "polling", "delegate_result"}.issubset(set(async_states))


def test_async_delegate_cancel_updates_job_and_task_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(
            db_path=Path(tmp) / "task" / "tasks.db"
        )
        a2a_api = _FakeA2AAPI()
        ctx, _services = _ctx(task_manager, a2a_api)
        mode = DelegateMode(strategy=AsyncJobStrategy())

        initial = mode.execute(ctx)
        assert initial.status == "job_pending"

        cancelled = mode._cancellation.cancel_async(
            ctx=ctx,
            job_id=str(ctx.state.delegation_job_id),
            task_id=str(ctx.state.delegation_task_id),
        )

        record = task_manager.get_task(str(ctx.state.delegation_task_id))
        assert cancelled.status == "stopped"
        assert a2a_api.cancel_calls == ["job-1"]
        assert record is not None
        assert record.state == TaskLifecycleState.CANCELLED
