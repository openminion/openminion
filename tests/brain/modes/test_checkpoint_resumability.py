from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.loop.strategies.coding import CodingMode
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.loop.tools.phases.eval import EvalMode
from openminion.modules.brain.loop.tools.phases.observe import OBSERVE_MODE, ObserveMode
from openminion.modules.brain.loop.tools.phases.refine import REFINE_MODE, RefineMode
from openminion.modules.brain.checkpoint.contracts import (
    TaskBackedModeContract,
)
from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    WorkingState,
    new_uuid,
)
from openminion.modules.brain.schemas.closure import ClosureJudgment
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.brain.checkpoint import CheckpointManager
from openminion.modules.llm.schemas import LLMResponse, ToolCall, UsageInfo
from openminion.modules.task import TaskLifecycleState, TaskManager


@dataclass
class _ModeServices:
    task_manager: TaskManager
    statuses: list[dict[str, Any]] = field(default_factory=list)
    plan_calls: list[str] = field(default_factory=list)
    response_queue: list[str] = field(default_factory=list)
    runner: Any = None

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
        if self.response_queue:
            return self.response_queue.pop(0)
        return ""

    def plan(self, *, state, user_input, logger, decision=None):
        del state, logger, decision
        self.plan_calls.append(str(user_input or ""))
        return SimpleNamespace(objective="mock plan result.", steps=[])

    def approve_command(self, *, state, command, logger):
        del state, logger
        return command

    def act_command(self, *, state, command, logger):
        del state, command, logger
        return ActionResult(command_id=new_uuid(), status="success", summary="ok"), None

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
        return ClosureJudgment(satisfied=True, next_action="close")

    def apply_closure_judgment(self, *, state, judgment):
        del state, judgment
        return "close"

    def extract_success_memories(self, **kwargs):
        del kwargs
        return []

    def create_task(self, **kwargs):
        return self.task_manager.create_task(**kwargs)

    def get_task(self, *, task_id: str):
        return self.task_manager.get_task(task_id)

    def list_open_tasks_for_session(
        self, *, session_id: str, mode_name: str | None = None, limit: int = 100
    ):
        return self.task_manager.list_open_tasks_for_session(
            session_id,
            mode_name=mode_name,
            limit=limit,
        )

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


@dataclass
class _FakeLLMClient:
    responses: list[LLMResponse] = field(default_factory=list)
    _index: int = 0

    def complete(self, messages, tools=None, **overrides) -> LLMResponse:
        del messages, tools, overrides
        response = self.responses[self._index]
        self._index += 1
        return response


@dataclass
class _FakeCommandExecutor:
    outcomes: list[CommandExecutionOutcome] = field(default_factory=list)
    _index: int = 0

    def execute_command(
        self,
        *,
        state: WorkingState,
        command: Any,
        logger: Any,
        preapproved: bool = False,
        approve_only: bool = False,
        include_reflect: bool = True,
    ) -> CommandExecutionOutcome:
        del state, logger, preapproved, approve_only, include_reflect
        tool_name = str(getattr(command, "tool_name", "") or "").strip()
        if tool_name == "code.repo_index":
            return CommandExecutionOutcome(
                approved_command=command,
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="repo index",
                    outputs={
                        "repo_index": {
                            "root": "/workspace",
                            "files": [{"path": "src/main.py", "language": "python"}],
                            "symbols": [],
                            "imports": [],
                        }
                    },
                ),
            )
        if tool_name == "code.repo_map":
            return CommandExecutionOutcome(
                approved_command=command,
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="repo map",
                    outputs={"repo_map": "src/"},
                ),
            )
        if tool_name == "code.symbol_find":
            return CommandExecutionOutcome(
                approved_command=command,
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="no symbols",
                    outputs={"matches": []},
                ),
            )
        outcome = self.outcomes[self._index]
        self._index += 1
        return outcome


def _state(
    *,
    session_id: str,
    ticks: int = 5,
    tool_calls: int = 5,
    goal: str,
) -> WorkingState:
    return WorkingState(
        session_id=session_id,
        agent_id="router-agent",
        goal=goal,
        budgets_remaining=BudgetCounters(
            ticks=ticks,
            tool_calls=tool_calls,
            a2a_calls=1,
            tokens=5000,
            time_ms=120000,
        ),
        trace_id=f"trace-{session_id}",
    )


def _ctx(
    task_manager: TaskManager,
    *,
    state: WorkingState,
    decision: Any,
    user_input: str,
    response_queue: list[str] | None = None,
    llm_adapter: Any = None,
    command_executor: Any = None,
) -> tuple[ExecutionContext, _ModeServices]:
    services = _ModeServices(
        task_manager=task_manager,
        response_queue=list(response_queue or []),
    )
    services.runner = SimpleNamespace(task_manager=task_manager)
    ctx = ExecutionContext(
        state=state,
        decision=decision,
        user_input=user_input,
        logger=SimpleNamespace(events=[], emit=lambda *args, **kwargs: None),
        options=SimpleNamespace(profile=None),
        llm_adapter=llm_adapter,
        command_executor=command_executor or SimpleNamespace(),
        _services=services,
    )
    return ctx, services


def test_refine_mode_pauses_and_resumes_from_checkpoint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        decision = SimpleNamespace(
            mode=REFINE_MODE,
            confidence=0.9,
            reason_code="refine_request",
            refine_target="handler.py",
            refine_criteria=["clarity"],
            objective="handler.py",
        )
        first_ctx, first_services = _ctx(
            task_manager,
            state=_state(session_id="s-refine-1", ticks=1, goal="handler.py"),
            decision=decision,
            user_input="refine handler",
            response_queue=[
                json.dumps(
                    {
                        "action_taken": "tightened names",
                        "quality_assessment": "better but more work remains",
                        "remaining_issues": ["docs"],
                        "passed_gate": False,
                    }
                )
            ],
        )
        mode = RefineMode()

        paused = mode.execute(first_ctx)

        assert paused.status == "waiting_user"
        assert len(first_services.plan_calls) == 1
        checkpoint_id = str(first_ctx.state.task_backed_checkpoint_id or "")
        assert checkpoint_id.endswith("-cursor-1")
        assert isinstance(mode, TaskBackedModeContract)

        resumed_state = first_ctx.state.model_copy(deep=True)
        resumed_state.budgets_remaining.ticks = 5
        resumed_ctx, resumed_services = _ctx(
            task_manager,
            state=resumed_state,
            decision=decision,
            user_input="continue",
            response_queue=[
                json.dumps(
                    {
                        "action_taken": "added docs",
                        "quality_assessment": "looks good now",
                        "remaining_issues": [],
                        "passed_gate": True,
                    }
                )
            ],
        )
        resumed_ctx.state.task_backed_task_id = first_ctx.state.task_backed_task_id
        resumed_ctx.state.task_backed_resume_state = mode.resume(
            resumed_ctx, checkpoint_id
        )

        finished = mode.execute(resumed_ctx)

        assert finished.status == "done"
        assert len(resumed_services.plan_calls) == 1
        task = task_manager.get_task(str(resumed_ctx.state.task_backed_task_id))
        assert task is not None
        assert task.state == TaskLifecycleState.DONE


def test_refine_mode_version_mismatch_returns_empty_resume_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        manager = CheckpointManager(task_service=task_manager)
        task_id = manager.create_task(
            session_id="s-refine-version",
            owner=REFINE_MODE,
            goal="handler.py",
            agent_id="router-agent",
        )
        checkpoint_id = manager.save_payload(
            owner=REFINE_MODE,
            version=1,
            task_id=task_id,
            payload={"round_history": []},
            cursor=1,
        )
        mode = RefineMode()
        mode.CHECKPOINT_VERSION = 2
        ctx, _ = _ctx(
            task_manager,
            state=_state(session_id="s-refine-version", goal="handler.py"),
            decision=SimpleNamespace(mode=REFINE_MODE, objective="handler.py"),
            user_input="continue",
        )
        ctx.state.task_backed_task_id = task_id

        assert mode.resume(ctx, checkpoint_id) == {}


def test_coding_mode_resumes_after_budget_exit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        llm_client = _FakeLLMClient(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="fake-model",
                    output_text="",
                    tool_calls=[
                        ToolCall(
                            id="tc-1",
                            name="file.read",
                            arguments={"path": "/tmp/app.py"},
                        )
                    ],
                    usage=UsageInfo(input_tokens=1, output_tokens=1),
                )
            ]
        )
        executor = _FakeCommandExecutor(
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(tool_name="file.read"),
                    action_result=ActionResult(
                        command_id=new_uuid(),
                        status="success",
                        summary="read ok",
                        outputs={"content": "print('hi')"},
                    ),
                )
            ]
        )
        decision = SimpleNamespace(
            mode="coding",
            confidence=0.9,
            reason_code="coding_task",
            objective="inspect repo",
            sub_intents=[],
            rationale="",
            question=None,
            answer=None,
            success_criteria={},
        )
        first_ctx, _ = _ctx(
            task_manager,
            state=_state(session_id="s-coding-1", tool_calls=1, goal="inspect repo"),
            decision=decision,
            user_input="find auth",
            llm_adapter=SimpleNamespace(client=llm_client),
            command_executor=executor,
        )
        mode = CodingMode()

        paused = mode.execute(first_ctx)

        assert paused.action_result is not None
        assert paused.action_result.error is not None
        assert paused.action_result.error.code == "coding_budget_exhausted"
        checkpoint_id = str(first_ctx.state.task_backed_checkpoint_id or "")
        assert checkpoint_id.endswith("-cursor-1")
        assert isinstance(mode, TaskBackedModeContract)

        resumed_ctx, _ = _ctx(
            task_manager,
            state=first_ctx.state.model_copy(deep=True),
            decision=decision,
            user_input="continue",
            llm_adapter=SimpleNamespace(
                client=_FakeLLMClient(
                    responses=[
                        LLMResponse(
                            ok=True,
                            provider="fake",
                            model="fake-model",
                            output_text="Auth is in app.py",
                            usage=UsageInfo(input_tokens=1, output_tokens=1),
                        )
                    ]
                )
            ),
            command_executor=_FakeCommandExecutor(),
        )
        resumed_ctx.state.task_backed_task_id = first_ctx.state.task_backed_task_id
        resumed_ctx.state.budgets_remaining.tool_calls = 5
        resumed_ctx.state.task_backed_resume_state = mode.resume(
            resumed_ctx, checkpoint_id
        )

        finished = mode.execute(resumed_ctx)

        assert finished.status == "done"
        assert "app.py" in str(finished.message or "")


def test_coding_mode_resumes_after_needs_user_without_duplicate_batch_stop() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        llm_client = _FakeLLMClient(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="fake-model",
                    output_text="",
                    tool_calls=[
                        ToolCall(
                            id="tc-1",
                            name="exec.run",
                            arguments={"cmd": "rm -rf build"},
                        )
                    ],
                    usage=UsageInfo(input_tokens=1, output_tokens=1),
                )
            ]
        )
        executor = _FakeCommandExecutor(
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=ActionResult(
                        command_id=new_uuid(),
                        status="needs_user",
                        summary="Please approve deleting build artifacts.",
                    ),
                )
            ]
        )
        decision = SimpleNamespace(
            mode="coding",
            confidence=0.9,
            reason_code="coding_task",
            objective="clean build output",
            sub_intents=[],
            rationale="",
            question=None,
            answer=None,
            success_criteria={},
        )
        first_ctx, _ = _ctx(
            task_manager,
            state=_state(
                session_id="s-coding-needs-user",
                tool_calls=5,
                goal="clean build output",
            ),
            decision=decision,
            user_input="remove build output",
            llm_adapter=SimpleNamespace(client=llm_client),
            command_executor=executor,
        )
        mode = CodingMode()

        paused = mode.execute(first_ctx)

        assert paused.status == "waiting_user"
        checkpoint_id = str(first_ctx.state.task_backed_checkpoint_id or "")
        assert checkpoint_id.endswith("-cursor-1")

        resumed_ctx, _ = _ctx(
            task_manager,
            state=first_ctx.state.model_copy(deep=True),
            decision=decision,
            user_input="continue",
            llm_adapter=SimpleNamespace(
                client=_FakeLLMClient(
                    responses=[
                        LLMResponse(
                            ok=True,
                            provider="fake",
                            model="fake-model",
                            output_text="",
                            tool_calls=[
                                ToolCall(
                                    id="tc-1",
                                    name="exec.run",
                                    arguments={"cmd": "rm -rf build"},
                                )
                            ],
                            usage=UsageInfo(input_tokens=1, output_tokens=1),
                        ),
                        LLMResponse(
                            ok=True,
                            provider="fake",
                            model="fake-model",
                            output_text="Build artifacts removed.",
                            usage=UsageInfo(input_tokens=1, output_tokens=1),
                        ),
                    ]
                )
            ),
            command_executor=_FakeCommandExecutor(
                outcomes=[
                    CommandExecutionOutcome(
                        approved_command=SimpleNamespace(),
                        action_result=ActionResult(
                            command_id=new_uuid(),
                            status="success",
                            summary="removed build artifacts",
                        ),
                    )
                ]
            ),
        )
        resumed_ctx.state.task_backed_task_id = first_ctx.state.task_backed_task_id
        resumed_ctx.state.task_backed_resume_state = mode.resume(
            resumed_ctx, checkpoint_id
        )

        finished = mode.execute(resumed_ctx)

        assert finished.status == "done"
        assert "removed" in str(finished.message or "").lower()


def test_observe_mode_pauses_and_resumes_with_elapsed_timeout_budget(
    monkeypatch,
) -> None:
    values = iter([100.0, 101.0, 200.0, 201.0])
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.monotonic",
        lambda: next(values),
    )
    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.phases.observe.time.sleep",
        lambda _: None,
    )

    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        decision = SimpleNamespace(
            mode=OBSERVE_MODE,
            confidence=0.9,
            reason_code="observe_request",
            observe_target="http://example.com/health",
            observe_condition="returns HTTP 200",
            observe_check_command="fetch http://example.com/health",
            objective="http://example.com/health",
        )
        first_ctx, _ = _ctx(
            task_manager,
            state=_state(
                session_id="s-observe-1",
                ticks=1,
                goal="Watch the health endpoint",
            ),
            decision=decision,
            user_input="observe health",
            response_queue=[
                json.dumps(
                    {
                        "check_output": "HTTP 503",
                        "condition_met": False,
                        "assessment": "not ready",
                    }
                )
            ],
        )
        mode = ObserveMode()

        paused = mode.execute(first_ctx)

        assert paused.status == "waiting_user"
        checkpoint_id = str(first_ctx.state.task_backed_checkpoint_id or "")
        assert checkpoint_id.endswith("-cursor-1")
        assert isinstance(mode, TaskBackedModeContract)

        resumed_ctx, _ = _ctx(
            task_manager,
            state=first_ctx.state.model_copy(deep=True),
            decision=decision,
            user_input="continue",
            response_queue=[
                json.dumps(
                    {
                        "check_output": "HTTP 200",
                        "condition_met": True,
                        "assessment": "healthy",
                    }
                )
            ],
        )
        resumed_ctx.state.task_backed_task_id = first_ctx.state.task_backed_task_id
        resumed_ctx.state.budgets_remaining.ticks = 5
        resumed_ctx.state.task_backed_resume_state = mode.resume(
            resumed_ctx, checkpoint_id
        )

        finished = mode.execute(resumed_ctx)

        assert finished.status == "done"
        assert "Checks performed: 2" in str(finished.message or "")
        assert "Elapsed time: 2.0s" in str(finished.message or "")


def test_eval_mode_resumes_from_cached_evidence_and_skips_regather() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        task_manager = TaskManager.for_lifecycle_db(db_path=Path(tmp) / "tasks.db")
        decision = SimpleNamespace(
            mode="eval",
            confidence=0.9,
            reason_code="eval_request",
            eval_target="handler.py",
            eval_criteria=["correctness"],
            objective="handler.py",
        )
        first_ctx, first_services = _ctx(
            task_manager,
            state=_state(session_id="s-eval-1", ticks=1, goal="handler.py"),
            decision=decision,
            user_input="evaluate handler",
        )
        mode = EvalMode()

        paused = mode.execute(first_ctx)

        assert paused.status == "waiting_user"
        assert len(first_services.plan_calls) == 1
        checkpoint_id = str(first_ctx.state.task_backed_checkpoint_id or "")
        assert checkpoint_id.endswith("-cursor-1")
        assert isinstance(mode, TaskBackedModeContract)

        resumed_ctx, resumed_services = _ctx(
            task_manager,
            state=first_ctx.state.model_copy(deep=True),
            decision=decision,
            user_input="continue",
            response_queue=[
                json.dumps(
                    {
                        "target": "handler.py",
                        "criteria": [
                            {
                                "name": "correctness",
                                "description": "",
                                "verdict": "pass",
                                "evidence": "cached evidence",
                                "notes": "",
                            }
                        ],
                        "overall_verdict": "pass",
                        "summary": "looks good",
                        "confidence": 0.8,
                    }
                )
            ],
        )
        resumed_ctx.state.task_backed_task_id = first_ctx.state.task_backed_task_id
        resumed_ctx.state.budgets_remaining.ticks = 5
        resumed_ctx.state.task_backed_resume_state = mode.resume(
            resumed_ctx, checkpoint_id
        )

        finished = mode.execute(resumed_ctx)

        assert finished.status == "done"
        assert len(resumed_services.plan_calls) == 0
        assert "Overall verdict: PASS" in str(finished.message or "")
