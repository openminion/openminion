from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.checkpoint.contracts import (
    TaskBackedModeContract,
)
from openminion.modules.brain.execution.workflow import (
    StepJudgment,
    StepResult,
    WorkflowMode,
    WorkflowPlan,
    WorkflowStep,
)
from openminion.modules.brain.schemas import ActionResult, BudgetCounters, WorkingState
from openminion.modules.brain.checkpoint import (
    CheckpointConsumer,
    CheckpointEnvelope,
    CheckpointManager,
    CheckpointMixin,
    SimpleCheckpointMixin,
)
from openminion.modules.task import TaskLifecycleState, TaskManager


@dataclass
class _FakeServices:
    task_manager: TaskManager
    statuses: list[dict[str, Any]] = field(default_factory=list)

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
        del logger
        state.status = status
        return SimpleNamespace(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
            kind=kind,
        )

    def create_task(self, **kwargs):
        return self.task_manager.create_task(**kwargs)

    def get_task(self, *, task_id: str):
        return self.task_manager.get_task(task_id)

    def list_open_tasks_for_session(self, **kwargs):
        return self.task_manager.list_open_tasks_for_session(**kwargs)

    def save_checkpoint(
        self, *, task_id: str, checkpoint_id: str, state: dict[str, Any]
    ):
        self.task_manager.save_checkpoint(task_id, checkpoint_id, state)

    def get_latest_checkpoint(self, *, task_id: str):
        return self.task_manager.get_latest_checkpoint(task_id)

    def list_checkpoints(self, *, task_id: str):
        return self.task_manager.list_checkpoints(task_id)

    def update_task_progress(self, *, task_id: str, progress: dict[str, Any]) -> None:
        self.task_manager.update_progress(task_id, progress)

    def transition_task(
        self, *, task_id: str, to_state: str, failure_reason: str | None = None
    ):
        return self.task_manager.transition_task(
            task_id=task_id,
            to_state=to_state,
            failure_reason=failure_reason,
        )

    def direct_response(self, **kwargs):
        del kwargs
        return ""

    def plan(self, **kwargs):
        del kwargs
        return SimpleNamespace(objective="", steps=[])

    def approve_command(self, **kwargs):
        return kwargs["command"]

    def act_command(self, **kwargs):
        del kwargs
        return ActionResult(command_id="c", status="success", summary="ok"), None

    def assess_plan_feasibility(self, **kwargs):
        del kwargs
        return None

    def evaluate_meta(self, **kwargs):
        del kwargs
        return None

    def apply_meta_directive(self, **kwargs):
        del kwargs

    def meta_override_response(self, **kwargs):
        del kwargs
        return None

    def meta_tool_restriction_reason(self, **kwargs):
        del kwargs
        return None

    def command_has_side_effects(self, **kwargs):
        del kwargs
        return False

    def resolve_verification_mode(self, *, current, candidate):
        return candidate if candidate is not None else current

    def verify(self, **kwargs):
        del kwargs
        return True

    def improve(self, **kwargs):
        del kwargs

    def compact(self, **kwargs):
        del kwargs

    def evaluate_turn_closure(self, **kwargs):
        del kwargs
        return None

    def apply_closure_judgment(self, **kwargs):
        del kwargs
        return "close"

    def extract_success_memories(self, **kwargs):
        del kwargs
        return []


def _state(*, session_id: str = "s-checkpoint") -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="router-agent",
        goal="checkpoint test",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=5,
            a2a_calls=1,
            tokens=1000,
            time_ms=60000,
        ),
        trace_id=f"trace-{session_id}",
    )


def _ctx(
    task_manager: TaskManager, *, state: WorkingState | None = None
) -> ExecutionContext:
    services = _FakeServices(task_manager=task_manager)
    services.runner = SimpleNamespace(task_manager=task_manager)
    return ExecutionContext(
        state=state or _state(),
        decision=SimpleNamespace(mode="dummy", objective="checkpoint test"),
        user_input="checkpoint test",
        logger=SimpleNamespace(events=[], emit=lambda *args, **kwargs: None),
        options=SimpleNamespace(),
        llm_adapter=None,
        command_executor=SimpleNamespace(),
        _services=services,
    )


class _MockConsumer:
    CHECKPOINT_VERSION = 1
    mode_name = "research"

    def __init__(self) -> None:
        self.payload = {"value": 1}

    def snapshot_state(self) -> dict[str, Any]:
        return dict(self.payload)

    def restore_state(self, payload: dict[str, Any]) -> None:
        self.payload = dict(payload)


class _VersionTwoConsumer(_MockConsumer):
    CHECKPOINT_VERSION = 2


class _DummyWorkflow(CheckpointMixin, WorkflowMode):
    CHECKPOINT_VERSION = 1
    mode_name = "dummy_workflow"
    default_config = {"checkpoint_interval": 2}

    def __init__(self) -> None:
        self.completed: list[str] = []
        self._checkpoint_interval = 2

    def snapshot_state(self) -> dict[str, Any]:
        return {"completed": list(self.completed)}

    def restore_state(self, payload: dict[str, Any]) -> None:
        self.completed = list(dict(payload or {}).get("completed", []) or [])

    def initialize(self, ctx: ExecutionContext) -> WorkflowPlan:
        self._init_checkpoint(ctx)
        if not self._checkpoint_resuming:
            self.completed = []
        return WorkflowPlan(steps=["one", "two", "three"])

    def execute_step(self, ctx: ExecutionContext, step: WorkflowStep) -> StepResult:
        del ctx
        return StepResult(step=step)

    def judge_step(
        self,
        ctx: ExecutionContext,
        step: WorkflowStep,
        result: StepResult,
    ) -> StepJudgment:
        del result
        self.completed.append(str(step.value))
        self._save_checkpoint(ctx, cursor=step.index + 1)
        return StepJudgment(disposition="continue")

    def finalize(self, ctx: ExecutionContext) -> ExecutionResult:
        self._finalize_checkpoint(ctx, terminal=True, cursor=len(self.completed))
        return ExecutionResult.from_step_output(
            ctx.respond(message="done", status="done")
        )


class _DummySimple(SimpleCheckpointMixin):
    CHECKPOINT_VERSION = 1
    mode_name = "dummy_simple"

    def __init__(self) -> None:
        self.counter = 0

    def snapshot_state(self) -> dict[str, Any]:
        return {"counter": self.counter}

    def restore_state(self, payload: dict[str, Any]) -> None:
        self.counter = int(dict(payload or {}).get("counter", 0) or 0)

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        self._init_checkpoint(ctx)
        self.counter += 1
        self._save_checkpoint(ctx, cursor=self.counter)
        return ExecutionResult.from_step_output(
            ctx.respond(message="ok", status="done")
        )


def test_checkpoint_consumer_protocol_is_runtime_checkable() -> None:
    assert isinstance(_MockConsumer(), CheckpointConsumer)


def test_checkpoint_envelope_round_trips_and_rejects_invalid_shapes() -> None:
    envelope = CheckpointEnvelope(
        version=1,
        owner="research",
        cursor=2,
        timestamp_ms=123,
        payload={"findings": ["a"]},
    )

    restored = CheckpointEnvelope.model_validate_json(envelope.model_dump_json())

    assert restored == envelope

    with pytest.raises(ValidationError):
        CheckpointEnvelope(
            version=0,
            owner="research",
            cursor=0,
            timestamp_ms=1,
            payload={},
        )
    with pytest.raises(ValidationError):
        CheckpointEnvelope(
            version=1,
            owner="",
            cursor=0,
            timestamp_ms=1,
            payload={},
        )
    with pytest.raises(ValidationError):
        CheckpointEnvelope.model_validate(
            {
                "version": 1,
                "owner": "research",
                "cursor": 0,
                "timestamp_ms": 1,
                "payload": {},
                "extra_field": True,
            }
        )


def test_checkpoint_manager_save_load_round_trip_and_deterministic_ids() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        manager = CheckpointManager(task_service=task_manager)
        consumer = _MockConsumer()
        task_id = manager.create_task(
            session_id="s-manager",
            owner="research",
            goal="checkpoint goal",
            agent_id="router-agent",
        )

        checkpoint_id = manager.save(consumer=consumer, task_id=task_id, cursor=2)
        envelope = manager.load(consumer=consumer, task_id=task_id)

        assert checkpoint_id == f"research-{task_id}-cursor-2"
        assert envelope is not None
        assert envelope.cursor == 2
        assert consumer.payload == {"value": 1}


def test_checkpoint_manager_returns_none_for_missing_or_version_mismatch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        manager = CheckpointManager(task_service=task_manager)
        task_id = manager.create_task(
            session_id="s-manager",
            owner="research",
            goal="checkpoint goal",
            agent_id="router-agent",
        )

        assert manager.load(consumer=_MockConsumer(), task_id="missing-task") is None

        manager.save(consumer=_MockConsumer(), task_id=task_id, cursor=1)
        assert manager.load(consumer=_VersionTwoConsumer(), task_id=task_id) is None


def test_workflow_checkpoint_mixin_respects_interval_and_resumes_from_cursor() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        ctx = _ctx(task_manager)
        handler = _DummyWorkflow()

        workflow = handler.initialize(ctx)
        handler.completed.append("one")
        assert handler._save_checkpoint(ctx, cursor=1) is None
        assert task_manager.list_checkpoints(str(ctx.state.task_backed_task_id)) == []

        handler.completed.append("two")
        checkpoint_id = handler._save_checkpoint(ctx, cursor=2)
        assert checkpoint_id == (
            f"dummy_workflow-{ctx.state.task_backed_task_id}-cursor-2"
        )

        resumed_state = ctx.state.model_copy(deep=True)
        resumed_ctx = _ctx(task_manager, state=resumed_state)
        resumed_ctx.state.task_backed_task_id = ctx.state.task_backed_task_id
        payload = handler.resume(resumed_ctx, checkpoint_id)

        assert payload["completed"] == ["one", "two"]

        resumed_handler = _DummyWorkflow()
        resumed_ctx.state.task_backed_resume_state = payload
        resumed_ctx.state.task_backed_task_id = ctx.state.task_backed_task_id
        resumed_workflow = resumed_handler.resume(resumed_ctx)

        assert isinstance(resumed_workflow, WorkflowPlan)
        assert resumed_workflow.cursor == 2
        assert resumed_handler.completed == ["one", "two"]
        assert workflow.steps == ["one", "two", "three"]


def test_checkpoint_mixins_finalize_and_simple_save_are_task_backed_compatible() -> (
    None
):
    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        workflow_ctx = _ctx(task_manager, state=_state(session_id="s-workflow"))
        workflow_handler = _DummyWorkflow()

        workflow_handler.initialize(workflow_ctx)
        workflow_handler.completed = ["one"]
        workflow_handler._finalize_checkpoint(workflow_ctx, terminal=True, cursor=1)

        workflow_record = task_manager.get_task(
            str(workflow_ctx.state.task_backed_task_id)
        )
        assert workflow_record is not None
        assert workflow_record.state == TaskLifecycleState.DONE

        simple_ctx = _ctx(task_manager, state=_state(session_id="s-simple"))
        simple_handler = _DummySimple()
        simple_handler.execute(simple_ctx)

        simple_record = task_manager.get_task(str(simple_ctx.state.task_backed_task_id))
        assert simple_record is not None
        assert task_manager.list_checkpoints(simple_record.task_id) == [
            f"dummy_simple-{simple_record.task_id}-cursor-1"
        ]
        assert isinstance(workflow_handler, TaskBackedModeContract)
        assert isinstance(simple_handler, TaskBackedModeContract)
