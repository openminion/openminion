"""Research-mode integration and regression tests (RSM-09).

Covers: registry compatibility, task-backed contract preservation, bounded
child execution, migration compatibility, and regression against other
task-backed infrastructure. Updated for the canonical iterative model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.execution.dispatch import maybe_resume_task_backed_direct
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.loop.strategies.research import (
    RESEARCH_MODE,
    ResearchMode,
)
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    Plan,
    WorkingState,
)
from openminion.modules.brain.runner.tick import run_step
from openminion.modules.task import TaskLifecycleState, TaskManager
from tests.brain.runner_test_support import _profile


@dataclass
class _FakeRunner:
    task_manager: TaskManager
    profile: Any = field(
        default_factory=lambda: SimpleNamespace(agent_id="router-agent")
    )


@dataclass
class _FakeServices:
    runner: _FakeRunner
    statuses: list[dict[str, Any]]
    plan_calls: list[str]

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

    def direct_response(self, *, user_input, decision=None):
        del user_input, decision
        return ""

    def plan(self, *, state, user_input, logger, decision=None):
        del state, logger, decision
        text = str(user_input or "")
        self.plan_calls.append(text)
        return Plan(objective="mock plan result.", steps=[])

    def approve_command(self, *, state, command, logger):
        del state, logger
        return command

    def act_command(self, *, state, command, logger):
        del state, command, logger
        raise AssertionError("research mode should not call ctx.act_command()")

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
        return False

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

    def create_task(
        self,
        *,
        session_id: str,
        mode_name: str,
        goal: str,
        agent_id: str | None,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ):
        return self.runner.task_manager.create_task(
            session_id=session_id,
            mode_name=mode_name,
            goal=goal,
            agent_id=agent_id,
            metadata=metadata,
            task_id=task_id,
        )

    def get_task(self, *, task_id: str):
        return self.runner.task_manager.get_task(task_id)

    def list_open_tasks_for_session(
        self,
        *,
        session_id: str,
        mode_name: str | None = None,
        limit: int = 100,
    ):
        return self.runner.task_manager.list_open_tasks_for_session(
            session_id,
            mode_name=mode_name,
            limit=limit,
        )

    def save_checkpoint(
        self,
        *,
        task_id: str,
        checkpoint_id: str,
        state: dict[str, Any],
    ) -> None:
        self.runner.task_manager.save_checkpoint(task_id, checkpoint_id, state)

    def get_latest_checkpoint(self, *, task_id: str):
        return self.runner.task_manager.get_latest_checkpoint(task_id)

    def list_checkpoints(self, *, task_id: str):
        return self.runner.task_manager.list_checkpoints(task_id)

    def update_task_progress(self, *, task_id: str, progress: dict[str, Any]) -> None:
        self.runner.task_manager.update_progress(task_id, progress)

    def transition_task(
        self,
        *,
        task_id: str,
        to_state: TaskLifecycleState,
        failure_reason: str | None = None,
    ):
        return self.runner.task_manager.transition_task(
            task_id=task_id,
            to_state=to_state,
            failure_reason=failure_reason,
        )

    def emit_status(self, **kwargs) -> None:
        self.statuses.append(dict(kwargs))


def _state(*, session_id: str = "s-research", ticks: int = 20) -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="router-agent",
        goal="Research the adoption of WebAssembly",
        budgets_remaining=BudgetCounters(
            ticks=ticks,
            tool_calls=10,
            a2a_calls=2,
            tokens=5000,
            time_ms=120000,
        ),
        trace_id=f"trace-{session_id}",
    )


def _ctx(task_manager: TaskManager, *, state: WorkingState | None = None):
    working_state = state or _state()
    services = _FakeServices(
        runner=_FakeRunner(task_manager=task_manager),
        statuses=[],
        plan_calls=[],
    )
    decision = SimpleNamespace(
        mode=RESEARCH_MODE,
        confidence=0.9,
        reason_code="research_request",
        research_query="Research the adoption of WebAssembly",
        research_scope="",
        objective="Research the adoption of WebAssembly",
        sub_intents=[],
        rationale="",
        question=None,
        answer=None,
    )
    logger = SimpleNamespace(events=[], emit=lambda *args, **kwargs: None)
    return (
        ExecutionContext(
            state=working_state,
            decision=decision,
            user_input="Research the adoption of WebAssembly",
            logger=logger,
            options=SimpleNamespace(),
            llm_adapter=None,
            command_executor=SimpleNamespace(),
            _services=services,
        ),
        services,
    )


def _make_mode(max_iterations: int = 3) -> ResearchMode:
    mode = ResearchMode()
    mode.apply_mode_config(
        config=SimpleNamespace(
            checkpoint_interval=1,
            max_resume_count=10,
            max_research_iterations=max_iterations,
        ),
        runner=None,
        profile=None,
    )
    return mode


# Regression: core loop runs all iterations and completes


def test_research_mode_runs_all_iterations_and_completes() -> None:
    """Canonical execution: runs max_iterations iterations and returns done."""
    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, services = _ctx(task_manager)
        mode = _make_mode(max_iterations=3)

        result = mode.execute(ctx)

        assert result.status == "done"
        # 3 iterations (child plan) + 1 synthesis plan call = 4.
        assert len(services.plan_calls) == 4
        task_id = str(ctx.state.task_backed_task_id)
        assert task_id
        checkpoints = task_manager.list_checkpoints(task_id)
        assert checkpoints == [
            f"{RESEARCH_MODE}-{task_id}-cursor-1",
            f"{RESEARCH_MODE}-{task_id}-cursor-2",
            f"{RESEARCH_MODE}-{task_id}-cursor-3",
        ]
        record = task_manager.get_task(task_id)
        assert record is not None
        assert record.state == TaskLifecycleState.DONE
        # Last reported phase reflects last completed iteration (0-indexed).
        assert record.metadata["progress"]["phase"] == "iteration_2"


def test_research_mode_resume_continues_from_latest_checkpoint() -> None:
    """Pause after budget exhaustion; resume picks up at the correct iteration."""
    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        initial_state = _state(ticks=1)
        ctx, services = _ctx(task_manager, state=initial_state)
        mode = _make_mode(max_iterations=3)

        paused = mode.execute(ctx)

        assert paused.status == "waiting_user"
        # Exactly 1 iteration ran before pause.
        assert len(services.plan_calls) == 1
        checkpoint_id = str(ctx.state.task_backed_checkpoint_id or "")
        assert checkpoint_id.endswith("-cursor-1")

        resumed_state = ctx.state.model_copy(deep=True)
        resumed_state.budgets_remaining.ticks = 20
        resumed_ctx, resumed_services = _ctx(task_manager, state=resumed_state)
        resumed_ctx.state.task_backed_task_id = ctx.state.task_backed_task_id
        resumed_ctx.state.task_backed_checkpoint_id = (
            ctx.state.task_backed_checkpoint_id
        )
        resumed_ctx.state.task_backed_resume_state = mode.resume(
            resumed_ctx,
            checkpoint_id,
        )

        resumed = mode.execute(resumed_ctx)

        assert resumed.status == "done"
        # 2 remaining iterations + 1 synthesis = 3 plan calls.
        assert len(resumed_services.plan_calls) == 3
        loaded = task_manager.get_task(str(ctx.state.task_backed_task_id))
        assert loaded is not None
        assert loaded.state == TaskLifecycleState.DONE
        assert int(loaded.metadata["progress"]["resume_count"]) == 1


# Regression: cancel preserves findings


def test_research_mode_cancel_preserves_partial_results_and_checkpoint() -> None:
    """Cancel after pause preserves findings and transitions task to cancelled."""
    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, services = _ctx(task_manager, state=_state(ticks=1))
        mode = _make_mode(max_iterations=3)

        paused = mode.execute(ctx)
        cancelled = mode.cancel(ctx, "User cancelled the research.")

        assert paused.status == "waiting_user"
        assert cancelled.status == "stopped"
        assert "Partial findings" in str(cancelled.message or "")
        loaded = task_manager.get_task(str(ctx.state.task_backed_task_id))
        assert loaded is not None
        assert loaded.state == TaskLifecycleState.CANCELLED
        checkpoints = task_manager.list_checkpoints(str(ctx.state.task_backed_task_id))
        assert checkpoints
        assert checkpoints[-1].endswith("-cursor-1")


# Negative regression: invalid checkpoint


def test_research_mode_invalid_checkpoint_fails_closed() -> None:
    """Resuming with a wrong checkpoint ID returns error status."""
    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx, _ = _ctx(task_manager, state=_state(ticks=1))
        mode = _make_mode(max_iterations=3)
        mode.execute(ctx)

        resumed_state = ctx.state.model_copy(deep=True)
        resumed_ctx, _ = _ctx(task_manager, state=resumed_state)
        resumed_ctx.state.task_backed_task_id = ctx.state.task_backed_task_id
        resumed_ctx.state.task_backed_resume_state = mode.resume(
            resumed_ctx,
            "missing-checkpoint",
        )

        result = mode.execute(resumed_ctx)

        assert result.status == "error"


def test_run_step_resumes_open_task_backed_mode_before_normal_dispatch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = LocalSessionStore(root / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            tool_api=LocalToolAdapter(),
            a2a_api=LocalA2AAdapter(),
            memory_api=LocalMemoryAdapter(root / "memory"),
            policy_api=LocalPolicyAdapter(),
            options=RunnerOptions(metactl_enabled=False),
        )
        runner.task_manager = TaskManager.for_lifecycle_db(
            db_path=root / "task" / "tasks.db"
        )
        record = runner.task_manager.create_task(
            session_id="s-run-step-research",
            mode_name=RESEARCH_MODE,
            goal="Research resumable task-backed modes",
            agent_id="router-agent",
        )
        runner.task_manager.save_checkpoint(
            record.task_id,
            f"research-{record.task_id}-phase-1",
            {
                "task_id": record.task_id,
                "objective": "Research resumable task-backed modes",
                "next_phase_index": 1,
                "partial_results": ["Gather sources complete."],
                "phase_outputs": {"gather_sources": "Gather sources complete."},
                "resume_count": 0,
            },
        )

        output = run_step(
            runner,
            session_id="s-run-step-research",
            user_input="continue the research",
        )

        assert output.status == "done"


def test_task_backed_resume_does_not_hijack_unrelated_new_input() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = LocalSessionStore(root / "sessions")
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=LocalContextAdapter(session_store=session),
            tool_api=LocalToolAdapter(),
            a2a_api=LocalA2AAdapter(),
            memory_api=LocalMemoryAdapter(root / "memory"),
            policy_api=LocalPolicyAdapter(),
            options=RunnerOptions(metactl_enabled=False),
        )
        runner.task_manager = TaskManager.for_lifecycle_db(
            db_path=root / "task" / "tasks.db"
        )
        runner.task_manager.create_task(
            session_id="s-run-step-research",
            mode_name=RESEARCH_MODE,
            goal="Research resumable task-backed modes",
            agent_id="router-agent",
        )
        state = WorkingState(
            session_id="s-run-step-research",
            agent_id="router-agent",
            goal="Research resumable task-backed modes",
            budgets_remaining=BudgetCounters(
                ticks=10,
                tool_calls=10,
                a2a_calls=0,
                tokens=5000,
                time_ms=120000,
            ),
        )

        resumed = maybe_resume_task_backed_direct(
            runner,
            state=state,
            user_input="what time is now?",
            logger=SimpleNamespace(events=[], emit=lambda *args, **kwargs: None),
        )

        assert resumed is None
        assert state.task_backed_task_id is None
        assert state.task_backed_checkpoint_id is None
        assert state.task_backed_resume_state == {}
