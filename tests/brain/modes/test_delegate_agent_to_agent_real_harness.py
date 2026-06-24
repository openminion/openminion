from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_SUCCESS,
)
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.execution.targets.delegated.handler import DelegateMode
from openminion.modules.brain.execution.targets.delegated.strategies import (
    AsyncJobStrategy,
)
from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    WorkingState,
)
from openminion.modules.task import TaskLifecycleState, TaskManager
from tests.helpers.real_a2a_delegate_harness import RealA2ADelegateHarness


@dataclass
class _Runner:
    agent_registry: dict[str, dict[str, str]]
    a2a_api: Any
    task_manager: TaskManager | None = None


@dataclass
class _Services:
    runner: _Runner
    harness: RealA2ADelegateHarness
    command_calls: list[Any]
    statuses: list[dict[str, Any]]

    def save_state(self, *, state: WorkingState) -> None:
        del state

    def emit_phase_status(self, *, state: WorkingState, **kwargs: Any) -> None:
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
    ) -> Any:
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

    def direct_response(self, *, user_input: str, decision: Any) -> str:
        del user_input, decision
        return ""

    def plan(self, *, state: WorkingState, user_input: str, logger: Any, decision=None):
        del state, user_input, logger, decision
        raise AssertionError("DA2A real harness should not call ctx.plan()")

    def approve_command(self, *, state: WorkingState, command: Any, logger: Any) -> Any:
        del state, logger
        return command

    def act_command(self, *, state: WorkingState, command: Any, logger: Any):
        del logger
        self.command_calls.append(command)
        return self.harness.action_from_command(
            command=command,
            session_id=state.session_id,
            trace_id=str(state.trace_id or ""),
        )

    def transition_task(
        self,
        *,
        task_id: str,
        to_state: str,
        failure_reason: str | None = None,
    ):
        manager = self.runner.task_manager
        if manager is None:
            raise AssertionError("task manager unavailable")
        return manager.transition_task(
            task_id=task_id,
            to_state=to_state,
            failure_reason=failure_reason,
        )

    def assess_plan_feasibility(
        self, *, state: WorkingState, user_input: str, logger: Any
    ):
        del state, user_input, logger
        return None

    def evaluate_meta(self, **kwargs: Any):
        del kwargs
        return None

    def apply_meta_directive(self, **kwargs: Any) -> None:
        del kwargs

    def meta_override_response(self, **kwargs: Any):
        del kwargs
        return None

    def meta_tool_restriction_reason(self, *, command: Any, directive: Any):
        del command, directive
        return None

    def command_has_side_effects(self, *, command: Any) -> bool:
        del command
        return True

    def resolve_verification_mode(self, *, current: Any, candidate: Any) -> Any:
        return candidate if candidate is not None else current

    def verify(
        self,
        *,
        state: WorkingState,
        command: Any,
        action_result: ActionResult,
        mode: str,
        logger: Any,
    ) -> bool:
        del state, command, action_result, mode, logger
        return True

    def improve(self, *, state: WorkingState, report: Any, logger: Any) -> None:
        del state, report, logger

    def compact(self, *, state: WorkingState, logger: Any, content: str = "") -> None:
        del state, logger, content

    def evaluate_turn_closure(self, **kwargs: Any):
        del kwargs
        return None

    def apply_closure_judgment(self, *, state: WorkingState, judgment: Any) -> str:
        del state, judgment
        return "close"

    def extract_success_memories(self, **kwargs: Any) -> list[Any]:
        del kwargs
        return []


def _state(*, session_id: str, trace_id: str) -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="hello-agent",
        goal="Delegate to target agent",
        budgets_remaining=BudgetCounters(
            ticks=8,
            tool_calls=5,
            a2a_calls=3,
            tokens=5000,
            time_ms=120000,
        ),
        trace_id=trace_id,
    )


def _ctx(
    *,
    harness: RealA2ADelegateHarness,
    target_agent_id: str,
    goal: str,
    session_id: str = "s-da2a-real",
    trace_id: str = "trace-da2a-real",
    task_manager: TaskManager | None = None,
) -> tuple[ExecutionContext, _Services]:
    registry = {
        item["agent_id"]: {"state": item.get("status", "online")}
        for item in harness.list_agents()
    }
    services = _Services(
        runner=_Runner(
            agent_registry=registry,
            a2a_api=harness.adapter,
            task_manager=task_manager,
        ),
        harness=harness,
        command_calls=[],
        statuses=[],
    )
    decision = SimpleNamespace(
        mode="delegate",
        confidence=0.9,
        reason_code="da2a_structured_delegate_probe",
        target_agent_id=target_agent_id,
        target_capability=None,
        goal=goal,
        constraints="return structured marker",
        synthesize_result=False,
        timeout_ms=2500,
        sub_intents=[],
        rationale="",
        question=None,
        answer=None,
    )
    logger = SimpleNamespace(emit=lambda *args, **kwargs: None)
    ctx = ExecutionContext(
        state=_state(session_id=session_id, trace_id=trace_id),
        decision=decision,
        user_input="delegate using exact target id",
        logger=logger,
        options=SimpleNamespace(decompose_cancel_requested=False),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=services,
    )
    return ctx, services


def test_real_a2a_harness_registers_targets_and_rejects_unknown(tmp_path: Path) -> None:
    harness = RealA2ADelegateHarness(home_root=tmp_path)
    try:
        harness.register_target("planner-safe", marker="planner-marker")
        harness.register_target("ops-safe", marker="ops-marker")

        agents = {item["agent_id"] for item in harness.list_agents()}
        assert {"planner-safe", "ops-safe"}.issubset(agents)

        result = harness.call(
            target_agent_id="planner-safe",
            goal="produce a safe plan",
            session_id="s-da2a-harness",
            trace_id="trace-da2a-harness",
        )
        assert result["status"] == BRAIN_ACTION_STATUS_SUCCESS
        assert result["outputs"]["target_marker"] == "planner-marker"
        assert result["outputs"]["lineage"]["target_agent_id"] == "planner-safe"
        assert result["outputs"] != {"goal": "produce a safe plan"}

        missing = harness.call(
            target_agent_id="ghost-agent",
            goal="this must fail",
            session_id="s-da2a-harness",
            trace_id="trace-da2a-harness-missing",
        )
        assert missing["status"] == BRAIN_ACTION_STATUS_FAILED
        assert "error" in missing
    finally:
        harness.close()


def test_delegate_mode_sync_uses_real_a2a_target_execution(tmp_path: Path) -> None:
    harness = RealA2ADelegateHarness(home_root=tmp_path)
    try:
        harness.register_target("planner-safe", marker="planner-sync")
        ctx, services = _ctx(
            harness=harness,
            target_agent_id="planner-safe",
            goal="build a bounded plan",
            trace_id="trace-da2a-sync",
        )

        result = DelegateMode().execute(ctx)

        assert result.status == "done"
        assert result.action_result is not None
        assert result.action_result.outputs["target_marker"] == "planner-sync"
        assert result.action_result.outputs["received_goal"] == "build a bounded plan"
        lineage = result.action_result.outputs["lineage"]
        assert lineage["from_agent"] == "hello-agent"
        assert lineage["target_agent_id"] == "planner-safe"
        assert lineage["trace_id"] == "trace-da2a-sync"
        assert len(services.command_calls) == 1
        assert services.command_calls[0].target_agent_id == "planner-safe"
        assert harness.records[0].target_agent_id == "planner-safe"
        audit_statuses = {
            item["status"] for item in harness.trace_events("trace-da2a-sync")
        }
        assert "CALL_RECEIVED" in audit_statuses
        assert "SUCCESS" in audit_statuses
    finally:
        harness.close()


def test_delegate_mode_async_uses_real_a2a_job_lifecycle(tmp_path: Path) -> None:
    harness = RealA2ADelegateHarness(home_root=tmp_path)
    try:
        harness.register_target("ops-safe", marker="ops-async")
        with tempfile.TemporaryDirectory() as tmp:
            task_manager = TaskManager.for_lifecycle_db(
                db_path=Path(tmp) / "task" / "tasks.db"
            )
            ctx, services = _ctx(
                harness=harness,
                target_agent_id="ops-safe",
                goal="run safe ops check",
                session_id="s-da2a-async",
                trace_id="trace-da2a-async",
                task_manager=task_manager,
            )
            mode = DelegateMode(strategy=AsyncJobStrategy())

            initial = mode.execute(ctx)
            assert initial.status == "job_pending"
            assert ctx.state.delegation_job_id
            assert ctx.state.delegation_task_id
            linked = task_manager.get_task(str(ctx.state.delegation_task_id))
            assert linked is not None
            assert linked.metadata["job_id"] == ctx.state.delegation_job_id
            assert linked.metadata["target_agent_id"] == "ops-safe"
            assert linked.metadata["kind"] == "delegation"

            completed = harness.wait_for_job(str(ctx.state.delegation_job_id))
            assert completed["status"] == "completed"
            resumed = mode.resume(ctx)

            assert resumed.status == "done"
            assert resumed.action_result is not None
            assert resumed.action_result.outputs["target_marker"] == "ops-async"
            assert (
                resumed.action_result.outputs["lineage"]["target_agent_id"]
                == "ops-safe"
            )
            assert harness.records[0].target_agent_id == "ops-safe"
            updated = task_manager.get_task(linked.task_id)
            assert updated is not None
            assert updated.state == TaskLifecycleState.DONE
            assert services.command_calls[0].expect_async is True
            audit_statuses = {
                item["status"] for item in harness.trace_events("trace-da2a-async")
            }
            assert "JOB_QUEUED" in audit_statuses
            assert "SUCCESS" in audit_statuses
    finally:
        harness.close()
