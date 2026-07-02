"""Coding-mode integration tests."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from openminion.modules.brain.loop.strategies.coding import CodingMode
from openminion.modules.brain.loop.strategies.coding.loop_state import CodingLoopState
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_CIRCULAR_PATTERN,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopState,
)
from openminion.modules.brain.loop.tools.confirmation import (
    attach_confirmation_replay_queue,
    confirmation_required_user_message,
)
from openminion.modules.brain.loop.strategies.coding.verification import (
    coerce_coding_verifier_verdict,
    serialize_verifier_candidate,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.schemas import (
    ActionError,
    ActionResult,
    ArtifactRef,
    BudgetCounters,
    Deliverable,
    Goal,
    ModeProfileConfig,
    SuccessCriterion,
    ToolCommand,
    WorkingState,
    new_uuid,
)
from openminion.modules.brain.schemas.closure import ClosureJudgment
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.llm.schemas import LLMResponse, ToolCall, UsageInfo

_PLANNER_CONTEXT_TOOLS = frozenset(
    {"code.repo_index", "code.repo_map", "code.symbol_find"}
)


def test_coding_loop_state_telemetry_preserves_tool_results_for_closure() -> None:
    loop = CodingLoopState(
        scratchpad={
            "adaptive.tool_results": [
                {
                    "tool_name": "file.write",
                    "verified": True,
                    "data": {"path": "demo/pyproject.toml", "bytes_written": 128},
                },
                {
                    "tool_name": "exec.run",
                    "verified": True,
                    "data": {"argv": ["pytest", "-q"], "exit_code": 0},
                },
            ]
        }
    )

    payload = loop.telemetry_payload(frozenset({"file.write", "exec.run"}))

    assert payload["tool_execution_count"] == 2
    assert payload["tool_verified"] is True
    assert payload["tool_results"][0]["data"]["path"] == "demo/pyproject.toml"
    assert payload["tool_results"][1]["data"]["exit_code"] == 0


def _patch_child_dispatch(monkeypatch, fake_invoke) -> None:
    monkeypatch.setattr(
        "openminion.modules.brain.loop.strategies.coding.handler.invoke_decision_direct",
        lambda runner, *, state, decision, user_input, logger, depth=0: fake_invoke(
            runner,
            state=state,
            decision=decision,
            user_input=user_input,
            logger=logger,
            depth=depth,
        ),
    )


# Fakes


@dataclass
class _FakeLLMClient:
    """Fake LLMClient that returns responses from a queue."""

    responses: list[LLMResponse] = field(default_factory=list)
    calls: list[Any] = field(default_factory=list)
    _index: int = 0

    def complete(self, messages, tools=None, **overrides) -> LLMResponse:
        self.calls.append(
            {"messages": messages, "tools": tools, "overrides": overrides}
        )
        if self._index < len(self.responses):
            resp = self.responses[self._index]
            self._index += 1
            return resp
        # Default: final text with no tool calls
        return LLMResponse(
            ok=True,
            provider="fake",
            model=overrides.get("model", "fake-model"),
            output_text="done",
        )


@dataclass
class _FakeCommandExecutor:
    """Fake CommandExecutor; records calls and returns preset outcomes."""

    outcomes: list[CommandExecutionOutcome] = field(default_factory=list)
    calls: list[Any] = field(default_factory=list)
    _index: int = 0
    include_reflect_values: list[bool] = field(default_factory=list)

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
                            "files": [
                                {
                                    "path": "src/auth.py",
                                    "language": "python",
                                    "top_level_symbols": ["AuthService"],
                                    "imports": ["time"],
                                }
                            ],
                            "symbols": [
                                {
                                    "name": "AuthService",
                                    "kind": "class",
                                    "file": "src/auth.py",
                                    "start_line": 1,
                                    "end_line": 20,
                                }
                            ],
                            "imports": [
                                {
                                    "importer": "src/auth.py",
                                    "module": "time",
                                    "imported_names": [],
                                }
                            ],
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
                    outputs={"repo_map": "src/\n  auth.py :: AuthService"},
                ),
            )
        if tool_name == "code.symbol_find":
            symbol = str(getattr(command, "args", {}).get("symbol", "") or "")
            return CommandExecutionOutcome(
                approved_command=command,
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary=f"found {symbol}",
                    outputs={
                        "matches": [
                            {
                                "file": "src/auth.py",
                                "start_line": 10,
                                "end_line": 24,
                                "kind": "class",
                            }
                        ]
                    },
                ),
            )
        self.calls.append(command)
        self.include_reflect_values.append(include_reflect)
        if self._index < len(self.outcomes):
            outcome = self.outcomes[self._index]
            self._index += 1
            return outcome
        outputs = {"content": "file content"}
        artifact_refs: list[ArtifactRef] = []
        if tool_name == "exec.run":
            outputs = {"report": "ok"}
            artifact_refs = [ArtifactRef(ref="runtime://exec-run.txt")]
        return CommandExecutionOutcome(
            approved_command=command,
            action_result=ActionResult(
                command_id=new_uuid(),
                status="success",
                summary="ok",
                outputs=outputs,
                artifact_refs=artifact_refs,
            ),
        )

    def advance_after_action(
        self, *, state, action_result, force_replan=False, logger=None
    ):
        pass


@dataclass
class _TimedCommandExecutor(_FakeCommandExecutor):
    delays_by_path: dict[str, float] = field(default_factory=dict)
    call_windows: list[tuple[str, float, float]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

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
        del preapproved, approve_only
        tool_name = str(getattr(command, "tool_name", "") or "").strip()
        if tool_name in _PLANNER_CONTEXT_TOOLS:
            return super().execute_command(
                state=state,
                command=command,
                logger=logger,
                include_reflect=include_reflect,
            )
        path = str(getattr(command, "args", {}).get("path", "") or "")
        started = time.monotonic()
        delay = float(self.delays_by_path.get(path, 0.0) or 0.0)
        if delay > 0:
            time.sleep(delay)
        result = super().execute_command(
            state=state,
            command=command,
            logger=logger,
            include_reflect=include_reflect,
        )
        finished = time.monotonic()
        with self._lock:
            self.call_windows.append((path, started, finished))
        return result


@dataclass
class _RepoIndexFallbackExecutor(_FakeCommandExecutor):
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
        tool_name = str(getattr(command, "tool_name", "") or "").strip()
        if tool_name == "code.repo_index":
            return CommandExecutionOutcome(
                approved_command=command,
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="failed",
                    summary="repo index unavailable",
                ),
            )
        return super().execute_command(
            state=state,
            command=command,
            logger=logger,
            preapproved=preapproved,
            approve_only=approve_only,
            include_reflect=include_reflect,
        )


@dataclass
class _FakeServices:
    statuses: list[dict[str, Any]] = field(default_factory=list)
    runner: Any = None
    closure_judgment: ClosureJudgment | None = None
    closure_disposition: str | None = None

    def save_state(self, *, state: WorkingState) -> None:
        pass

    def emit_phase_status(self, *, state: WorkingState, **kwargs) -> None:
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
        state.status = status
        del kind
        return SimpleNamespace(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
        )

    def direct_response(self, *, user_input, decision):
        return ""

    def plan(self, **kwargs):
        raise AssertionError("coding mode should not call plan()")

    def approve_command(self, *, state, command, logger):
        return command

    def act_command(self, *, state, command, logger):
        return ActionResult(command_id=new_uuid(), status="success", summary="ok"), None

    def assess_plan_feasibility(self, **kwargs):
        return None

    def evaluate_meta(self, **kwargs):
        return None

    def apply_meta_directive(self, **kwargs):
        pass

    def meta_override_response(self, **kwargs):
        return None

    def meta_tool_restriction_reason(self, **kwargs):
        return None

    def command_has_side_effects(self, *, command):
        return True

    def resolve_verification_mode(self, *, current, candidate):
        return candidate if candidate is not None else current

    def verify(self, **kwargs):
        return True

    def improve(self, **kwargs):
        pass

    def compact(self, **kwargs):
        pass

    def evaluate_turn_closure(self, **kwargs) -> ClosureJudgment:
        if self.closure_judgment is not None:
            return self.closure_judgment
        return ClosureJudgment(satisfied=True, next_action="close")

    def apply_closure_judgment(self, *, state, judgment) -> str:
        del state, judgment
        if self.closure_disposition is not None:
            return self.closure_disposition
        return "close"

    def extract_success_memories(self, **kwargs) -> list[str]:
        return []

    def create_task(self, **kwargs):
        del kwargs
        return SimpleNamespace(task_id="coding-task-1")

    def get_task(self, **kwargs):
        return None

    def list_open_tasks_for_session(self, **kwargs):
        return []

    def save_checkpoint(self, **kwargs):
        pass

    def get_latest_checkpoint(self, **kwargs):
        return None

    def list_checkpoints(self, **kwargs):
        return []

    def update_task_progress(self, **kwargs):
        pass

    def transition_task(self, **kwargs):
        pass


def _state(
    tool_calls: int = 10,
    tokens: int = 50000,
    llm_calls_max: int = 20,
) -> WorkingState:
    return WorkingState(
        session_id="s-coding",
        agent_id="test-agent",
        goal="inspect the repo",
        budgets_remaining=BudgetCounters(
            ticks=20,
            tool_calls=tool_calls,
            a2a_calls=0,
            tokens=tokens,
            time_ms=120000,
        ),
        llm_calls_max=llm_calls_max,
    )


def _llm_adapter(client: _FakeLLMClient) -> Any:
    return SimpleNamespace(client=client)


def _decision() -> Any:
    return SimpleNamespace(
        mode="coding",
        confidence=0.9,
        reason_code="coding_task",
        sub_intents=[],
        rationale="",
        question=None,
        answer=None,
        objective="inspect repo",
        success_criteria={},
    )


def _ctx(
    llm_client: _FakeLLMClient,
    executor: _FakeCommandExecutor,
    state: WorkingState | None = None,
    services: _FakeServices | None = None,
    user_input: str = "find where auth is implemented",
) -> ExecutionContext:
    services = services or _FakeServices()
    return ExecutionContext(
        state=state or _state(),
        decision=_decision(),
        user_input=user_input,
        logger=MagicMock(),
        options=SimpleNamespace(profile=None),
        llm_adapter=_llm_adapter(llm_client),
        command_executor=executor,
        _services=services,
    )


def _plan_response(payload: str) -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text=payload,
        finish_reason="stop",
    )


def _coding_resume_payload() -> dict[str, Any]:
    verifier_command = ToolCommand(
        title="Run tests",
        tool_name="exec.run",
        args={"argv": ["pytest", "-q"]},
    )
    verifier_result = ActionResult(
        command_id=new_uuid(),
        status="success",
        summary="tests passed",
        outputs={"report": "ok"},
        artifact_refs=[ArtifactRef(ref="runtime://pytest-report.txt")],
    )
    return {
        "messages": [
            {
                "role": "user",
                "content": "fix the parser",
                "meta": {},
            }
        ],
        "iteration": 1,
        "llm_calls": 1,
        "tool_calls_made": ["exec.run"],
        "total_tool_calls": 1,
        "termination_reason": "final_text",
        "seen_signatures": [],
        "coding_plan": {
            "goal": "fix the parser",
            "phases": [
                {
                    "name": "plan",
                    "status": "done",
                    "steps": ["restate the target"],
                    "output": "parser plan",
                },
                {
                    "name": "implement",
                    "status": "done",
                    "steps": ["edit parser"],
                    "output": "patched parser",
                },
                {
                    "name": "verify",
                    "status": "active",
                    "steps": ["run tests"],
                    "output": "",
                },
            ],
            "current_phase": "verify",
            "scratchpad": [],
            "completed_steps": [],
            "open_issues": [],
            "subtasks": [],
            "verifier_goal": _coding_verifier_goal().model_dump(mode="json"),
        },
        "scratchpad": {
            "coding.plan_phases_executed": ["plan", "implement", "verify"],
            "coding.current_phase": "verify",
            "coding.open_issues_count": 0,
            "coding.last_verifier_candidate": serialize_verifier_candidate(
                command=verifier_command,
                action_result=verifier_result,
            ),
        },
    }


def _coding_verifier_goal() -> Goal:
    return Goal(
        goal_id="coding-goal-1",
        description="Confirm the coding task produced structured proof.",
        success_criteria=[
            SuccessCriterion(
                criterion_id="criterion-1",
                description="verification command produced structured evidence",
                structural_check="structured_evidence_present",
            )
        ],
        deliverables=[
            Deliverable(
                deliverable_id="deliverable-1",
                description="verification artifact produced",
                verification_hint="artifact_presence",
            )
        ],
    )


def _typed_verifier_failure_summary() -> str:
    return (
        "Typed verifier did not confirm coding completion: "
        "Structural verify(...) reported failure.; "
        "Missing artifact_refs for artifact_presence verifier."
    )


def _subtask_plan_response(
    *,
    first_target: str = "src/a.py",
    second_target: str = "src/b.py",
) -> LLMResponse:
    return _plan_response(
        f"""
        {{
          "goal": "split work",
          "phases": [
            {{"name": "implement", "status": "active", "steps": ["fan out subtasks"], "output": ""}},
            {{"name": "verify", "status": "pending", "steps": ["run tests"], "output": ""}}
          ],
          "current_phase": "implement",
          "scratchpad": [],
          "completed_steps": [],
          "open_issues": [],
          "verifier_goal": {json.dumps(_coding_verifier_goal().model_dump(mode="json"))},
          "subtasks": [
            {{"goal": "patch alpha", "target_files": ["{first_target}"], "success_criteria": "alpha done", "status": "pending"}},
            {{"goal": "patch beta", "target_files": ["{second_target}"], "success_criteria": "beta done", "status": "pending"}}
          ]
        }}
        """
    )


# Happy path: one tool call then final text


def test_coding_loop_single_tool_then_final_text() -> None:
    executor = _FakeCommandExecutor()
    llm_client = _FakeLLMClient(
        responses=[
            # First call: LLM requests file.read
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="tc-1", name="file.read", arguments={"path": "/src/auth.py"}
                    )
                ],
                finish_reason="tool_calls",
            ),
            # Second call: final answer
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Auth is implemented in /src/auth.py",
                finish_reason="stop",
            ),
        ]
    )
    handler = CodingMode()
    ctx = _ctx(llm_client, executor)
    result = handler.execute(ctx)

    assert result.status == "done"
    assert "auth.py" in (result.message or "")
    # Tool was called exactly once
    assert len(executor.calls) == 1
    assert executor.calls[0].tool_name == "file.read"
    # include_reflect was False for every tool call
    assert all(not v for v in executor.include_reflect_values)
    # LLM was called twice
    assert len(llm_client.calls) == 2


def test_coding_loop_exec_run_cmd_alias_reaches_final_text() -> None:
    executor = _FakeCommandExecutor()
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="tc-exec",
                        name="exec.run",
                        arguments={"cmd": "pytest -q tests/test_report.py"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="fixed and verified",
                finish_reason="stop",
            ),
        ]
    )

    result = CodingMode().execute(_ctx(llm_client, executor))

    assert result.status == "done"
    assert result.message == "fixed and verified"
    assert len(executor.calls) == 1
    assert executor.calls[0].tool_name == "exec.run"
    assert executor.calls[0].args["cmd"] == "pytest -q tests/test_report.py"


def test_coding_loop_preserves_tool_transcript_shape_for_follow_up_round() -> None:
    executor = _FakeCommandExecutor()
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="tc-1", name="file.read", arguments={"path": "/src/auth.py"}
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
                finish_reason="stop",
            ),
        ]
    )
    handler = CodingMode()
    ctx = _ctx(llm_client, executor)

    result = handler.execute(ctx)

    assert result.status == "done"
    second_call_messages = llm_client.calls[1]["messages"]
    tool_message = next(
        message for message in reversed(second_call_messages) if message.role == "tool"
    )
    assert tool_message.role == "tool"
    assert tool_message.meta["tool_name"] == "file.read"
    assert tool_message.content == (
        '{"status": "success", "summary": "ok", "outputs": {"content": "file content"}}'
    )


def test_coding_loop_executes_all_plan_phases_in_order() -> None:
    executor = _FakeCommandExecutor()
    llm_client = _FakeLLMClient(
        responses=[
            _plan_response(
                json.dumps(
                    {
                        "goal": "inspect auth",
                        "phases": [
                            {
                                "name": "explore",
                                "status": "active",
                                "steps": ["inspect files"],
                                "output": "",
                            },
                            {
                                "name": "plan",
                                "status": "pending",
                                "steps": ["choose edits"],
                                "output": "",
                            },
                            {
                                "name": "implement",
                                "status": "pending",
                                "steps": ["apply edits"],
                                "output": "",
                            },
                            {
                                "name": "verify",
                                "status": "pending",
                                "steps": ["run tests"],
                                "output": "",
                            },
                        ],
                        "current_phase": "explore",
                        "scratchpad": [],
                        "completed_steps": [],
                        "open_issues": [],
                        "subtasks": [],
                        "verifier_goal": _coding_verifier_goal().model_dump(
                            mode="json"
                        ),
                    }
                )
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="explore complete",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="plan complete",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="tc-run",
                        name="exec.run",
                        arguments={"argv": ["pytest", "-q"]},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="implementation complete",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="verification complete",
                finish_reason="stop",
            ),
        ]
    )
    services = _FakeServices()

    result = CodingMode().execute(_ctx(llm_client, executor, services=services))

    assert result.status == "done"
    assert result.message == "verification complete"
    assert result.action_result is not None
    assert result.action_result.outputs["coding.plan_phases_executed"] == [
        "explore",
        "plan",
        "implement",
        "verify",
    ]
    phase_updates = [
        status.get("detail_text")
        for status in services.statuses
        if status.get("source_phase") == "coding.plan"
    ]
    assert "[act:coding] phase: explore" in phase_updates
    assert "[act:coding] phase: plan" in phase_updates
    assert "[act:coding] phase: implement" in phase_updates
    assert "[act:coding] phase: verify" in phase_updates


def test_coding_loop_falls_back_to_implement_plan_when_plan_json_is_invalid() -> None:
    executor = _FakeCommandExecutor()
    llm_client = _FakeLLMClient(
        responses=[
            _plan_response("{not valid json"),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="fallback complete",
                finish_reason="stop",
            ),
        ]
    )

    result = CodingMode().execute(_ctx(llm_client, executor))

    assert result.status == "done"
    assert result.message == "fallback complete"
    assert result.action_result is not None
    assert result.action_result.outputs["coding.plan_phases_executed"] == ["implement"]


def test_coding_loop_rejects_phase_skip_plan_and_falls_back() -> None:
    executor = _FakeCommandExecutor()
    llm_client = _FakeLLMClient(
        responses=[
            _plan_response(
                """
                {
                  "goal": "inspect auth",
                  "phases": [
                    {"name": "explore", "status": "active", "steps": [], "output": ""},
                    {"name": "implement", "status": "pending", "steps": [], "output": ""}
                  ],
                  "current_phase": "explore",
                  "scratchpad": [],
                  "completed_steps": [],
                  "open_issues": [],
                  "subtasks": []
                }
                """
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="fallback complete",
                finish_reason="stop",
            ),
        ]
    )

    result = CodingMode().execute(_ctx(llm_client, executor))

    assert result.status == "done"
    assert result.action_result is not None
    assert result.action_result.outputs["coding.plan_phases_executed"] == ["implement"]


def test_coding_loop_stays_in_implement_until_exec_run_before_verify() -> None:
    executor = _FakeCommandExecutor()
    llm_client = _FakeLLMClient(
        responses=[
            _plan_response(
                json.dumps(
                    {
                        "goal": "inspect auth",
                        "phases": [
                            {
                                "name": "implement",
                                "status": "active",
                                "steps": ["edit"],
                                "output": "",
                            },
                            {
                                "name": "verify",
                                "status": "pending",
                                "steps": ["test"],
                                "output": "",
                            },
                        ],
                        "current_phase": "implement",
                        "scratchpad": [],
                        "completed_steps": [],
                        "open_issues": [],
                        "subtasks": [],
                        "verifier_goal": _coding_verifier_goal().model_dump(
                            mode="json"
                        ),
                    }
                )
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="implementation complete",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="tc-run",
                        name="exec.run",
                        arguments={"argv": ["pytest", "-q"]},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="implementation checked",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="verification complete",
                finish_reason="stop",
            ),
        ]
    )

    result = CodingMode().execute(_ctx(llm_client, executor))

    assert result.status == "done"
    assert executor.calls[0].tool_name == "exec.run"
    assert any(
        "Stay in implement and run at least one exec.run" in message.content
        for message in llm_client.calls[2]["messages"]
        if message.role == "user"
    )


def test_coding_plan_prompt_prefers_repo_index_over_repo_map() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            _plan_response(
                """
                {
                  "goal": "Update AuthService",
                  "phases": [
                    {"name": "implement", "status": "active", "steps": ["patch AuthService"], "output": ""}
                  ],
                  "current_phase": "implement",
                  "scratchpad": [],
                  "completed_steps": [],
                  "open_issues": [],
                  "subtasks": []
                }
                """
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
                finish_reason="stop",
            ),
        ]
    )

    result = CodingMode().execute(
        _ctx(
            llm_client,
            _FakeCommandExecutor(),
            user_input="Update AuthService to support retries",
        )
    )

    assert result.status == "done"
    system_prompt = llm_client.calls[0]["messages"][0].content
    assert "[REPO INDEX]" in system_prompt
    assert "src/auth.py" in system_prompt
    assert "AuthService" in system_prompt
    assert "[REPO MAP]" not in system_prompt
    assert "[SYMBOL FINDINGS]" not in system_prompt


def test_coding_plan_prompt_uses_repo_map_only_as_fallback() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            _plan_response(
                """
                {
                  "goal": "Update AuthService",
                  "phases": [
                    {"name": "implement", "status": "active", "steps": ["patch AuthService"], "output": ""}
                  ],
                  "current_phase": "implement",
                  "scratchpad": [],
                  "completed_steps": [],
                  "open_issues": [],
                  "subtasks": []
                }
                """
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
                finish_reason="stop",
            ),
        ]
    )

    result = CodingMode().execute(
        _ctx(
            llm_client,
            _RepoIndexFallbackExecutor(),
            user_input="Update AuthService to support retries",
        )
    )

    assert result.status == "done"
    system_prompt = llm_client.calls[0]["messages"][0].content
    assert "[REPO INDEX]" not in system_prompt
    assert "[REPO MAP - FALLBACK]" in system_prompt
    assert "auth.py :: AuthService" in system_prompt


def test_coding_plan_persists_to_module_state_and_resumes_on_continue() -> None:
    first_llm = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="tc-read",
                        name="file.read",
                        arguments={"path": "/src/auth.py"},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )
    first_ctx = _ctx(
        first_llm,
        _FakeCommandExecutor(),
        state=_state(tool_calls=1),
    )
    first_result = CodingMode().execute(first_ctx)

    assert first_result.status == "active"
    assert first_ctx.state.module_state["coding"]["coding_plan"]["current_phase"] == (
        "implement"
    )
    assert (
        first_ctx.state.task_backed_resume_state["coding_plan"]["current_phase"]
        == "implement"
    )
    assert int(first_ctx.state.task_backed_resume_state["resume_count"]) == 0

    resumed_state = first_ctx.state.model_copy(deep=True)
    resumed_state.budgets_remaining.tool_calls = 5
    second_result = CodingMode().execute(
        _ctx(
            _FakeLLMClient(
                responses=[
                    LLMResponse(
                        ok=True,
                        provider="fake",
                        model="fake-model",
                        output_text="resumed from module state",
                        finish_reason="stop",
                    )
                ]
            ),
            _FakeCommandExecutor(),
            state=resumed_state,
            user_input="continue",
        )
    )

    assert second_result.status == "done"
    assert second_result.message == "resumed from module state"
    assert "coding" not in resumed_state.module_state


def test_coding_resume_hook_mirrors_module_state_into_task_backed_surface() -> None:
    state = _state()
    state.module_state["coding"] = _coding_resume_payload()
    mode = CodingMode()

    workflow = mode.resume(
        _ctx(
            _FakeLLMClient(),
            _FakeCommandExecutor(),
            state=state,
            user_input="continue",
        )
    )

    assert workflow is not None
    assert workflow.has_next_step()
    assert state.task_backed_resume_state["coding_plan"]["current_phase"] == "verify"
    assert int(state.task_backed_resume_state["resume_count"]) == 1
    assert int(mode.snapshot_state()["resume_count"]) == 1


def test_coding_resume_replans_on_non_continue_user_input() -> None:
    state = _state()
    state.module_state["coding"] = _coding_resume_payload()
    services = _FakeServices()

    result = CodingMode().execute(
        _ctx(
            _FakeLLMClient(
                responses=[
                    LLMResponse(
                        ok=True,
                        provider="fake",
                        model="fake-model",
                        output_text="replanned",
                        finish_reason="stop",
                    )
                ]
            ),
            _FakeCommandExecutor(),
            state=state,
            services=services,
            user_input="switch to parser cleanup",
        )
    )

    assert result.status == "done"
    assert result.message == "replanned"
    assert not any(
        status.get("detail_text") == "[act:coding] phase: plan"
        for status in services.statuses
    )


def test_coding_resume_hook_rehydrates_plan_from_module_state() -> None:
    state = _state()
    state.module_state["coding"] = _coding_resume_payload()
    mode = CodingMode()

    workflow = mode.resume(
        _ctx(
            _FakeLLMClient(),
            _FakeCommandExecutor(),
            state=state,
            user_input="continue",
        )
    )

    assert workflow is not None
    assert workflow.has_next_step()
    assert mode.snapshot_state()["coding_plan"]["current_phase"] == "verify"
    assert int(mode.snapshot_state()["resume_count"]) == 1


def test_coding_cancel_clears_module_state_and_emits_stop() -> None:
    state = _state()
    state.module_state["coding"] = _coding_resume_payload()
    ctx = _ctx(_FakeLLMClient(), _FakeCommandExecutor(), state=state)

    result = CodingMode().cancel(ctx, "stop coding")

    assert result.status == "stopped"
    assert "stop coding" in str(result.message or "")
    assert "coding" not in state.module_state


def test_coding_verify_failure_returns_continue_and_records_self_correction() -> None:
    state = _state()
    state.module_state["coding"] = _coding_resume_payload()
    services = _FakeServices()
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=ToolCommand(
                    title="Run tests",
                    tool_name="exec.run",
                    args={"argv": ["pytest", "-q"]},
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="failed",
                    summary="tests failed",
                ),
            )
        ]
    )

    result = CodingMode().execute(
        _ctx(
            _FakeLLMClient(
                responses=[
                    LLMResponse(
                        ok=True,
                        provider="fake",
                        model="fake-model",
                        output_text="",
                        tool_calls=[
                            ToolCall(
                                id="tc-run",
                                name="exec.run",
                                arguments={"argv": ["pytest", "-q"]},
                            )
                        ],
                        finish_reason="tool_calls",
                    )
                ]
            ),
            executor,
            state=state,
            services=services,
            user_input="continue",
        )
    )

    assert result.status == "continue"
    assert result.action_result is not None
    assert result.action_result.outputs["coding.self_corrections"] == 1
    assert state.module_state["coding"]["coding_plan"]["current_phase"] == "implement"
    assert any(
        "[act:coding] self-correcting: attempt 1/7"
        in str(status.get("detail_text") or "")
        for status in services.statuses
    )


def test_coding_self_correction_continues_then_finishes_without_user_input() -> None:
    state = _state()
    state.module_state["coding"] = _coding_resume_payload()
    first_result = CodingMode().execute(
        _ctx(
            _FakeLLMClient(
                responses=[
                    LLMResponse(
                        ok=True,
                        provider="fake",
                        model="fake-model",
                        output_text="",
                        tool_calls=[
                            ToolCall(
                                id="tc-run",
                                name="exec.run",
                                arguments={"argv": ["pytest", "-q"]},
                            )
                        ],
                        finish_reason="tool_calls",
                    )
                ]
            ),
            _FakeCommandExecutor(
                outcomes=[
                    CommandExecutionOutcome(
                        approved_command=ToolCommand(
                            title="Run tests",
                            tool_name="exec.run",
                            args={"argv": ["pytest", "-q"]},
                        ),
                        action_result=ActionResult(
                            command_id=new_uuid(),
                            status="failed",
                            summary="tests failed",
                        ),
                    )
                ]
            ),
            state=state,
            user_input="continue",
        )
    )

    assert first_result.status == "continue"
    follow_up = CodingMode().execute(
        _ctx(
            _FakeLLMClient(
                responses=[
                    LLMResponse(
                        ok=True,
                        provider="fake",
                        model="fake-model",
                        output_text="",
                        tool_calls=[
                            ToolCall(
                                id="tc-run-fixed",
                                name="exec.run",
                                arguments={"argv": ["pytest", "-q"]},
                            )
                        ],
                        finish_reason="tool_calls",
                    ),
                    LLMResponse(
                        ok=True,
                        provider="fake",
                        model="fake-model",
                        output_text="implementation fixed",
                        finish_reason="stop",
                    ),
                    LLMResponse(
                        ok=True,
                        provider="fake",
                        model="fake-model",
                        output_text="verified",
                        finish_reason="stop",
                    ),
                ]
            ),
            _FakeCommandExecutor(),
            state=first_result.working_state,
            user_input="",
        )
    )

    assert follow_up.status == "done"
    assert follow_up.message == "verified"
    assert follow_up.action_result is not None
    assert follow_up.action_result.outputs["coding.self_corrections"] == 1


def test_coding_final_text_continue_preserves_state_and_resumes_without_user_input() -> (
    None
):
    services = _FakeServices(
        closure_judgment=ClosureJudgment(
            satisfied=False,
            next_action="continue",
            reason="Only pyproject.toml was created.",
        ),
        closure_disposition="continue",
    )
    first_result = CodingMode().execute(
        _ctx(
            _FakeLLMClient(
                responses=[
                    LLMResponse(
                        ok=True,
                        provider="fake",
                        model="fake-model",
                        output_text="",
                        tool_calls=[
                            ToolCall(
                                id="tc-pyproject",
                                name="file.write",
                                arguments={
                                    "path": "/workspace/pyproject.toml",
                                    "content": "[project]\\nname='scratch'\\n",
                                },
                            )
                        ],
                        finish_reason="tool_calls",
                    ),
                    LLMResponse(
                        ok=True,
                        provider="fake",
                        model="fake-model",
                        output_text="Created pyproject.toml.",
                        finish_reason="stop",
                    ),
                ]
            ),
            _FakeCommandExecutor(),
            services=services,
            user_input="build a scratch project",
        )
    )

    assert first_result.status == "continue"
    assert "coding" in first_result.working_state.module_state
    resume_messages = first_result.working_state.module_state["coding"]["messages"]
    assert any(
        "Continue the coding task in phase" in str(message.get("content") or "")
        for message in resume_messages
    )

    follow_up = CodingMode().execute(
        _ctx(
            _FakeLLMClient(
                responses=[
                    LLMResponse(
                        ok=True,
                        provider="fake",
                        model="fake-model",
                        output_text="",
                        tool_calls=[
                            ToolCall(
                                id="tc-readme",
                                name="file.write",
                                arguments={
                                    "path": "/workspace/README.md",
                                    "content": "# Scratch project\\n",
                                },
                            )
                        ],
                        finish_reason="tool_calls",
                    ),
                    LLMResponse(
                        ok=True,
                        provider="fake",
                        model="fake-model",
                        output_text="Scratch project scaffold completed.",
                        finish_reason="stop",
                    ),
                ]
            ),
            _FakeCommandExecutor(),
            state=first_result.working_state,
            user_input="",
        )
    )

    assert follow_up.status == "done"
    assert follow_up.message == "Scratch project scaffold completed."


def test_coding_verify_failure_blocks_when_self_correction_cap_is_exceeded() -> None:
    state = _state()
    payload = _coding_resume_payload()
    payload["scratchpad"] = {
        **dict(payload["scratchpad"]),
        "coding.self_corrections": 1,
        "coding.last_failure_summary": "previous failure",
    }
    state.module_state["coding"] = payload
    mode = CodingMode()
    mode.apply_mode_config(
        config={"max_self_corrections": 1},
        runner=None,
        profile=None,
    )

    result = mode.execute(
        _ctx(
            _FakeLLMClient(
                responses=[
                    LLMResponse(
                        ok=True,
                        provider="fake",
                        model="fake-model",
                        output_text="",
                        tool_calls=[
                            ToolCall(
                                id="tc-run",
                                name="exec.run",
                                arguments={"argv": ["pytest", "-q"]},
                            )
                        ],
                        finish_reason="tool_calls",
                    )
                ]
            ),
            _FakeCommandExecutor(
                outcomes=[
                    CommandExecutionOutcome(
                        approved_command=ToolCommand(
                            title="Run tests",
                            tool_name="exec.run",
                            args={"argv": ["pytest", "-q"]},
                        ),
                        action_result=ActionResult(
                            command_id=new_uuid(),
                            status="failed",
                            summary="new failure",
                        ),
                    )
                ]
            ),
            state=state,
            user_input="continue",
        )
    )

    assert result.status == "waiting_user"
    assert result.action_result is not None
    assert result.action_result.error is not None
    assert result.action_result.error.code == "blocked_cap"


def test_mode_profile_config_round_trips_max_self_corrections() -> None:
    config = ModeProfileConfig(
        max_adaptive_iterations=12,
        max_self_corrections=3,
    )

    dumped = config.model_dump(mode="python")
    restored = ModeProfileConfig.model_validate(dumped)

    assert restored.max_adaptive_iterations == 12
    assert restored.max_self_corrections == 3


def test_coding_verifier_verdict_rejects_non_enum_values() -> None:
    with pytest.raises(ValueError, match="Coding verifier verdict must be one of"):
        coerce_coding_verifier_verdict("done")


def test_coding_verify_gate_blocks_with_typed_reason_when_exec_run_missing() -> None:
    state = _state()
    payload = _coding_resume_payload()
    payload["tool_calls_made"] = []
    payload["scratchpad"] = {
        "coding.plan_phases_executed": ["plan", "implement"],
        "coding.current_phase": "implement",
        "coding.open_issues_count": 0,
    }
    payload["coding_plan"]["phases"] = [
        {
            "name": "plan",
            "status": "done",
            "steps": ["restate the target"],
            "output": "parser plan",
        },
        {
            "name": "implement",
            "status": "active",
            "steps": ["edit parser"],
            "output": "patched parser",
        },
        {
            "name": "verify",
            "status": "pending",
            "steps": ["run tests"],
            "output": "",
        },
    ]
    payload["coding_plan"]["current_phase"] = "implement"
    state.module_state["coding"] = payload
    mode = CodingMode()
    mode.apply_mode_config(
        config={"max_self_corrections": 1},
        runner=None,
        profile=None,
    )
    services = _FakeServices()

    result = mode.execute(
        _ctx(
            _FakeLLMClient(
                responses=[
                    LLMResponse(
                        ok=True,
                        provider="fake",
                        model="fake-model",
                        output_text="implementation complete",
                        finish_reason="stop",
                    )
                ]
            ),
            _FakeCommandExecutor(),
            state=state,
            services=services,
            user_input="continue",
        )
    )

    assert result.status == "waiting_user"
    assert result.action_result is not None
    assert result.action_result.error is not None
    assert result.action_result.error.code == "verify_cap_exceeded"
    assert result.action_result.outputs["coding.verify_gate_blocks"] == 1
    assert result.action_result.outputs["coding.termination_reason"] == (
        "verify_cap_exceeded"
    )
    assert any(
        status.get("payload", {}).get("coding.verify_gate_reason") == "missing_exec_run"
        for status in services.statuses
    )


def test_coding_verify_phase_blocks_when_verifier_goal_is_unbound() -> None:
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=ToolCommand(
                    title="Run tests",
                    tool_name="exec.run",
                    args={"argv": ["pytest", "-q"]},
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="tests passed",
                    outputs={"report": "ok"},
                    artifact_refs=[ArtifactRef(ref="runtime://pytest-report.txt")],
                ),
            )
        ]
    )
    llm_client = _FakeLLMClient(
        responses=[
            _plan_response(
                """
                {
                  "goal": "inspect auth",
                  "phases": [
                    {"name": "implement", "status": "active", "steps": ["apply edits"], "output": ""},
                    {"name": "verify", "status": "pending", "steps": ["run tests"], "output": ""}
                  ],
                  "current_phase": "implement",
                  "scratchpad": [],
                  "completed_steps": [],
                  "open_issues": [],
                  "subtasks": []
                }
                """
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="tc-run",
                        name="exec.run",
                        arguments={"argv": ["pytest", "-q"]},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="implementation complete",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="verification complete",
                finish_reason="stop",
            ),
        ]
    )
    services = _FakeServices()

    result = CodingMode().execute(_ctx(llm_client, executor, services=services))

    assert result.status == "waiting_user"
    assert result.action_result is not None
    assert result.action_result.error is not None
    assert result.action_result.error.code == "verification_unbound"
    assert result.action_result.outputs["coding.verifier_verdict"] == (
        "verification_unbound"
    )
    assert any(
        status.get("payload", {}).get("coding.verify_gate_reason")
        == "verification_unbound"
        for status in services.statuses
    )


def test_coding_verify_phase_uses_typed_verifier_before_done() -> None:
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=ToolCommand(
                    title="Run tests",
                    tool_name="exec.run",
                    args={"argv": ["pytest", "-q"]},
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="tests passed",
                    outputs={"report": "ok"},
                    artifact_refs=[ArtifactRef(ref="runtime://pytest-report.txt")],
                ),
            )
        ]
    )
    llm_client = _FakeLLMClient(
        responses=[
            _plan_response(
                json.dumps(
                    {
                        "goal": "inspect auth",
                        "phases": [
                            {
                                "name": "implement",
                                "status": "active",
                                "steps": ["apply edits"],
                                "output": "",
                            },
                            {
                                "name": "verify",
                                "status": "pending",
                                "steps": ["run tests"],
                                "output": "",
                            },
                        ],
                        "current_phase": "implement",
                        "scratchpad": [],
                        "completed_steps": [],
                        "open_issues": [],
                        "subtasks": [],
                        "verifier_goal": _coding_verifier_goal().model_dump(
                            mode="json"
                        ),
                    }
                )
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="tc-run",
                        name="exec.run",
                        arguments={"argv": ["pytest", "-q"]},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="implementation complete",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="verification complete",
                finish_reason="stop",
            ),
        ]
    )
    services = _FakeServices()

    result = CodingMode().execute(_ctx(llm_client, executor, services=services))

    assert result.status == "done"
    assert result.message == "verification complete"
    assert any(
        status.get("payload", {}).get("coding.verifier_verdict") == "verified_complete"
        for status in services.statuses
    )
    assert any(
        status.get("payload", {}).get("coding.verifier_goal_id") == "coding-goal-1"
        for status in services.statuses
    )


def test_coding_verify_failure_blocks_on_repeated_identical_error() -> None:
    state = _state()
    payload = _coding_resume_payload()
    payload["scratchpad"] = {
        **dict(payload["scratchpad"]),
        "coding.self_corrections": 1,
        "coding.last_failure_summary": _typed_verifier_failure_summary(),
    }
    state.module_state["coding"] = payload

    result = CodingMode().execute(
        _ctx(
            _FakeLLMClient(
                responses=[
                    LLMResponse(
                        ok=True,
                        provider="fake",
                        model="fake-model",
                        output_text="",
                        tool_calls=[
                            ToolCall(
                                id="tc-run",
                                name="exec.run",
                                arguments={"argv": ["pytest", "-q"]},
                            )
                        ],
                        finish_reason="tool_calls",
                    )
                ]
            ),
            _FakeCommandExecutor(
                outcomes=[
                    CommandExecutionOutcome(
                        approved_command=ToolCommand(
                            title="Run tests",
                            tool_name="exec.run",
                            args={"argv": ["pytest", "-q"]},
                        ),
                        action_result=ActionResult(
                            command_id=new_uuid(),
                            status="failed",
                            summary="tests failed",
                        ),
                    )
                ]
            ),
            state=state,
            user_input="continue",
        )
    )

    assert result.status == "waiting_user"
    assert result.action_result is not None
    assert result.action_result.error is not None
    assert result.action_result.error.code == "blocked_novel_failure"


def test_coding_subtasks_dispatch_parallel_and_synthesize_outputs(monkeypatch) -> None:
    call_windows: list[tuple[str, float, float]] = []
    lock = threading.Lock()

    def _fake_invoke(runner, *, state, decision, user_input, logger, depth=0):
        del runner, user_input, logger, depth
        started = time.monotonic()
        time.sleep(0.08)
        finished = time.monotonic()
        with lock:
            call_windows.append((str(decision.objective), started, finished))
        return ExecutionResult(
            status="done",
            working_state=state,
            message=f"{decision.objective} complete",
            action_result=ActionResult(
                command_id=new_uuid(),
                status="success",
                summary=f"{decision.objective} complete",
                outputs={"diff": f"diff -- {decision.objective}"},
            ),
        )

    _patch_child_dispatch(monkeypatch, _fake_invoke)
    services = _FakeServices(
        runner=SimpleNamespace(profile=SimpleNamespace(mode_config={}))
    )
    llm_client = _FakeLLMClient(
        responses=[
            _subtask_plan_response(),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="tc-run",
                        name="exec.run",
                        arguments={"argv": ["pytest", "-q"]},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="implementation synthesized",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="verified",
                finish_reason="stop",
            ),
        ]
    )

    result = CodingMode().execute(
        _ctx(
            llm_client,
            _FakeCommandExecutor(),
            services=services,
            user_input="split work",
        )
    )

    assert result.status == "done"
    assert len(call_windows) == 2
    assert (
        call_windows[0][2] > call_windows[1][1]
        or call_windows[1][2] > call_windows[0][1]
    )
    synthesized_prompts = [
        message.content
        for message in llm_client.calls[1]["messages"]
        if message.role == "user"
    ]
    assert any("Subtask synthesis:" in item for item in synthesized_prompts)
    assert any("diff -- patch alpha" in item for item in synthesized_prompts)
    assert any("diff -- patch beta" in item for item in synthesized_prompts)


def test_coding_subtasks_conflicting_targets_serialize(monkeypatch) -> None:
    call_windows: list[tuple[str, float, float]] = []

    def _fake_invoke(runner, *, state, decision, user_input, logger, depth=0):
        del runner, user_input, logger, depth
        started = time.monotonic()
        time.sleep(0.03)
        finished = time.monotonic()
        call_windows.append((str(decision.objective), started, finished))
        return ExecutionResult(
            status="done",
            working_state=state,
            message=f"{decision.objective} complete",
        )

    _patch_child_dispatch(monkeypatch, _fake_invoke)
    services = _FakeServices(
        runner=SimpleNamespace(profile=SimpleNamespace(mode_config={}))
    )
    llm_client = _FakeLLMClient(
        responses=[
            _subtask_plan_response(first_target="src/a.py", second_target="src/a.py"),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="tc-run",
                        name="exec.run",
                        arguments={"argv": ["pytest", "-q"]},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="implementation synthesized",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="verified",
                finish_reason="stop",
            ),
        ]
    )

    result = CodingMode().execute(
        _ctx(
            llm_client,
            _FakeCommandExecutor(),
            services=services,
            user_input="split work",
        )
    )

    assert result.status == "done"
    assert len(call_windows) == 2
    assert call_windows[0][2] <= call_windows[1][1]


def test_coding_subtasks_stop_when_parent_budget_is_exhausted(monkeypatch) -> None:
    invoked: list[str] = []

    def _fake_invoke(runner, *, state, decision, user_input, logger, depth=0):
        del runner, user_input, logger, depth
        invoked.append(str(decision.objective))
        return ExecutionResult(
            status="done",
            working_state=state,
            message=f"{decision.objective} complete",
        )

    _patch_child_dispatch(monkeypatch, _fake_invoke)
    state = _state(tokens=1)
    services = _FakeServices(
        runner=SimpleNamespace(profile=SimpleNamespace(mode_config={}))
    )
    llm_client = _FakeLLMClient(
        responses=[
            _subtask_plan_response(first_target="src/a.py", second_target="src/a.py"),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="implementation complete",
                finish_reason="stop",
            ),
        ]
    )

    result = CodingMode().execute(
        _ctx(
            llm_client,
            _FakeCommandExecutor(),
            state=state,
            services=services,
            user_input="split work",
        )
    )

    assert result.status in {"active", "done", "continue", "waiting_user"}
    assert invoked == ["patch alpha"]


def test_coding_subtask_failure_escalates_to_open_issues(monkeypatch) -> None:
    def _fake_invoke(runner, *, state, decision, user_input, logger, depth=0):
        del runner, user_input, logger, depth
        if str(decision.objective) == "patch alpha":
            return ExecutionResult(
                status="error",
                working_state=state,
                message="alpha failed",
            )
        return ExecutionResult(
            status="done",
            working_state=state,
            message="beta complete",
        )

    _patch_child_dispatch(monkeypatch, _fake_invoke)
    state = _state()
    services = _FakeServices(
        runner=SimpleNamespace(profile=SimpleNamespace(mode_config={}))
    )
    llm_client = _FakeLLMClient(
        responses=[
            _subtask_plan_response(),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="implementation complete",
                finish_reason="stop",
            ),
        ]
    )

    result = CodingMode().execute(
        _ctx(
            llm_client,
            _FakeCommandExecutor(),
            state=state,
            services=services,
            user_input="split work",
        )
    )

    assert result.status in {"active", "done", "continue", "waiting_user"}
    assert any(
        "patch alpha: alpha failed" in issue
        for issue in state.module_state["coding"]["coding_plan"]["open_issues"]
    )


def test_coding_loop_parallelizes_two_independent_reads() -> None:
    executor = _TimedCommandExecutor(
        delays_by_path={"/src/alpha.py": 0.2, "/src/beta.py": 0.2}
    )
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
                        arguments={"path": "/src/alpha.py"},
                    ),
                    ToolCall(
                        id="tc-2",
                        name="file.read",
                        arguments={"path": "/src/beta.py"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
                finish_reason="stop",
            ),
        ]
    )
    handler = CodingMode()
    ctx = _ctx(llm_client, executor)

    started = time.monotonic()
    result = handler.execute(ctx)
    elapsed = time.monotonic() - started

    assert result.status == "done"
    assert elapsed < 0.35
    payload = result.action_result.outputs if result.action_result else {}
    assert payload["coding.parallel_fan_out_count"] == 2
    assert payload["coding.tool_calls_parallel"] == 2
    assert payload["coding.tool_calls_sequential"] == 0


def test_coding_loop_parallel_telemetry_is_emitted_in_status_event() -> None:
    executor = _TimedCommandExecutor(
        delays_by_path={"/src/alpha.py": 0.05, "/src/beta.py": 0.05}
    )
    services = _FakeServices()
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
                        arguments={"path": "/src/alpha.py"},
                    ),
                    ToolCall(
                        id="tc-2",
                        name="file.read",
                        arguments={"path": "/src/beta.py"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
                finish_reason="stop",
            ),
        ]
    )

    result = CodingMode().execute(_ctx(llm_client, executor, services=services))

    assert result.status == "done"
    payloads = [
        status.get("payload") or {}
        for status in services.statuses
        if status.get("source_phase") == "coding.loop"
    ]
    assert payloads
    assert any(
        payload.get("coding.parallel_fan_out_count") == 2
        and payload.get("coding.tool_calls_parallel") == 2
        and payload.get("coding.tool_calls_sequential") == 0
        for payload in payloads
    )


def test_coding_loop_serializes_write_then_read_same_path() -> None:
    executor = _TimedCommandExecutor(delays_by_path={"/src/alpha.py": 0.1})
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
                        name="file.write",
                        arguments={"path": "/src/alpha.py", "content": "updated"},
                    ),
                    ToolCall(
                        id="tc-2",
                        name="file.read",
                        arguments={"path": "/src/alpha.py"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
                finish_reason="stop",
            ),
        ]
    )
    handler = CodingMode()
    ctx = _ctx(llm_client, executor)

    result = handler.execute(ctx)

    assert result.status == "done"
    write_call = next(
        item for item in executor.call_windows if item[0] == "/src/alpha.py"
    )
    read_call = executor.call_windows[-1]
    assert write_call[2] <= read_call[1]
    payload = result.action_result.outputs if result.action_result else {}
    assert payload["coding.tool_calls_sequential"] == 2


def test_coding_loop_merges_parallel_results_in_tool_call_order() -> None:
    executor = _TimedCommandExecutor(
        delays_by_path={"/src/slow.py": 0.2, "/src/fast.py": 0.01}
    )
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="tc-slow",
                        name="file.read",
                        arguments={"path": "/src/slow.py"},
                    ),
                    ToolCall(
                        id="tc-fast",
                        name="file.read",
                        arguments={"path": "/src/fast.py"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
                finish_reason="stop",
            ),
        ]
    )
    handler = CodingMode()
    ctx = _ctx(llm_client, executor)

    result = handler.execute(ctx)

    assert result.status == "done"
    second_call_messages = llm_client.calls[1]["messages"]
    tool_messages = [
        message for message in second_call_messages if message.role == "tool"
    ]
    assert [message.meta["tool_call_id"] for message in tool_messages[-2:]] == [
        "tc-slow",
        "tc-fast",
    ]


# Disallowed tool → fail-closed


def test_coding_loop_disallowed_tool_exits_with_error() -> None:
    executor = _FakeCommandExecutor()
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
                        name="browser",
                        arguments={"url": "https://example.com"},
                    )
                ],
            )
        ]
    )
    handler = CodingMode()
    ctx = _ctx(llm_client, executor)
    result = handler.execute(ctx)

    assert result.status == "error"
    assert result.action_result is not None
    assert result.action_result.status == "blocked"
    assert "browser" in (result.message or "")
    # Executor should NOT have been called — allowlist check happens before execute
    assert len(executor.calls) == 0


# needs_user exit


def test_coding_loop_stops_on_needs_user() -> None:
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=MagicMock(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="needs_user",
                    summary="Please confirm deletion.",
                ),
            )
        ]
    )
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="tc-1", name="exec.run", arguments={"cmd": "rm -rf /build"}
                    )
                ],
            )
        ]
    )
    handler = CodingMode()
    ctx = _ctx(llm_client, executor)
    result = handler.execute(ctx)

    assert result.status == "waiting_user"
    assert "confirm" in (result.message or "").lower()


def test_coding_loop_preserves_confirmation_replay_state() -> None:
    class _ConfirmRequiredExecutor(_FakeCommandExecutor):
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
            del state, logger, preapproved, approve_only
            self.calls.append(command)
            self.include_reflect_values.append(include_reflect)
            return CommandExecutionOutcome(
                approved_command=command,
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="needs_user",
                    summary="Denied by policy: operation requires explicit confirmation",
                    error=ActionError(
                        code="CONFIRM_REQUIRED",
                        message=(
                            "Denied by policy: operation requires explicit confirmation"
                        ),
                        details={"tool": command.tool_name},
                    ),
                ),
            )

    state = _state()
    executor = _ConfirmRequiredExecutor()
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
                        arguments={"command": "python --version"},
                    )
                ],
            )
        ]
    )

    result = CodingMode().execute(_ctx(llm_client, executor, state=state))

    assert result.status == "waiting_user"
    assert state.pending_confirmation_command is not None
    assert state.pending_confirmation_command.tool_name == "exec.run"
    assert state.pending_confirmation_command.args == {"command": "python --version"}
    assert (
        "Reply exactly yes to confirm or exactly no to cancel."
        in state.post_action_user_message
    )
    assert "command=python --version" in state.post_action_user_message
    assert result.message == state.post_action_user_message


def test_coding_loop_replays_confirmed_pending_tool_without_llm_yes() -> None:
    class _PolicyAPI:
        def parse_confirmation_response(self, text: str) -> str:
            return "affirm" if text == "yes" else "unclear"

        def grant_once_from_confirmation(self, **kwargs) -> str:
            return "grant-1"

    state = _state()
    payload = _coding_resume_payload()
    payload["messages"] = [{"role": "user", "content": "verify python", "meta": {}}]
    payload["tool_calls_made"] = []
    payload["scratchpad"] = {
        **dict(payload["scratchpad"]),
        "coding.plan_phases_executed": ["plan", "implement"],
    }
    state.module_state["coding"] = payload
    state.pending_confirmation_command = ToolCommand(
        title="exec.run",
        tool_name="exec.run",
        args={"command": "python --version"},
    )
    state.status = "waiting_user"
    state.post_action_user_message = (
        "Policy confirmation required.\n"
        "exec.run (command=python --version)\n"
        "Reply exactly yes to confirm or exactly no to cancel."
    )
    services = _FakeServices(runner=SimpleNamespace(policy_api=_PolicyAPI()))
    executor = _FakeCommandExecutor()
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="verified",
                finish_reason="stop",
            )
        ]
    )

    result = CodingMode().execute(
        _ctx(
            llm_client,
            executor,
            state=state,
            services=services,
            user_input="yes",
        )
    )

    assert result.status == "done"
    assert state.pending_confirmation_command is None
    assert executor.calls[0].tool_name == "exec.run"
    assert executor.calls[0].inputs["confirmation_source"] == "policy_replay"
    assert executor.calls[0].inputs["confirmation_grant_id"] == "grant-1"
    first_llm_messages = llm_client.calls[0]["messages"]
    assert all(message.content != "yes" for message in first_llm_messages)
    assert any(message.role == "tool" for message in first_llm_messages)


def test_coding_loop_replays_confirmed_pending_tool_batch_without_llm_yes() -> None:
    class _PolicyAPI:
        def __init__(self) -> None:
            self._grant_ids = ["grant-1", "grant-2"]

        def parse_confirmation_response(self, text: str) -> str:
            return "affirm" if text == "yes" else "unclear"

        def grant_once_from_confirmation(self, **kwargs) -> str:
            del kwargs
            return self._grant_ids.pop(0)

    state = _state()
    payload = _coding_resume_payload()
    payload["messages"] = [{"role": "user", "content": "scaffold project", "meta": {}}]
    payload["tool_calls_made"] = []
    payload["scratchpad"] = {
        **dict(payload["scratchpad"]),
        "coding.plan_phases_executed": ["plan", "implement"],
    }
    state.module_state["coding"] = payload
    pending = ToolCommand(
        title="file.write",
        tool_name="file.write",
        args={"path": "demo/pyproject.toml", "body": "[project]"},
        inputs={"path": "demo/pyproject.toml", "body": "[project]"},
    )
    sibling = ToolCommand(
        title="file.write",
        tool_name="file.write",
        args={"path": "demo/README.md", "body": "# demo"},
        inputs={"path": "demo/README.md", "body": "# demo"},
    )
    state.pending_confirmation_command = attach_confirmation_replay_queue(
        pending, [sibling]
    )
    state.status = "waiting_user"
    state.post_action_user_message = confirmation_required_user_message(
        state.pending_confirmation_command
    )
    services = _FakeServices(runner=SimpleNamespace(policy_api=_PolicyAPI()))

    class _StateAwareExecutor(_FakeCommandExecutor):
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
            assert state.status == "active"
            return super().execute_command(
                state=state,
                command=command,
                logger=logger,
                preapproved=preapproved,
                approve_only=approve_only,
                include_reflect=include_reflect,
            )

    executor = _StateAwareExecutor()
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="batch verified",
                finish_reason="stop",
            )
        ]
    )

    result = CodingMode().execute(
        _ctx(
            llm_client,
            executor,
            state=state,
            services=services,
            user_input="yes",
        )
    )

    assert result.status == "done"
    assert state.pending_confirmation_command is None
    assert [command.args["path"] for command in executor.calls[:2]] == [
        "demo/pyproject.toml",
        "demo/README.md",
    ]
    assert [
        command.inputs["confirmation_grant_id"] for command in executor.calls[:2]
    ] == [
        "grant-1",
        "grant-2",
    ]
    assert all(
        command.inputs["confirmation_source"] == "policy_replay"
        for command in executor.calls[:2]
    )
    first_llm_messages = llm_client.calls[0]["messages"]
    assert all(message.content != "yes" for message in first_llm_messages)
    assert sum(1 for message in first_llm_messages if message.role == "tool") >= 2
    assert any(
        "do not repeat the same confirmed tool calls" in message.content
        for message in first_llm_messages
        if message.role == "user"
    )
    assert result.action_result is not None
    assert result.action_result.outputs["tool_execution_count"] >= 2
    assert [
        item["tool_name"] for item in result.action_result.outputs["tool_results"][:2]
    ] == ["file.write", "file.write"]


def test_coding_loop_replays_confirmed_batch_without_policy_grant_api() -> None:
    state = _state()
    payload = _coding_resume_payload()
    payload["messages"] = [{"role": "user", "content": "scaffold project", "meta": {}}]
    payload["tool_calls_made"] = []
    payload["scratchpad"] = {
        **dict(payload["scratchpad"]),
        "coding.plan_phases_executed": ["plan", "implement"],
    }
    state.module_state["coding"] = payload
    pending = ToolCommand(
        title="file.write",
        tool_name="file.write",
        args={"path": "demo/pyproject.toml", "body": "[project]"},
        inputs={"path": "demo/pyproject.toml", "body": "[project]"},
    )
    sibling = ToolCommand(
        title="file.write",
        tool_name="file.write",
        args={"path": "demo/README.md", "body": "# demo"},
        inputs={"path": "demo/README.md", "body": "# demo"},
    )
    state.pending_confirmation_command = attach_confirmation_replay_queue(
        pending, [sibling]
    )
    state.status = "waiting_user"
    state.post_action_user_message = confirmation_required_user_message(
        state.pending_confirmation_command
    )

    class _GrantRequiredExecutor(_FakeCommandExecutor):
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
            inputs = dict(getattr(command, "inputs", {}) or {})
            assert inputs.get("confirmation_source") == "policy_replay"
            assert str(inputs.get("confirmation_grant_id", "")).startswith(
                "local-confirmation-"
            )
            return super().execute_command(
                state=state,
                command=command,
                logger=logger,
                preapproved=preapproved,
                approve_only=approve_only,
                include_reflect=include_reflect,
            )

    executor = _GrantRequiredExecutor()
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="batch verified",
                finish_reason="stop",
            )
        ]
    )

    result = CodingMode().execute(
        _ctx(llm_client, executor, state=state, user_input="yes")
    )

    assert result.status == "done"
    assert state.pending_confirmation_command is None
    assert [command.args["path"] for command in executor.calls[:2]] == [
        "demo/pyproject.toml",
        "demo/README.md",
    ]


def test_coding_loop_replays_seeded_confirmation_decision_without_llm_regeneration() -> (
    None
):
    state = _state()
    payload = _coding_resume_payload()
    payload["messages"] = [{"role": "user", "content": "scaffold project", "meta": {}}]
    payload["tool_calls_made"] = []
    payload["scratchpad"] = {
        **dict(payload["scratchpad"]),
        "coding.plan_phases_executed": ["plan", "implement"],
    }
    state.module_state["coding"] = payload
    state.status = "active"
    seeded = ToolCommand(
        title="file.write",
        tool_name="file.write",
        args={"path": "demo/README.md", "body": "# demo"},
        inputs={
            "path": "demo/README.md",
            "body": "# demo",
            "confirmation_source": "policy_replay",
            "confirmation_grant_id": "grant-seeded",
        },
    )
    executor = _FakeCommandExecutor()
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="seeded write verified",
                finish_reason="stop",
            )
        ]
    )
    ctx = _ctx(llm_client, executor, state=state, user_input=None)
    ctx.decision.reason_code = "confirmation_replay"
    ctx.decision._seeded_commands = [seeded]

    result = CodingMode().execute(ctx)

    assert result.status == "done"
    assert [command.args["path"] for command in executor.calls[:1]] == [
        "demo/README.md"
    ]
    assert executor.calls[0].inputs["confirmation_source"] == "policy_replay"
    assert executor.calls[0].inputs["confirmation_grant_id"] == "grant-seeded"
    first_llm_messages = llm_client.calls[0]["messages"]
    assert any(message.role == "tool" for message in first_llm_messages)
    assert any(
        "do not repeat the same confirmed tool calls" in message.content
        for message in first_llm_messages
        if message.role == "user"
    )
    assert all(message.content != "yes" for message in first_llm_messages)
    assert result.action_result is not None
    assert result.action_result.outputs["tool_execution_count"] >= 1
    assert result.action_result.outputs["tool_results"][0]["tool_name"] == "file.write"


# job_pending exit


def test_coding_loop_stops_on_job_pending() -> None:
    from openminion.modules.brain.schemas import JobHandle, iso_now

    job = JobHandle(
        task_id="job-1",
        command_id=new_uuid(),
        provider="tool",
        status="pending",
        poll_after_ms=1000,
        created_at=iso_now(),
    )
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=MagicMock(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="job started",
                ),
                job=job,
            )
        ]
    )
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="tc-1", name="exec.run", arguments={"cmd": "make test"})
                ],
            )
        ]
    )
    handler = CodingMode()
    ctx = _ctx(llm_client, executor)
    result = handler.execute(ctx)

    assert result.status == "job_pending"


# Budget exhausted (token budget)


def test_coding_loop_stops_on_token_budget_exhausted() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="tc-1", name="file.read", arguments={"path": "a.py"})
                ],
                usage=UsageInfo(input_tokens=40001, output_tokens=0),
            )
        ]
    )
    executor = _FakeCommandExecutor()
    state = _state(tokens=40000, tool_calls=10, llm_calls_max=20)
    handler = CodingMode()
    ctx = _ctx(llm_client, executor, state=state)
    result = handler.execute(ctx)

    # Budget exhausted after consuming all tokens
    assert result.status in ("active",)
    assert result.action_result is not None
    assert "budget" in (result.message or "").lower()


def test_coding_loop_circular_pattern_returns_recoverable_result() -> None:
    llm_client = _FakeLLMClient()
    executor = _FakeCommandExecutor()
    handler = CodingMode()
    ctx = _ctx(llm_client, executor)
    outcome = AdaptiveToolLoopOutcome(
        profile_name="coding_v1",
        mode_name="act_loop_adaptive",
        state=AdaptiveToolLoopState(),
        termination_reason=ADAPTIVE_TERM_CIRCULAR_PATTERN,
        allowed_tools=frozenset({"file.write", "exec.run"}),
        error_message="circular pattern",
    )

    result = handler._result_from_outcome(
        ctx,
        outcome=outcome,
        allowed_tools=frozenset({"file.write", "exec.run"}),
    )

    assert result.status == "active"
    assert "repeated the same tool pattern" in (result.message or "")
    assert result.action_result is not None
    assert result.action_result.error is not None
    assert result.action_result.error.code == "coding_circular_tool_pattern"


# LLM error response


def test_coding_loop_stops_on_llm_not_ok() -> None:
    from openminion.modules.llm.schemas import ResponseError

    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=False,
                provider="fake",
                model="fake-model",
                output_text="",
                error=ResponseError(code="PROVIDER_ERROR", message="upstream error"),
            )
        ]
    )
    executor = _FakeCommandExecutor()
    handler = CodingMode()
    ctx = _ctx(llm_client, executor)
    result = handler.execute(ctx)

    assert result.status == "error"


# Runtime unavailable → prepare fails cleanly


def test_prepare_fails_when_no_raw_client() -> None:
    executor = _FakeCommandExecutor()
    services = _FakeServices()
    state = _state()
    ctx = ExecutionContext(
        state=state,
        decision=_decision(),
        user_input="test",
        logger=MagicMock(),
        options=SimpleNamespace(profile=None),
        llm_adapter=SimpleNamespace(),  # no .client attribute
        command_executor=executor,
        _services=services,
    )
    handler = CodingMode()
    prep = handler.prepare(ctx)
    assert prep.mode_result is not None
    assert prep.mode_result.status == "error"
    assert "llmclient" in (prep.mode_result.message or "").lower()


# prepare succeeds when client is present


def test_prepare_succeeds_with_valid_adapter() -> None:
    llm_client = _FakeLLMClient()
    executor = _FakeCommandExecutor()
    services = _FakeServices()
    state = _state()
    ctx = ExecutionContext(
        state=state,
        decision=_decision(),
        user_input="test",
        logger=MagicMock(),
        options=SimpleNamespace(profile=None),
        llm_adapter=_llm_adapter(llm_client),
        command_executor=executor,
        _services=services,
    )
    handler = CodingMode()
    prep = handler.prepare(ctx)
    assert prep.mode_result is None
    assert "started" in " ".join(s.get("detail_text", "") for s in services.statuses)


# Final text path emits telemetry with correct fields


def test_coding_loop_emits_telemetry_on_done() -> None:
    executor = _FakeCommandExecutor()
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="all done",
            )
        ]
    )
    services = _FakeServices()
    state = _state()
    ctx = _ctx(llm_client, executor, state=state, services=services)
    handler = CodingMode()
    result = handler.execute(ctx)

    assert result.status == "done"
    # Telemetry should be in the action result outputs
    assert result.action_result is not None
    tel = result.action_result.outputs
    assert "coding.loop_iterations" in tel
    assert "coding.termination_reason" in tel
    assert tel["coding.termination_reason"] == "final_text"
    assert "coding.allowed_tools" in tel


def test_coding_loop_emits_adaptive_status_payload_during_execution() -> None:
    executor = _FakeCommandExecutor()
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="tc-1", name="file.read", arguments={"path": "a.py"})
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
            ),
        ]
    )
    services = _FakeServices()
    ctx = _ctx(llm_client, executor, services=services)

    result = CodingMode().execute(ctx)

    assert result.status == "done"
    adaptive_payloads = [item.get("payload") or {} for item in services.statuses]
    assert any(
        payload.get("adaptive.profile") == "coding_v1" for payload in adaptive_payloads
    )
    assert any(payload.get("adaptive.mode") == "act" for payload in adaptive_payloads)
    assert any(
        payload.get("adaptive.tool_calls_total") == 1 for payload in adaptive_payloads
    )
