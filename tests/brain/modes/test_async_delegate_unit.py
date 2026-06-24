from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.brain.execution.loop_contracts import ExecutionResult
from openminion.modules.brain.execution.targets.delegated.handler import DelegateMode
from openminion.modules.brain.execution.targets.delegated.contracts import (
    DelegatePayload,
)
from openminion.modules.brain.execution.targets.delegated.strategies import (
    AsyncJobStrategy,
    DefaultAsyncCancellationPolicy,
    DirectStatusMapper,
    PollingResumeStrategy,
    SummaryInheritancePolicy,
    SyncCommandStrategy,
)
from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    JobHandle,
)
from openminion.modules.task import TaskLifecycleState, TaskManager


def _ctx(*, a2a_api: Any | None = None, task_manager: TaskManager | None = None):
    commands: list[Any] = []
    ctx = SimpleNamespace(
        state=SimpleNamespace(
            session_id="s-async-delegate",
            agent_id="router-agent",
            trace_id="t-async-delegate",
            budgets_remaining=BudgetCounters(
                ticks=5,
                tool_calls=5,
                a2a_calls=2,
                tokens=1000,
                time_ms=10000,
            ),
        ),
        user_input="delegate this",
        act_command=lambda *, command: (
            commands.append(command)
            or ActionResult(
                command_id=command.command_id,
                status="success",
                summary="async delegation started",
            ),
            JobHandle(
                task_id="job-1",
                command_id=command.command_id,
                provider="a2actl",
                status="running",
            ),
        ),
        _services=SimpleNamespace(
            runner=SimpleNamespace(a2a_api=a2a_api, task_manager=task_manager)
        ),
    )
    return ctx, commands


def test_async_job_strategy_builds_expect_async_command_and_returns_job() -> None:
    ctx, commands = _ctx()
    strategy = AsyncJobStrategy()

    execution = strategy.execute(
        ctx=ctx,
        payload=DelegatePayload(
            target_agent_id="agent.research",
            goal="research topic x",
        ),
        resolved_agent_id="agent.research",
        delegation_context=SummaryInheritancePolicy().build_child_context(
            parent_state=SimpleNamespace(
                goal="parent goal",
                last_result=None,
                constraints=[],
                active_skill_id=None,
            ),
            subtask=SimpleNamespace(goal="research topic x", constraints=""),
        ),
        idempotency_key="delegate-async-1",
    )

    assert len(commands) == 1
    assert execution.command.expect_async is True
    assert execution.job is not None
    assert execution.job.task_id == "job-1"


@pytest.mark.parametrize(
    ("status", "expected_status", "expected_message"),
    [
        ("running", "pending", "in progress"),
        ("pending", "pending", "pending"),
        ("completed", "done", "Delegated answer"),
        ("failed", "error", "delegate exploded"),
        ("cancelled", "stopped", "Delegation cancelled"),
    ],
)
def test_polling_resume_strategy_maps_job_states(
    status: str,
    expected_status: str,
    expected_message: str,
) -> None:
    a2a_api = SimpleNamespace(
        poll_task=lambda **kwargs: {
            "status": status,
            "summary": "Delegation cancelled" if status == "cancelled" else "",
            "outputs": {"answer": "Delegated answer"} if status == "completed" else {},
            "error": {"code": "A2A_FAILED", "message": "delegate exploded"}
            if status == "failed"
            else (
                {"code": "A2A_CANCELLED", "message": "Delegation cancelled"}
                if status == "cancelled"
                else {}
            ),
        }
    )
    ctx, _commands = _ctx(a2a_api=a2a_api)
    strategy = PollingResumeStrategy(status_mapper=DirectStatusMapper())

    result = strategy.check(
        ctx=ctx,
        payload=DelegatePayload(target_agent_id="agent.research", goal="research"),
        resolved_agent_id="agent.research",
        job_id="job-1",
    )

    assert isinstance(result, ExecutionResult)
    assert result.status == expected_status
    assert expected_message in str(result.message)


def test_async_cancellation_policy_cancels_job_and_updates_task_record() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        record = manager.create_linked_task(
            linked_job_id="job-1",
            agent_id="router-agent",
            metadata={"job_id": "job-1"},
        )
        a2a_api = SimpleNamespace(
            cancel_task=lambda **kwargs: {
                "status": "cancelled",
                "summary": "Delegation cancelled.",
                "error": {
                    "code": "A2A_JOB_CANCELLED",
                    "message": "Delegation cancelled.",
                },
            }
        )
        ctx, _commands = _ctx(a2a_api=a2a_api, task_manager=manager)
        policy = DefaultAsyncCancellationPolicy()

        result = policy.cancel_async(
            ctx=ctx,
            job_id="job-1",
            task_id=record.task_id,
        )

        updated = manager.get_task(record.task_id)
        assert result.status == "stopped"
        assert updated is not None
        assert updated.state == TaskLifecycleState.CANCELLED


def test_delegate_mode_has_resume_only_when_async_strategy_is_active() -> None:
    assert DelegateMode(strategy=AsyncJobStrategy()).has_resume is True
    assert DelegateMode(strategy=SyncCommandStrategy()).has_resume is False


def test_direct_status_mapper_preserves_existing_terminal_mappings() -> None:
    ctx, _commands = _ctx()
    mapper = DirectStatusMapper()

    done = mapper.map_result(
        ctx=ctx,
        payload=DelegatePayload(target_agent_id="agent.research", goal="research"),
        resolved_agent_id="agent.research",
        action_result=ActionResult(
            command_id="cmd-1",
            status="success",
            summary="sync success",
        ),
    )
    waiting = mapper.map_result(
        ctx=ctx,
        payload=DelegatePayload(target_agent_id="agent.research", goal="research"),
        resolved_agent_id="agent.research",
        action_result=ActionResult(
            command_id="cmd-2",
            status="needs_user",
            summary="need user",
        ),
    )

    assert done.status == "done"
    assert waiting.status == "waiting_user"
