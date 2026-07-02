from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.schemas import (
    ActionError,
    ActionResult,
    AdaptiveBudgetConfig,
    BudgetCounters,
    Decision,
    JobHandle,
    MemoryConsolidationResult,
    ToolCommand,
    WorkingState,
    iso_now,
    new_uuid,
)
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_CIRCULAR_PATTERN,
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_CONFIDENT_COMPLETE,
    ADAPTIVE_TERM_DECOMPOSE_INVALID,
    ADAPTIVE_TERM_DECOMPOSE_REQUESTED,
    ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED,
    ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    ADAPTIVE_TERM_FINALIZATION_BLOCKED,
    ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
    ADAPTIVE_TERM_FINAL_TEXT,
    ADAPTIVE_TERM_ITERATION_CAP,
    ADAPTIVE_TERM_JOB_PENDING,
    ADAPTIVE_TERM_NEEDS_USER,
    ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED,
    ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
    DefaultAdaptiveToolLoopLLMRuntime,
    DirectToolTurnContext,
    PLAN_TOOL_ATTEMPTED_SCRATCHPAD_KEY,
    PLAN_TOOL_NAME,
    TOOL_REQUEST_TOOL_NAME,
    run_adaptive_tool_loop,
    semantic_batch_signature,
)
from openminion.modules.brain.loop.entry import decompose_tool_spec
from openminion.modules.brain.loop.tools.confirmation import (
    confirmation_required_user_message,
    extract_confirmation_replay_queue,
)
from openminion.modules.brain.loop.tools.response_payloads import (
    _FINALIZATION_STATUS_GUIDANCE,
)
from openminion.modules.brain.loop.tools.snapshot import LoopSnapshot
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.llm.schemas import (
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
    UsageInfo,
)


def test_finalization_guidance_preserves_user_requested_answer_format() -> None:
    assert "Preserve any user-specified final-answer format" in (
        _FINALIZATION_STATUS_GUIDANCE
    )
    assert "do not replace a requested format with a generic completion summary" in (
        _FINALIZATION_STATUS_GUIDANCE
    )


@dataclass
class _FakeRuntime:
    responses: list[LLMResponse] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    _index: int = 0

    def complete(
        self,
        *,
        messages,
        tools,
        model,
        tool_choice="auto",
        max_output_tokens=None,
        metadata=None,
    ):
        self.calls.append(
            {
                "messages": list(messages),
                "tools": list(tools),
                "model": model,
                "tool_choice": tool_choice,
                "max_output_tokens": max_output_tokens,
                "metadata": metadata,
            }
        )
        response = self.responses[self._index]
        self._index += 1
        return response


@dataclass
class _FakeClient:
    response: LLMResponse
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete(self, messages, tools, **kwargs):
        self.calls.append(
            {
                "messages": list(messages),
                "tools": list(tools or []),
                "kwargs": dict(kwargs),
            }
        )
        return self.response


def test_confident_complete_bare_json_signal_is_stripped_from_final_text() -> None:
    response_text = (
        "Weather summary.\n\n"
        '{\n"confident_complete": true,\n"reasoning": "Weather data returned."\n}'
    )
    client = _FakeClient(
        response=LLMResponse(
            ok=True,
            provider="fake",
            model="fake-model",
            output_text=response_text,
            assistant_messages=[Message(role="assistant", content=response_text)],
            finish_reason="stop",
        )
    )
    runtime = DefaultAdaptiveToolLoopLLMRuntime(client)

    response = runtime.complete(messages=[], tools=[], model="fake-model")

    assert response.output_text == "Weather summary."
    assert response.assistant_messages[-1].content == "Weather summary."
    assert response.confident_complete == {
        "complete": True,
        "reasoning": "Weather data returned.",
    }


def test_confident_complete_pure_json_response_stays_visible() -> None:
    response_text = '{"confident_complete": true, "reasoning": "Only control JSON."}'
    client = _FakeClient(
        response=LLMResponse(
            ok=True,
            provider="fake",
            model="fake-model",
            output_text=response_text,
            assistant_messages=[Message(role="assistant", content=response_text)],
            finish_reason="stop",
        )
    )
    runtime = DefaultAdaptiveToolLoopLLMRuntime(client)

    response = runtime.complete(messages=[], tools=[], model="fake-model")

    assert response.output_text == response_text
    assert response.assistant_messages[-1].content == response_text
    assert response.confident_complete is None


def test_non_signal_trailing_json_response_stays_visible() -> None:
    response_text = 'Here is JSON.\n{"city": "San Francisco"}'
    client = _FakeClient(
        response=LLMResponse(
            ok=True,
            provider="fake",
            model="fake-model",
            output_text=response_text,
            assistant_messages=[Message(role="assistant", content=response_text)],
            finish_reason="stop",
        )
    )
    runtime = DefaultAdaptiveToolLoopLLMRuntime(client)

    response = runtime.complete(messages=[], tools=[], model="fake-model")

    assert response.output_text == response_text
    assert response.assistant_messages[-1].content == response_text
    assert response.confident_complete is None
    assert response.finalization_status is None


def test_finalization_status_bare_json_signal_is_stripped_from_final_text() -> None:
    response_text = (
        'Done.\n\n{"status": "final_answer", "reasoning": "Complete enough to answer."}'
    )
    client = _FakeClient(
        response=LLMResponse(
            ok=True,
            provider="fake",
            model="fake-model",
            output_text=response_text,
            assistant_messages=[Message(role="assistant", content=response_text)],
            finish_reason="stop",
        )
    )
    runtime = DefaultAdaptiveToolLoopLLMRuntime(client)

    response = runtime.complete(messages=[], tools=[], model="fake-model")

    assert response.output_text == "Done."
    assert response.assistant_messages[-1].content == "Done."
    assert response.finalization_status == {
        "status": "final_answer",
        "reasoning": "Complete enough to answer.",
        "remaining_work": "",
        "blocking_reason": "",
    }


@dataclass
class _LoopContext:
    state: WorkingState
    outcomes: list[CommandExecutionOutcome] = field(default_factory=list)
    outcomes_by_path: dict[str, CommandExecutionOutcome] = field(default_factory=dict)
    commands: list[Any] = field(default_factory=list)
    statuses: list[dict[str, Any]] = field(default_factory=list)
    delays_by_path: dict[str, float] = field(default_factory=dict)
    call_windows: list[tuple[str, float, float]] = field(default_factory=list)
    session_api: Any | None = None
    _index: int = 0

    def execute_command(self, *, command, include_reflect: bool = False):
        del include_reflect
        self.commands.append(command)
        path = str(getattr(command, "args", {}).get("path", "") or "")
        started = time.monotonic()
        delay = float(self.delays_by_path.get(path, 0.0) or 0.0)
        if delay > 0:
            time.sleep(delay)
        if path and path in self.outcomes_by_path:
            outcome = self.outcomes_by_path[path]
        else:
            outcome = self.outcomes[self._index]
            self._index += 1
        finished = time.monotonic()
        self.call_windows.append((path, started, finished))
        return outcome

    def emit_status(self, **kwargs) -> None:
        self.statuses.append(dict(kwargs))


@dataclass
class _FakeSessionAPI:
    active_plan: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        self.events.append(
            {
                "session_id": session_id,
                "event_type": event_type,
                "payload": payload,
                "kwargs": dict(kwargs),
            }
        )

    def get_active_task_plan(self, session_id: str) -> dict[str, Any] | None:
        del session_id
        return dict(self.active_plan) if isinstance(self.active_plan, dict) else None


def _state(
    *,
    tool_calls: int = 5,
    tokens: int = 5000,
    llm_calls_max: int = 5,
    trace_id: str | None = None,
) -> WorkingState:
    return WorkingState(
        session_id="s-adaptive",
        agent_id="agent",
        trace_id=trace_id,
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=tool_calls,
            a2a_calls=0,
            tokens=tokens,
            time_ms=120000,
        ),
        llm_calls_max=llm_calls_max,
    )


def _tool_specs(*names: str) -> list[ToolSpec]:
    return [
        ToolSpec(
            name=name,
            description=name,
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        )
        for name in names
    ]


def _profile(
    *,
    allowed_tools: frozenset[str],
    max_iterations: int = 4,
    max_tool_calls_per_loop: int | None = None,
    allow_llm_recovery_after_tool_failure: bool = True,
    provider_parallel_tool_capacity: int = 1,
    profile_name: str = "shared_adaptive_test",
    adaptive_budget_config: AdaptiveBudgetConfig | None = None,
) -> AdaptiveToolLoopProfile:
    return AdaptiveToolLoopProfile(
        profile_name=profile_name,
        mode_name="act_adaptive",
        allowed_tools=allowed_tools,
        max_iterations=max_iterations,
        max_tool_calls_per_loop=max_tool_calls_per_loop,
        allow_llm_recovery_after_tool_failure=allow_llm_recovery_after_tool_failure,
        tool_choice="auto" if allowed_tools else "none",
        provider_parallel_tool_capacity=provider_parallel_tool_capacity,
        adaptive_budget_config=adaptive_budget_config,
    )


def test_engine_runs_multiple_rounds_and_appends_tool_messages() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1", name="file.read", arguments={"path": "app.py"}
                    ),
                    ToolCall(
                        id="call-2", name="exec.run", arguments={"cmd": "pytest -q"}
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "The requested page was fetched.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="read ok",
                    outputs={"content": "AUTH=1"},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="tests passed",
                    outputs={"exit_code": 0},
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read", "exec.run"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="inspect and test")],
        tool_specs=_tool_specs("file.read", "exec.run"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "done"
    assert [command.tool_name for command in loop_ctx.commands] == [
        "file.read",
        "exec.run",
    ]
    assert len(runtime.calls) == 2

    second_call_messages = runtime.calls[1]["messages"]
    tool_messages = [
        message for message in second_call_messages if message.role == "tool"
    ]
    assert len(tool_messages) == 2
    assert json.loads(tool_messages[0].content)["summary"] == "read ok"
    assert tool_messages[0].meta["tool_name"] == "file.read"
    assert any(
        (item.get("payload") or {}).get("adaptive.tool_calls_total") == 2
        for item in loop_ctx.statuses
    )


def test_engine_retries_empty_plan_lookup_after_substantive_tool_results() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="file.read",
                        arguments={"path": "pyproject.toml"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="plan-call",
                        name="plan.list",
                        arguments={},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="SOURCES\n- used prior results\n\nCHANGES\n- updated files\n\nTESTS\n- passed",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "The final answer used the prior task results.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        session_api=_FakeSessionAPI(),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="read ok",
                    outputs={"content": "[project]\nname = 'demo'"},
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read", "plan.list"}),
            max_iterations=4,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="inspect and answer")],
        tool_specs=_tool_specs("file.read", "plan.list"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text.startswith("SOURCES")
    assert [command.tool_name for command in loop_ctx.commands] == ["file.read"]
    assert runtime.calls[1]["tools"]
    assert runtime.calls[2]["tools"]
    assert any(
        "Do not call plan.list" in message.content
        for message in runtime.calls[2]["messages"]
        if message.role == "system"
    )
    assert outcome.state.scratchpad["empty_plan_lookup_diversion_count"] == 1


def test_engine_recovers_seeded_policy_denial_with_suggested_tool() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="file.read",
                        arguments={"path": "pyproject.toml"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="SOURCES\n- used structured file evidence\n\nCHANGES\n- none\n\nTESTS\n- not needed",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Recovered from the blocked seeded command.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="failed",
                    summary="redirection blocked",
                    error=ActionError(
                        code="POLICY_DENIED",
                        message="redirection blocked",
                        details={
                            "tool_name": "exec.run",
                            "suggested_tool": "file.read",
                            "suggested_fix": "Use file.read for workspace inspection.",
                        },
                    ),
                    outputs={},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="read ok",
                    outputs={"content": "[project]\nname = 'demo'"},
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"exec.run", "file.read"}),
            max_iterations=4,
            allow_llm_recovery_after_tool_failure=True,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="inspect and answer")],
        tool_specs=_tool_specs("exec.run", "file.read"),
        seeded_commands=[
            ToolCommand(
                title="grep project scripts",
                kind="tool",
                tool_name="exec.run",
                args={"command": "grep project.scripts pyproject.toml 2>/dev/null"},
                inputs={"command": "grep project.scripts pyproject.toml 2>/dev/null"},
            )
        ],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text.startswith("SOURCES")
    assert [command.tool_name for command in loop_ctx.commands] == [
        "exec.run",
        "file.read",
    ]
    assert any(
        "Retry the same user task using file.read" in message.content
        for message in runtime.calls[0]["messages"]
        if message.role == "system"
    )


def test_engine_recovers_nested_seeded_policy_denial_before_replaying_siblings() -> (
    None
):
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="file.read",
                        arguments={"path": "README.md"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="SOURCES\n- structured file evidence\n\nCHANGES\n- none\n\nTESTS\n- not needed",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Recovered from the nested policy denial.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="blocked",
                    summary="redirection blocked",
                    outputs={
                        "error": {
                            "code": "POLICY_DENIED",
                            "message": "redirections are not supported",
                            "details": {
                                "command": "cat pyproject.toml && cat README.md 2>/dev/null",
                                "suggested_tool": "file.list_dir",
                                "suggested_fix": (
                                    "Use file.list_dir and file.read instead."
                                ),
                            },
                        }
                    },
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="read ok",
                    outputs={"content": "usage: task-summary"},
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"exec.run", "file.list_dir", "file.read"}),
            max_iterations=4,
            allow_llm_recovery_after_tool_failure=True,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="inspect and answer")],
        tool_specs=_tool_specs("exec.run", "file.list_dir", "file.read"),
        seeded_commands=[
            ToolCommand(
                title="read project files",
                kind="tool",
                tool_name="exec.run",
                args={"command": "cat pyproject.toml && cat README.md 2>/dev/null"},
                inputs={"command": "cat pyproject.toml && cat README.md 2>/dev/null"},
            ),
            ToolCommand(
                title="stale sibling shell read",
                kind="tool",
                tool_name="exec.run",
                args={"command": "find . -type f"},
                inputs={"command": "find . -type f"},
            ),
        ],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text.startswith("SOURCES")
    assert [command.tool_name for command in loop_ctx.commands] == [
        "exec.run",
        "file.read",
    ]
    assert any(
        "Retry the same user task using file.list_dir" in message.content
        for message in runtime.calls[0]["messages"]
        if message.role == "system"
    )


def test_engine_recovers_seeded_invalid_workdir_with_llm_guidance() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="exec.run",
                        arguments={
                            "command": "python -m pytest -q tests",
                            "workdir": "/tmp/workspace",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="SOURCES\n- verified\n\nCHANGES\n- none\n\nTESTS\n- pytest passed",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Recovered with an absolute workdir.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="blocked",
                    summary="workdir does not exist or is not a directory",
                    outputs={
                        "error": {
                            "code": "INVALID_ARGUMENT",
                            "message": "workdir does not exist or is not a directory",
                            "details": {"workdir": "research-project-123"},
                        }
                    },
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="pytest passed",
                    outputs={"exit_code": 0},
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"exec.run", "file.list_dir", "file.read"}),
            max_iterations=4,
            allow_llm_recovery_after_tool_failure=True,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="run tests")],
        tool_specs=_tool_specs("exec.run", "file.list_dir", "file.read"),
        seeded_commands=[
            ToolCommand(
                title="run pytest",
                kind="tool",
                tool_name="exec.run",
                args={
                    "command": "python -m pytest -q tests",
                    "workdir": "research-project-123",
                },
                inputs={
                    "command": "python -m pytest -q tests",
                    "workdir": "research-project-123",
                },
            )
        ],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert [command.tool_name for command in loop_ctx.commands] == [
        "exec.run",
        "exec.run",
    ]
    assert any(
        "absolute workspace directory" in message.content
        for message in runtime.calls[0]["messages"]
        if message.role == "system"
    )


def test_engine_recovers_blocked_policy_denial_with_suggested_tool() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="exec.run",
                        arguments={"command": "find . -type f | head -5"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="file.find",
                        arguments={"path": ".", "pattern": "*.py"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="SOURCES\n- used structured file.find evidence\n\nCHANGES\n- none\n\nTESTS\n- not needed",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Recovered from the blocked policy denial.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="blocked",
                    summary="Denied by policy: command 'find' is not allowlisted",
                    error=ActionError(
                        code="POLICY_DENIED",
                        message="Denied by policy: command 'find' is not allowlisted",
                        details={
                            "tool_name": "exec.run",
                            "suggested_tool": "file.find",
                            "suggested_fix": (
                                "Use file.find instead of shelling out to find."
                            ),
                        },
                    ),
                    outputs={},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="found files",
                    outputs={"matches": [{"path": "task_summary/report.py"}]},
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"exec.run", "file.find"}),
            max_iterations=4,
            allow_llm_recovery_after_tool_failure=True,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="inspect and answer")],
        tool_specs=_tool_specs("exec.run", "file.find"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text.startswith("SOURCES")
    assert [command.tool_name for command in loop_ctx.commands] == [
        "exec.run",
        "file.find",
    ]
    assert any(
        "Retry the task using file.find" in message.content
        for message in runtime.calls[1]["messages"]
        if message.role == "system"
    )


def test_engine_preserves_confirm_required_sibling_batch_for_replay() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="file.write",
                        arguments={"path": "demo/pyproject.toml", "body": "[project]"},
                    ),
                    ToolCall(
                        id="call-2",
                        name="file.write",
                        arguments={"path": "demo/README.md", "body": "# demo"},
                    ),
                    ToolCall(
                        id="call-3",
                        name="file.write",
                        arguments={
                            "path": "demo/sample_tasks.csv",
                            "body": "owner,title\nalice,ship\n",
                        },
                    ),
                    ToolCall(
                        id="call-4",
                        name="file.write",
                        arguments={
                            "path": "demo/task_summary/__init__.py",
                            "body": '"""pkg"""',
                        },
                    ),
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    def _needs_user(path: str, body: str) -> CommandExecutionOutcome:
        return CommandExecutionOutcome(
            approved_command=ToolCommand(
                title=f"write {path}",
                tool_name="file.write",
                args={"path": path, "body": body},
                inputs={"path": path, "body": body},
                requires_confirmation=True,
            ),
            action_result=ActionResult(
                command_id=new_uuid(),
                status="needs_user",
                summary="confirm write",
                error=ActionError(code="CONFIRM_REQUIRED", message="confirm write"),
            ),
        )

    @dataclass
    class _ConfirmingLoopContext(_LoopContext):
        def execute_command(self, *, command, include_reflect: bool = False):
            outcome = super().execute_command(
                command=command,
                include_reflect=include_reflect,
            )
            action_result = getattr(outcome, "action_result", None)
            if (
                action_result is not None
                and str(getattr(action_result, "status", "") or "") == "needs_user"
                and getattr(getattr(action_result, "error", None), "code", "")
                == "CONFIRM_REQUIRED"
                and self.state.pending_confirmation_command is None
            ):
                self.state.pending_confirmation_command = (
                    outcome.approved_command.model_copy(deep=True)
                )
                self.state.post_action_user_message = (
                    confirmation_required_user_message(
                        self.state.pending_confirmation_command
                    )
                )
            return outcome

    loop_ctx = _ConfirmingLoopContext(
        state=_state(tool_calls=8),
        outcomes_by_path={
            "demo/pyproject.toml": _needs_user("demo/pyproject.toml", "[project]"),
            "demo/README.md": _needs_user("demo/README.md", "# demo"),
            "demo/sample_tasks.csv": _needs_user(
                "demo/sample_tasks.csv", "owner,title\nalice,ship\n"
            ),
            "demo/task_summary/__init__.py": _needs_user(
                "demo/task_summary/__init__.py", '"""pkg"""'
            ),
        },
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.write"}),
            provider_parallel_tool_capacity=2,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="create demo project")],
        tool_specs=_tool_specs("file.write"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_NEEDS_USER
    pending = loop_ctx.state.pending_confirmation_command
    assert pending is not None
    assert pending.args == {"path": "demo/pyproject.toml", "body": "[project]"}
    queued = extract_confirmation_replay_queue(pending)
    assert [item.args for item in queued] == [
        {"path": "demo/README.md", "body": "# demo"},
        {"path": "demo/sample_tasks.csv", "body": "owner,title\nalice,ship\n"},
        {"path": "demo/task_summary/__init__.py", "body": '"""pkg"""'},
    ]
    assert "covers 3 queued commands" in loop_ctx.state.post_action_user_message


def test_engine_mid_loop_decompose_non_empty_returns_structured_handoff() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="decompose-call",
                        name="decompose",
                        arguments={
                            "subtasks": [
                                {
                                    "id": "inspect",
                                    "description": "Inspect current files",
                                    "inputs": {"path": "openminion"},
                                },
                                {
                                    "id": "summarize",
                                    "description": "Summarize findings",
                                    "depends_on": ["inspect"],
                                },
                            ]
                        },
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="file.read",
                    args={"path": "README.md"},
                ),
                action_result=ActionResult(
                    command_id="read-command",
                    status="success",
                    summary="read ok",
                    outputs={"content": "README"},
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"decompose"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="split the remaining work")],
        tool_specs=[decompose_tool_spec()],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_DECOMPOSE_REQUESTED
    assert outcome.decompose_subtasks == [
        {
            "subtask_id": "inspect",
            "goal": "Inspect current files",
            "inputs": {"path": "openminion"},
            "depends_on": [],
            "suggested_mode": None,
            "priority": 0,
        },
        {
            "subtask_id": "summarize",
            "goal": "Summarize findings",
            "inputs": {},
            "depends_on": ["inspect"],
            "suggested_mode": None,
            "priority": 0,
        },
    ]
    assert loop_ctx.commands == []


def test_engine_mid_loop_decompose_empty_stays_in_loop_without_tool_execution() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="decompose-call",
                        name="decompose",
                        arguments={"subtasks": []},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done without decomposition",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="file.read",
                    args={"path": "README.md"},
                ),
                action_result=ActionResult(
                    command_id="read-command",
                    status="success",
                    summary="read ok",
                    outputs={"content": "README"},
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"decompose"}), max_iterations=3),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="continue")],
        tool_specs=[decompose_tool_spec()],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "done without decomposition"
    assert loop_ctx.commands == []
    assert any(
        message.role == "tool" and message.meta.get("tool_name") == "decompose"
        for message in outcome.state.messages
    )


def test_engine_mid_loop_decompose_malformed_fails_closed() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="decompose-call",
                        name="decompose",
                        arguments={"subtasks": [{"id": "missing-description"}]},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="file.read",
                    args={"path": "README.md"},
                ),
                action_result=ActionResult(
                    command_id="read-command",
                    status="success",
                    summary="read ok",
                    outputs={"content": "README"},
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"decompose"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="continue")],
        tool_specs=[decompose_tool_spec()],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_DECOMPOSE_INVALID
    assert "description" in str(outcome.error_message)
    assert loop_ctx.commands == []


def test_engine_mid_loop_decompose_mixed_tool_calls_retries_once_then_executes_regular_tools() -> (
    None
):
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="decompose-call",
                        name="decompose",
                        arguments={"subtasks": []},
                    ),
                    ToolCall(
                        id="read-call",
                        name="file.read",
                        arguments={"path": "README.md"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="read-call-2",
                        name="file.read",
                        arguments={"path": "README.md"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="file.read",
                    args={"path": "README.md"},
                ),
                action_result=ActionResult(
                    command_id="read-command",
                    status="success",
                    summary="read ok",
                    outputs={"content": "README"},
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"decompose", "file.read"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="continue")],
        tool_specs=[decompose_tool_spec(), *_tool_specs("file.read")],
    )

    assert outcome.termination_reason != ADAPTIVE_TERM_DECOMPOSE_INVALID
    assert loop_ctx.commands
    assert loop_ctx.commands[0].tool_name == "file.read"


def test_engine_mid_loop_decompose_mixed_tool_calls_repeated_still_fail_closed() -> (
    None
):
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="decompose-call",
                        name="decompose",
                        arguments={"subtasks": []},
                    ),
                    ToolCall(
                        id="read-call",
                        name="file.read",
                        arguments={"path": "README.md"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="decompose-call-2",
                        name="decompose",
                        arguments={"subtasks": []},
                    ),
                    ToolCall(
                        id="read-call-2",
                        name="file.read",
                        arguments={"path": "README.md"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(tool_calls=4))

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"decompose", "file.read"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="continue")],
        tool_specs=[decompose_tool_spec(), *_tool_specs("file.read")],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_DECOMPOSE_INVALID
    assert "mixed" in outcome.state.scratchpad["adaptive.decompose_error"]["reason"]
    assert loop_ctx.commands == []


def test_engine_does_not_auto_decompose_from_tool_call_count() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="read-a", name="file.read", arguments={"path": "a.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="read-b", name="file.read", arguments={"path": "b.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Both tool results were incorporated.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="a"
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="b"
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"decompose", "file.read"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(role="user", content="read several files and answer")
        ],
        tool_specs=[decompose_tool_spec(), *_tool_specs("file.read")],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.decompose_subtasks is None
    assert [command.tool_name for command in loop_ctx.commands] == [
        "file.read",
        "file.read",
    ]


def test_engine_handles_plan_control_tool_without_tool_budget_debit() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="plan-call",
                        name=PLAN_TOOL_NAME,
                        arguments={
                            "action": "declare",
                            "plan_id": "apd-proof",
                            "objective": "Research Japan trip requirements",
                            "steps": [
                                {
                                    "step_id": "entry",
                                    "description": "Research entry requirements",
                                    "depends_on": [],
                                    "estimated_difficulty": "low",
                                    "tool_families": ["web", "search"],
                                }
                            ],
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="planned",
                finish_reason="stop",
            ),
        ]
    )
    session_api = _FakeSessionAPI()
    loop_ctx = _LoopContext(
        state=_state(tool_calls=2),
        session_api=session_api,
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset(), max_iterations=3),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="plan this")],
        tool_specs=[],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.state.total_tool_calls == 0
    assert loop_ctx.state.budgets_remaining.tool_calls == 2
    assert PLAN_TOOL_NAME in {spec.name for spec in runtime.calls[0]["tools"]}
    assert [event["event_type"] for event in session_api.events] == [
        "task_plan.declared"
    ]


def test_engine_marks_plan_tool_attempt_even_when_control_call_fails() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="plan-call",
                        name=PLAN_TOOL_NAME,
                        arguments={
                            "action": "step_completed",
                            "plan_id": "plan-1",
                            "step_id": "missing",
                            "outcome": "success",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="I completed the missing step.",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "The invalid plan event was surfaced.",
                },
                finish_reason="stop",
            ),
        ]
    )
    session_api = _FakeSessionAPI(
        active_plan={
            "plan_id": "plan-1",
            "objective": "Prove structural plan failure",
            "status": "active",
            "steps": [
                {
                    "step_id": "entry",
                    "description": "Valid active step",
                    "status": "pending",
                    "depends_on": [],
                    "estimated_difficulty": "low",
                    "tool_families": ["file"],
                }
            ],
        }
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=2),
        session_api=session_api,
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset(), max_iterations=3),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="complete the plan")],
        tool_specs=[],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.state.scratchpad[PLAN_TOOL_ATTEMPTED_SCRATCHPAD_KEY] is True
    assert [event["event_type"] for event in session_api.events] == [
        "task_plan.invalid_trailer"
    ]


def test_engine_does_not_complete_plan_step_from_prose_only() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="The entry step is done.",
                finish_reason="stop",
            )
        ]
    )
    session_api = _FakeSessionAPI(
        active_plan={
            "plan_id": "plan-1",
            "objective": "Keep plan updates typed",
            "status": "active",
            "steps": [
                {
                    "step_id": "entry",
                    "description": "Valid active step",
                    "status": "pending",
                    "depends_on": [],
                    "estimated_difficulty": "low",
                    "tool_families": ["file"],
                }
            ],
        }
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=2),
        session_api=session_api,
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset(), max_iterations=2),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="complete the entry step")],
        tool_specs=[],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert session_api.events == []


def test_default_runtime_parses_task_plan_trailer() -> None:
    response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text=(
            "I will track this plan.\n"
            '<task_plan>{"plan_id":"plan-1","objective":"Ship APD","steps":['
            '{"step_id":"inspect","description":"Inspect seams",'
            '"tool_families":["file"],"estimated_difficulty":"low"}'
            "]}</task_plan>"
        ),
        assistant_messages=[Message(role="assistant", content="placeholder")],
    )
    client = _FakeClient(response=response)
    runtime = DefaultAdaptiveToolLoopLLMRuntime.from_adapter(client)

    normalized = runtime.complete(
        messages=[Message(role="user", content="plan this")],
        tools=[],
        model="fake-model",
    )

    assert normalized.output_text == "I will track this plan."
    assert normalized.assistant_messages[-1].content == "I will track this plan."
    assert normalized.task_plan is not None
    assert normalized.task_plan["plan_id"] == "plan-1"
    assert normalized.task_plan["steps"][0]["tool_families"] == ["file"]


def test_default_runtime_parses_inline_task_plan_control_block() -> None:
    response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text=(
            "I will track this plan.\n"
            '<task_plan>{"plan_id":"plan-1","objective":"Ship APD","steps":['
            '{"step_id":"inspect","description":"Inspect seams",'
            '"tool_families":["file"],"estimated_difficulty":"low"}'
            "]}</task_plan>\n\n"
            "Step 1 is complete."
        ),
        assistant_messages=[Message(role="assistant", content="placeholder")],
    )
    client = _FakeClient(response=response)
    runtime = DefaultAdaptiveToolLoopLLMRuntime.from_adapter(client)

    normalized = runtime.complete(
        messages=[Message(role="user", content="plan this")],
        tools=[],
        model="fake-model",
    )

    assert normalized.output_text == "I will track this plan.\n\nStep 1 is complete."
    assert normalized.assistant_messages[-1].content == normalized.output_text
    assert normalized.task_plan is not None
    assert normalized.task_plan["plan_id"] == "plan-1"


def test_default_runtime_parses_plan_revision_trailer() -> None:
    response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text=(
            "I revised the plan.\n"
            '<plan_revision>{"plan_id":"plan-1","reason":"scope changed",'
            '"revised_steps":[{"step_id":"ship","description":"Ship it",'
            '"tool_families":["code"]}]}</plan_revision>'
        ),
    )
    client = _FakeClient(response=response)
    runtime = DefaultAdaptiveToolLoopLLMRuntime.from_adapter(client)

    normalized = runtime.complete(
        messages=[Message(role="user", content="revise")],
        tools=[],
        model="fake-model",
    )

    assert normalized.output_text == "I revised the plan."
    assert normalized.task_plan_revision is not None
    assert normalized.task_plan_revision["plan_id"] == "plan-1"
    assert normalized.task_plan_revision["reason"] == "scope changed"
    assert normalized.task_plan_revision["revised_steps"][0]["step_id"] == "ship"


def test_tool_request_activates_inactive_schema_for_next_loop_call() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="request-fetch",
                        name=TOOL_REQUEST_TOOL_NAME,
                        arguments={"name": "web.fetch"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="fetch",
                        name="web.fetch",
                        arguments={"url": "https://example.test"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Both requested files were read.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=5, llm_calls_max=10),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="fetched",
                    outputs={"url": "https://example.test"},
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"web.search", "web.fetch"}),
            max_iterations=4,
            profile_name="general_adaptive_v1",
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="fetch the page")],
        tool_specs=_tool_specs("web.search"),
        requestable_tool_specs=_tool_specs("web.search", "web.fetch"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert [command.tool_name for command in loop_ctx.commands] == ["web.fetch"]
    assert [spec.name for spec in runtime.calls[0]["tools"]] == [
        "web.search",
        TOOL_REQUEST_TOOL_NAME,
        PLAN_TOOL_NAME,
    ]
    assert "web.fetch" in [spec.name for spec in runtime.calls[1]["tools"]]
    assert runtime.calls[1]["tool_choice"] == "auto"
    tool_messages = [
        message for message in runtime.calls[1]["messages"] if message.role == "tool"
    ]
    assert json.loads(tool_messages[-1].content)["outputs"] == {
        "tool_name": "web.fetch",
        "activated": True,
    }
    assert loop_ctx.state.budgets_remaining.tool_calls == 4
    assert outcome.final_text == "done"


def test_engine_confident_complete_exits_early_with_final_text() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="All done.",
                confident_complete={
                    "complete": True,
                    "reasoning": "Verified enough evidence to stop.",
                },
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(tool_calls=4))

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="finish the task")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_CONFIDENT_COMPLETE
    assert outcome.final_text == "All done."
    assert outcome.confident_complete_reasoning == "Verified enough evidence to stop."
    assert outcome.telemetry_payload()["adaptive.termination_reason"] == (
        ADAPTIVE_TERM_CONFIDENT_COMPLETE
    )
    assert any(
        message.role == "system"
        and "structured confident_complete signal" in message.content
        for message in runtime.calls[0]["messages"]
    )


def test_engine_pending_turn_context_trailer_is_recorded_on_final_text() -> None:
    client = _FakeClient(
        response=LLMResponse(
            ok=True,
            provider="fake",
            model="fake-model",
            output_text=(
                "I found Oakland from your IP. Would you like me to get the current "
                "weather in Oakland for you?\n<pending_turn_context>"
                '{"original_user_request": "tell me your location like ip and city?", '
                '"active_work_summary": "If the user agrees, provide current weather for Oakland.", '
                '"known_context": {"location": "Oakland", "region": "California"}, '
                '"missing_fields": [], "artifact_refs": [], "response_preferences": {}}'
                "</pending_turn_context>"
            ),
            finish_reason="stop",
        )
    )
    loop_ctx = _LoopContext(state=_state(tool_calls=2))

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"location.get"})),
        runtime=DefaultAdaptiveToolLoopLLMRuntime(client),
        model="fake-model",
        initial_messages=[
            Message(role="user", content="tell me your location like ip and city?")
        ],
        tool_specs=_tool_specs("location.get"),
    )

    assert outcome.final_text == (
        "I found Oakland from your IP. Would you like me to get the current weather in Oakland for you?"
    )
    assert outcome.pending_turn_context == {
        "original_user_request": "tell me your location like ip and city?",
        "active_work_summary": "If the user agrees, provide current weather for Oakland.",
        "known_context": {"location": "Oakland", "region": "California"},
        "missing_fields": [],
        "artifact_refs": [],
        "response_preferences": {},
    }
    assert outcome.telemetry_payload()["pending_turn_context"]["known_context"] == {
        "location": "Oakland",
        "region": "California",
    }
    assert any(
        message.role == "system"
        and "structured pending_turn_context signal" in message.content
        for message in client.calls[0]["messages"]
    )


def test_engine_emits_structured_turn_progress_payloads() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="location.get", arguments={}),
                ],
                usage=UsageInfo(input_tokens=400, output_tokens=400, total_tokens=800),
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
                usage=UsageInfo(
                    input_tokens=300,
                    output_tokens=400,
                    total_tokens=700,
                ),
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=2),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="location ok",
                    outputs={"city": "Seattle"},
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"location.get"}), max_iterations=12),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="where am i?")],
        tool_specs=_tool_specs("location.get"),
    )

    status_payloads = [item.get("payload") or {} for item in loop_ctx.statuses]
    assert any(
        payload.get("turn.llm_call_count") == 1
        and payload.get("total_tokens_used") == 0
        and payload.get("turn.progress_phase") == "thinking..."
        for payload in status_payloads
    )
    assert any(
        payload.get("turn.llm_call_count") == 1
        and payload.get("total_tokens_used") == 800
        and payload.get("total_input_tokens_used") == 400
        and payload.get("total_output_tokens_used") == 400
        and payload.get("turn.tool_name") == "location.get"
        for payload in status_payloads
    )
    assert outcome.telemetry_payload()["total_tokens_used"] == 1500
    assert outcome.telemetry_payload()["total_input_tokens_used"] == 700
    assert outcome.telemetry_payload()["total_output_tokens_used"] == 800


def test_engine_confident_complete_without_final_text_continues() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                confident_complete={
                    "complete": True,
                    "reasoning": "I think this is done.",
                },
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Now with the real answer.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(tool_calls=4))

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"}), max_iterations=3),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="finish the task")],
        tool_specs=_tool_specs("file.read"),
    )

    assert len(runtime.calls) == 2
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "Now with the real answer."
    retry_messages = runtime.calls[1]["messages"]
    assert any(
        message.role == "system" and "without a final answer" in message.content
        for message in retry_messages
    )


def test_engine_unexecutable_tool_envelope_retries_for_plain_text() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text=(
                    "[system: UNEXECUTABLE_TOOL_ENVELOPE]\n"
                    "The model generated a tool envelope that could not be executed.\n"
                    "Target: unknown\nReason: unparseable"
                ),
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Plain text answer after envelope retry.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(tool_calls=4))

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"}), max_iterations=3),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="finish with plain text")],
        tool_specs=_tool_specs("file.read"),
    )

    assert len(runtime.calls) == 2
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "Plain text answer after envelope retry."
    retry_messages = runtime.calls[1]["messages"]
    assert any(
        message.role == "system"
        and "unexecutable tool envelope" in message.content.lower()
        for message in retry_messages
    )


def test_engine_confident_complete_with_tool_calls_prefers_tools() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Done, but also one more tool.",
                confident_complete={
                    "complete": True,
                    "reasoning": "Likely complete after one final check.",
                },
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="file.read",
                        arguments={"path": "README.md"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Verified and complete.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="read ok",
                    outputs={"content": "ok"},
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="finish the task")],
        tool_specs=_tool_specs("file.read"),
    )

    assert [command.tool_name for command in loop_ctx.commands] == ["file.read"]
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "Verified and complete."


def test_engine_watch_outcome_is_recorded_on_final_text() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Deployment is unhealthy.",
                watch_outcome={
                    "condition_met": True,
                    "summary": "Deployment is unhealthy.",
                },
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(tool_calls=2))

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"web.fetch", "time"}),
            max_iterations=3,
            profile_name="watch_check_v1",
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="check the deployment")],
        tool_specs=_tool_specs("web.fetch", "time"),
    )

    assert outcome.final_text == "Deployment is unhealthy."
    assert outcome.watch_condition_met is True
    assert outcome.watch_summary == "Deployment is unhealthy."
    assert outcome.telemetry_payload()["watch.condition_met"] is True
    assert any(
        message.role == "system"
        and "structured watch_outcome signal" in message.content
        for message in runtime.calls[0]["messages"]
    )


def test_engine_watch_action_turn_uses_standard_completion_without_watch_outcome() -> (
    None
):
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Restarted the deployment and verified the pods are healthy.",
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(tool_calls=2))

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.write", "exec.run", "web.fetch"}),
            max_iterations=3,
            profile_name="watch_action_v1",
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(role="user", content="Run kubectl rollout restart deployment/app")
        ],
        tool_specs=_tool_specs("file.write", "exec.run", "web.fetch"),
    )

    assert (
        outcome.final_text
        == "Restarted the deployment and verified the pods are healthy."
    )
    assert outcome.watch_condition_met is None
    assert any(
        message.role == "system" and "watch-triggered action turns" in message.content
        for message in runtime.calls[0]["messages"]
    )
    assert not any(
        message.role == "system"
        and str(message.content or "").strip().startswith("For watch-check turns")
        for message in runtime.calls[0]["messages"]
    )


def test_engine_session_work_summary_is_recorded_on_final_text() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Implemented authentication flow.",
                session_work_summary={
                    "summary": (
                        "Built authentication flow in auth.py and added login tests. "
                        "Next step is wiring token refresh into the API client."
                    )
                },
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(tool_calls=2))

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="finish the auth work")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.final_text == "Implemented authentication flow."
    assert outcome.session_work_summary is not None
    assert "Built authentication flow in auth.py" in outcome.session_work_summary
    assert outcome.telemetry_payload()["session_work_summary"].startswith(
        "Built authentication flow"
    )
    assert any(
        message.role == "system"
        and "structured session_work_summary signal" in message.content
        for message in runtime.calls[0]["messages"]
    )


def test_engine_delegation_result_summary_is_recorded_for_child_turn() -> None:
    client = _FakeClient(
        response=LLMResponse(
            ok=True,
            provider="fake",
            model="fake-model",
            output_text=(
                "Validated the payment retry path."
                "\n<delegation_result_summary>"
                '{"summary": "Validated retry behavior and produced a short report.", '
                '"artifacts_produced": ["artifact://retry-report"], "status": "complete"}'
                "</delegation_result_summary>"
            ),
            finish_reason="stop",
        )
    )
    state = _state(tool_calls=2)
    state.module_state = {
        "delegation": {
            "enabled": True,
            "parent_context": {
                "summary": "Parent narrowed the issue to payment retries.",
                "artifacts": ["artifact://payment-log"],
                "intent_id": "intent-payments",
            },
        }
    }
    loop_ctx = _LoopContext(state=state)

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=DefaultAdaptiveToolLoopLLMRuntime(client),
        model="fake-model",
        initial_messages=[Message(role="user", content="validate delegated task")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.final_text == "Validated the payment retry path."
    assert outcome.delegation_result_summary == {
        "summary": "Validated retry behavior and produced a short report.",
        "artifacts_produced": ["artifact://retry-report"],
        "status": "complete",
    }
    assert (
        outcome.telemetry_payload()["delegation_result_summary"]["summary"]
        == "Validated retry behavior and produced a short report."
    )
    assert any(
        message.role == "system"
        and "structured delegation_result_summary signal" in message.content
        for message in client.calls[0]["messages"]
    )
    assert any(
        message.role == "system" and "[PARENT CONTEXT]" in message.content
        for message in client.calls[0]["messages"]
    )


def test_engine_meta_rule_preference_trailer_is_recorded_without_mutating_profile() -> (
    None
):
    profile = _profile(allowed_tools=frozenset({"file.read"}), max_iterations=4)
    client = _FakeClient(
        response=LLMResponse(
            ok=True,
            provider="fake",
            model="fake-model",
            output_text=(
                "I'll use three retries for broad search tasks next time.\n"
                "<meta_rule_preference>"
                '{"rule": "search_retry_count", "preferred_value": 3, '
                '"reasoning": "Broad web queries often need extra retry headroom."}'
                "</meta_rule_preference>"
            ),
            finish_reason="stop",
        )
    )
    runtime = DefaultAdaptiveToolLoopLLMRuntime(client)
    loop_ctx = _LoopContext(state=_state(tool_calls=2))

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=profile,
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="finish the task")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert (
        outcome.final_text == "I'll use three retries for broad search tasks next time."
    )
    assert outcome.meta_rule_preference == {
        "rule": "search_retry_count",
        "preferred_value": 3,
        "reasoning": "Broad web queries often need extra retry headroom.",
    }
    assert outcome.telemetry_payload()["meta_rule_preference"]["rule"] == (
        "search_retry_count"
    )
    assert profile.max_iterations == 4
    assert any(
        message.role == "system"
        and "structured meta_rule_preference signal" in message.content
        for message in client.calls[0]["messages"]
    )


def test_engine_memory_consolidation_decisions_are_recorded_on_final_text() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Consolidation completed.",
                memory_consolidation=MemoryConsolidationResult(
                    decisions=[
                        {
                            "candidate_id": "cand-1",
                            "action": "promote",
                            "reasoning": "High value lesson.",
                        },
                        {
                            "candidate_id": "cand-2",
                            "action": "defer",
                            "reasoning": "Need more evidence.",
                        },
                    ]
                ).model_dump(mode="json"),
            )
        ]
    )
    ctx = _LoopContext(state=_state())
    ctx.state.module_state = {
        "memory_consolidation": {
            "enabled": True,
            "candidates": [
                {
                    "candidate_id": "cand-1",
                    "record_type": "fact",
                    "content_preview": "Remember preferred deploy region is us-west-2.",
                }
            ],
        }
    }

    outcome = run_adaptive_tool_loop(
        ctx,
        profile=_profile(allowed_tools=frozenset()),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="consolidate memory")],
        tool_specs=[],
    )

    assert outcome.final_text == "Consolidation completed."
    assert outcome.memory_consolidation_decisions == [
        {
            "candidate_id": "cand-1",
            "action": "promote",
            "reasoning": "High value lesson.",
        },
        {
            "candidate_id": "cand-2",
            "action": "defer",
            "reasoning": "Need more evidence.",
        },
    ]
    assert any(
        message.role == "system"
        and "structured memory_consolidation signal" in message.content
        for message in runtime.calls[0]["messages"]
    )


def test_engine_discards_stale_snapshot_from_previous_trace_and_executes_tool() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="weather",
                        arguments={"location": "San Francisco"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="sunny",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4, trace_id="trace-weather-new"),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="72F and sunny",
                ),
            )
        ],
    )
    loop_ctx.state.module_state = {
        "adaptive_loop": LoopSnapshot(
            turn_scope_id="trace-weather-old",
            iteration_index=7,
            message_transcript=[],
            tool_call_history=[],
            budgets_consumed={"llm_calls": 4, "tool_calls": 4},
            profile_name="shared_adaptive_test",
            model="fake-model",
            allowed_tools=frozenset({"weather"}),
        ).to_dict()
    }

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"weather"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="weather in San Francisco")],
        tool_specs=_tool_specs("weather"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert [command.tool_name for command in loop_ctx.commands] == ["weather"]
    assert outcome.state.scratchpad["resumed_from_snapshot"] is False
    snapshot = (loop_ctx.state.module_state or {}).get("adaptive_loop") or {}
    assert snapshot["turn_scope_id"] == "trace-weather-new"


def test_engine_forces_answer_only_closure_after_requested_direct_tool_batch() -> None:
    seed_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[
            ToolCall(
                id="call-1",
                name="file.list_dir",
                arguments={"path": "/repo/openminion"},
            )
        ],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="file.list_dir",
                        arguments={"path": "/repo/openminion/src"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="The top-level entries are src, tests, and pyproject.toml.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="listed root",
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.list_dir"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(
                role="user", content='tool file.list_dir {"path":"/repo/openminion"}'
            )
        ],
        initial_state=AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content='tool file.list_dir {"path":"/repo/openminion"}',
                )
            ],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("file.list_dir",),
                requested_batch_signature=semantic_batch_signature(
                    list(seed_response.tool_calls)
                ),
            ),
        ),
        tool_specs=_tool_specs("file.list_dir"),
        seed_response=seed_response,
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text.startswith("The top-level entries are")
    assert [command.args["path"] for command in loop_ctx.commands] == [
        "/repo/openminion"
    ]
    assert runtime.calls[1]["tool_choice"] == "none"
    assert any(
        "already completed successfully" in str(getattr(message, "content", "") or "")
        for message in runtime.calls[1]["messages"]
        if getattr(message, "role", "") == "system"
    )


def test_engine_forces_answer_only_closure_for_requested_direct_multi_tool_batch() -> (
    None
):
    seed_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[
            ToolCall(id="call-1", name="time", arguments={}),
            ToolCall(
                id="call-2",
                name="file.read",
                arguments={"path": "/repo/openminion/README.md"},
            ),
        ],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-3",
                        name="file.list_dir",
                        arguments={"path": "/repo/openminion/src"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="The time is 10:00 and README.md starts with project docs.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=6),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="10:00",
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="README contents",
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"time", "file.read", "file.list_dir"})
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(role="user", content="tool time {} + file.read README")
        ],
        initial_state=AdaptiveToolLoopState(
            messages=[Message(role="user", content="tool time {} + file.read README")],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("time", "file.read"),
                requested_batch_signature=semantic_batch_signature(
                    list(seed_response.tool_calls)
                ),
            ),
        ),
        tool_specs=_tool_specs("time", "file.read", "file.list_dir"),
        seed_response=seed_response,
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert "README.md" in outcome.final_text
    assert [command.tool_name for command in loop_ctx.commands] == [
        "time",
        "file.read",
    ]
    assert runtime.calls[1]["tool_choice"] == "none"


def test_engine_marks_direct_tool_request_satisfied_from_executed_command_args() -> (
    None
):
    seed_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[
            ToolCall(
                id="call-1",
                name="weather",
                arguments={"location": "San Francisco"},
            )
        ],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="San Francisco is 51F and sunny.",
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="weather",
                    args={"location": "san francisco"},
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="51F and sunny",
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"weather"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(role="user", content='tool weather {"location":"san francisco"}')
        ],
        initial_state=AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content='tool weather {"location":"san francisco"}',
                )
            ],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("weather",),
                requested_batch_signature=semantic_batch_signature(
                    [
                        ToolCall(
                            id="requested",
                            name="weather",
                            arguments={"location": "san francisco"},
                        )
                    ]
                ),
            ),
        ),
        tool_specs=_tool_specs("weather"),
        seed_response=seed_response,
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert "San Francisco" in outcome.final_text
    assert all(
        call["tool_choice"] != "none"
        or any(
            "already completed successfully"
            in str(getattr(message, "content", "") or "")
            for message in call["messages"]
            if getattr(message, "role", "") == "system"
        )
        for call in runtime.calls
    )


def test_engine_does_not_force_direct_tool_closure_before_requested_batch_succeeds() -> (
    None
):
    seed_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[
            ToolCall(
                id="call-1",
                name="file.list_dir",
                arguments={"path": "/repo/openminion"},
            )
        ],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(responses=[])
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="failed",
                    summary="permission denied",
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.list_dir"}),
            allow_llm_recovery_after_tool_failure=False,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(
                role="user", content='tool file.list_dir {"path":"/repo/openminion"}'
            )
        ],
        initial_state=AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content='tool file.list_dir {"path":"/repo/openminion"}',
                )
            ],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("file.list_dir",),
                requested_batch_signature=semantic_batch_signature(
                    list(seed_response.tool_calls)
                ),
            ),
        ),
        tool_specs=_tool_specs("file.list_dir"),
        seed_response=seed_response,
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY
    assert runtime.calls == []


def test_direct_tool_turn_failed_tool_result_skips_llm_recovery() -> None:
    runtime = _FakeRuntime(responses=[])
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="failed",
                    summary="path does not exist: /repo/missing.txt",
                    error=ActionError(
                        code="NOT_FOUND",
                        message="path does not exist: /repo/missing.txt",
                    ),
                ),
            )
        ],
    )
    requested_call = ToolCall(
        id="call-1",
        name="file.read",
        arguments={"path": "/repo/missing.txt"},
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read"}),
            allow_llm_recovery_after_tool_failure=True,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="Read file /repo/missing.txt")],
        initial_state=AdaptiveToolLoopState(
            messages=[Message(role="user", content="Read file /repo/missing.txt")],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("file.read",),
                requested_batch_signature=semantic_batch_signature([requested_call]),
                requested_calls=(requested_call,),
            ),
        ),
        tool_specs=_tool_specs("file.read"),
        seed_response=LLMResponse(
            ok=True,
            provider="fake",
            model="fake-model",
            output_text="",
            tool_calls=[requested_call],
            finish_reason="tool_calls",
        ),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY
    assert outcome.action_result is not None
    assert outcome.action_result.error is not None
    assert outcome.action_result.error.code == "NOT_FOUND"
    assert runtime.calls == []


def test_engine_clamps_overexpanded_initial_direct_tool_batch_to_requested_call() -> (
    None
):
    seed_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[
            ToolCall(
                id="call-1",
                name="file.list_dir",
                arguments={"path": "/repo/openminion"},
            ),
            ToolCall(
                id="call-2",
                name="file.read",
                arguments={"path": "/repo/openminion/README.md"},
            ),
        ],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-3",
                        name="file.read",
                        arguments={"path": "/repo/openminion/README.md"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="The repository root contains src, tests, and docs.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=6),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="listed root",
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.list_dir", "file.read"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(
                role="user", content='tool file.list_dir {"path":"/repo/openminion"}'
            )
        ],
        initial_state=AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content='tool file.list_dir {"path":"/repo/openminion"}',
                )
            ],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("file.list_dir",),
                requested_batch_signature=semantic_batch_signature(
                    [
                        ToolCall(
                            id="requested",
                            name="file.list_dir",
                            arguments={"path": "/repo/openminion"},
                        )
                    ]
                ),
            ),
        ),
        tool_specs=_tool_specs("file.list_dir", "file.read"),
        seed_response=seed_response,
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert [command.tool_name for command in loop_ctx.commands] == ["file.list_dir"]
    assert runtime.calls[1]["tool_choice"] == "none"


def test_engine_clamps_single_direct_tool_call_to_explicit_requested_args() -> None:
    requested_call = ToolCall(
        id="requested",
        name="weather",
        arguments={"location": "san francisco"},
    )
    seed_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[
            ToolCall(
                id="call-1",
                name="weather",
                arguments={"location": "San Francisco"},
            )
        ],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="San Francisco is 55F and clear.",
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="San Francisco, United States: 10.2°C, clear.",
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"weather"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(
                role="user",
                content='tool weather {"location":"san francisco"}',
            )
        ],
        initial_state=AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content='tool weather {"location":"san francisco"}',
                )
            ],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("weather",),
                requested_batch_signature=semantic_batch_signature([requested_call]),
                requested_calls=(requested_call,),
            ),
        ),
        tool_specs=_tool_specs("weather"),
        seed_response=seed_response,
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert [command.tool_name for command in loop_ctx.commands] == ["weather"]
    assert loop_ctx.commands[0].args == {"location": "san francisco"}
    assert outcome.state.direct_tool_requested_batch_satisfied is True


def test_engine_preserves_direct_tool_requested_inputs_when_clamping() -> None:
    requested_call = SimpleNamespace(
        id="requested",
        name="file.write",
        arguments={"path": "README.md", "content": "ok"},
        inputs={
            "confirmation_source": "policy_replay",
            "confirmation_grant_id": "grant-123",
        },
    )
    seed_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[
            ToolCall(
                id="call-1",
                name="file.write",
                arguments={"path": "README.md", "content": "different"},
            )
        ],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Wrote README.md.",
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="wrote README.md",
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.write"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="write README.md")],
        initial_state=AdaptiveToolLoopState(
            messages=[Message(role="user", content="write README.md")],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("file.write",),
                requested_batch_signature=semantic_batch_signature([requested_call]),
                requested_calls=(requested_call,),
            ),
        ),
        tool_specs=_tool_specs("file.write"),
        seed_response=seed_response,
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert [command.tool_name for command in loop_ctx.commands] == ["file.write"]
    assert loop_ctx.commands[0].args == {"path": "README.md", "content": "ok"}
    assert loop_ctx.commands[0].inputs == {
        "confirmation_source": "policy_replay",
        "confirmation_grant_id": "grant-123",
    }


def test_engine_accepts_zero_arg_direct_tool_execution_without_runtime_arg_echo() -> (
    None
):
    requested_call = ToolCall(
        id="requested",
        name="location",
        arguments={},
    )
    seed_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[
            ToolCall(
                id="call-1",
                name="location",
                arguments={},
            )
        ],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="You appear to be in Seattle, Washington.",
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="Location (identity.default): Seattle, Washington, United States",
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"location"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(
                role="user",
                content="tool location {}",
            )
        ],
        initial_state=AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content="tool location {}",
                )
            ],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("location",),
                requested_batch_signature=semantic_batch_signature([requested_call]),
                requested_calls=(requested_call,),
            ),
        ),
        tool_specs=_tool_specs("location"),
        seed_response=seed_response,
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "You appear to be in Seattle, Washington."
    assert [command.tool_name for command in loop_ctx.commands] == ["location"]
    assert loop_ctx.commands[0].args == {}
    assert outcome.state.direct_tool_requested_batch_satisfied is True


def test_engine_preserves_homogeneous_file_write_batch_after_single_requested_call() -> (
    None
):
    requested_call = ToolCall(
        id="call-1",
        name="file.write",
        arguments={
            "path": "/tmp/workspace/tests/__init__.py",
            "content": '"""Tests."""',
        },
    )
    expanded_batch = [
        ToolCall(
            id="call-2",
            name="file.write",
            arguments={
                "path": "/tmp/workspace/pyproject.toml",
                "content": "[project]\nname='task-summary'\n",
            },
        ),
        ToolCall(
            id="call-3",
            name="file.write",
            arguments={
                "path": "/tmp/workspace/tests/__init__.py",
                "content": '"""Tests."""',
            },
        ),
    ]
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=expanded_batch,
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Created the files.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="file.write",
                    args=dict(expanded_batch[0].arguments),
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="wrote pyproject",
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="file.write",
                    args=dict(expanded_batch[1].arguments),
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="wrote tests init",
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.write"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="write the project files")],
        initial_state=AdaptiveToolLoopState(
            messages=[Message(role="user", content="write the project files")],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("file.write",),
                requested_calls=(requested_call,),
                requested_batch_signature=semantic_batch_signature([requested_call]),
            ),
        ),
        tool_specs=_tool_specs("file.write"),
        seed_response=None,
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "Created the files."
    assert [command.args["path"] for command in loop_ctx.commands] == [
        "/tmp/workspace/pyproject.toml",
        "/tmp/workspace/tests/__init__.py",
    ]
    assert outcome.state.direct_tool_requested_batch_satisfied is True
    assert outcome.state.scratchpad["direct_tool_requested_batch_expanded"] is True


def test_engine_accepts_single_requested_direct_tool_with_runtime_default_args() -> (
    None
):
    requested_call = ToolCall(
        id="requested-1",
        name="file.list_dir",
        arguments={"path": "/repo"},
    )
    seed_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[
            ToolCall(
                id="call-1",
                name="file.list_dir",
                arguments={"path": "/repo"},
            )
        ],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="/repo contains src, tests, and docs.",
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="file.list_dir",
                    args={"path": "/repo", "recursive": False},
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="listed repo",
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.list_dir"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(role="user", content='tool file.list_dir {"path":"/repo"}')
        ],
        initial_state=AdaptiveToolLoopState(
            messages=[
                Message(role="user", content='tool file.list_dir {"path":"/repo"}')
            ],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("file.list_dir",),
                requested_batch_signature=semantic_batch_signature([requested_call]),
                requested_calls=(requested_call,),
            ),
        ),
        tool_specs=_tool_specs("file.list_dir"),
        seed_response=seed_response,
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "/repo contains src, tests, and docs."
    assert outcome.state.direct_tool_requested_batch_satisfied is True


def test_engine_retries_once_when_explicit_direct_tool_turn_returns_zero_call_success_text() -> (
    None
):
    seed_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text='Tool is returning a result: {"success": true}',
        tool_calls=[],
        finish_reason="stop",
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Policy confirmation required.",
                tool_calls=[],
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(tool_calls=4))

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.write"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(
                role="user",
                content='tool file.write {"path":"/tmp/demo.txt","content":"demo"}',
            )
        ],
        initial_state=AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content='tool file.write {"path":"/tmp/demo.txt","content":"demo"}',
                )
            ],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("file.write",),
                requested_batch_signature=semantic_batch_signature(
                    [
                        ToolCall(
                            id="requested",
                            name="file.write",
                            arguments={"path": "/tmp/demo.txt", "content": "demo"},
                        )
                    ]
                ),
            ),
        ),
        tool_specs=_tool_specs("file.write"),
        seed_response=seed_response,
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED
    assert outcome.error_message == (
        "The requested tool was not executed, so I cannot truthfully claim it succeeded."
    )
    assert len(runtime.calls) == 1
    assert any(
        "explicit tool command for file.write" in str(message.content).lower()
        for message in runtime.calls[0]["messages"]
        if getattr(message, "role", "") == "system"
    )


def test_engine_tracks_multi_step_direct_tool_sequence_across_iterations() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="web.search",
                        arguments={"query": "pipx official documentation pypa"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="web.fetch",
                        arguments={
                            "url": "https://docs.astral.sh/uv/getting-started/installation/"
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-3",
                        name="web.fetch",
                        arguments={"url": "https://pipx.pypa.io/"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Used the requested tool sequence.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=6),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="web.search",
                    args={"query": "pipx official documentation pypa"},
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="search ok",
                    outputs={"query": "pipx official documentation pypa"},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="web.fetch",
                    args={
                        "url": "https://docs.astral.sh/uv/getting-started/installation/"
                    },
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="uv docs ok",
                    outputs={
                        "url": "https://docs.astral.sh/uv/getting-started/installation/"
                    },
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="web.fetch",
                    args={"url": "https://pipx.pypa.io/"},
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="pipx docs ok",
                    outputs={"url": "https://pipx.pypa.io/"},
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"web.search", "web.fetch"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(
                role="user",
                content="Use `web.search`, then `web.fetch`, then `web.fetch`.",
            )
        ],
        initial_state=AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content="Use `web.search`, then `web.fetch`, then `web.fetch`.",
                )
            ],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("web.search", "web.fetch", "web.fetch"),
                requested_batch_signature="",
                requested_calls=(),
                match_by_name_only=True,
            ),
        ),
        tool_specs=_tool_specs("web.search", "web.fetch"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "done"
    assert outcome.state.direct_tool_requested_batch_satisfied is True
    assert [command.tool_name for command in loop_ctx.commands] == [
        "web.search",
        "web.fetch",
        "web.fetch",
    ]
    assert len(runtime.calls) == 4


def test_engine_fails_cleanly_when_answer_only_closure_still_returns_tool_calls() -> (
    None
):
    seed_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[
            ToolCall(
                id="call-1",
                name="file.list_dir",
                arguments={"path": "/repo/openminion"},
            )
        ],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="file.list_dir",
                        arguments={"path": "/repo/openminion/src"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-3",
                        name="file.list_dir",
                        arguments={"path": "/repo/openminion/tests"},
                    )
                ],
                finish_reason="tool_calls",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="listed root",
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.list_dir"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(
                role="user", content='tool file.list_dir {"path":"/repo/openminion"}'
            )
        ],
        initial_state=AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content='tool file.list_dir {"path":"/repo/openminion"}',
                )
            ],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("file.list_dir",),
                requested_batch_signature=semantic_batch_signature(
                    list(seed_response.tool_calls)
                ),
            ),
        ),
        tool_specs=_tool_specs("file.list_dir"),
        seed_response=seed_response,
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED
    assert [command.args["path"] for command in loop_ctx.commands] == [
        "/repo/openminion"
    ]


def test_engine_accepts_name_only_direct_tool_turn_without_typed_finalization() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="file.read",
                        arguments={"path": "/repo/README.md"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Workspace for OpenMinion-aligned local-first agent orchestration modules.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="file.read",
                    args={"path": "/repo/README.md"},
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="Read file",
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read"}),
            profile_name="general_adaptive_v1",
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(
                role="user",
                content="use file.read on /repo/README.md and reply with the first sentence only",
            )
        ],
        initial_state=AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content="use file.read on /repo/README.md and reply with the first sentence only",
                )
            ],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("file.read",),
                requested_batch_signature="",
                match_by_name_only=True,
            ),
        ),
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == (
        "Workspace for OpenMinion-aligned local-first agent orchestration modules."
    )
    assert outcome.error_message is None


def test_engine_accepts_name_only_direct_tool_turn_for_provider_specific_family_tool() -> (
    None
):
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="weather",
                        arguments={"location": "San Francisco"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="San Francisco is 17.1°C and clear.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="weather.openmeteo.current",
                    args={"location": "San Francisco"},
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="San Francisco, United States: 17.1°C, clear.",
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"weather"}),
            profile_name="general_adaptive_v1",
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(
                role="user",
                content=(
                    "Please use the weather tool to get the current weather in "
                    "San Francisco, then answer with one concise sentence."
                ),
            )
        ],
        initial_state=AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content=(
                        "Please use the weather tool to get the current weather in "
                        "San Francisco, then answer with one concise sentence."
                    ),
                )
            ],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("weather",),
                requested_batch_signature="",
                match_by_name_only=True,
            ),
        ),
        tool_specs=_tool_specs("weather"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "San Francisco is 17.1°C and clear."
    assert outcome.state.direct_tool_requested_batch_satisfied is True


def test_engine_retries_name_only_direct_tool_batch_after_mismatched_tools() -> None:
    seed_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[
            ToolCall(
                id="seed-1",
                name="file.list_dir",
                arguments={"path": "/tmp/workspace"},
            )
        ],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="web.fetch",
                        arguments={
                            "url": "https://packaging.python.org/en/latest/guides/writing-pyproject-toml/"
                        },
                    ),
                    ToolCall(
                        id="call-2",
                        name="file.list_dir",
                        arguments={"path": "/tmp/workspace"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-3",
                        name="file.list_dir",
                        arguments={"path": "/tmp/workspace"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-4",
                        name="file.write",
                        arguments={"path": "pyproject.toml", "content": "[project]"},
                    ),
                    ToolCall(
                        id="call-5",
                        name="file.write",
                        arguments={"path": "README.md", "content": "# demo"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="SOURCES\nDATE: 2026-06-21\nCHANGES\nupdated files\nTESTS\npassed",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=8),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="web.fetch",
                    args={
                        "url": "https://packaging.python.org/en/latest/guides/writing-pyproject-toml/"
                    },
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="fetched guide",
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="file.write",
                    args={"path": "pyproject.toml", "content": "[project]"},
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="wrote pyproject",
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="file.write",
                    args={"path": "README.md", "content": "# demo"},
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="wrote readme",
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"web.fetch", "file.write", "file.list_dir"})
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="update packaging files")],
        initial_state=AdaptiveToolLoopState(
            messages=[Message(role="user", content="update packaging files")],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("web.fetch", "file.write", "file.write"),
                requested_batch_signature="",
                match_by_name_only=True,
            ),
        ),
        tool_specs=_tool_specs("web.fetch", "file.write", "file.list_dir"),
        seed_response=seed_response,
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.state.direct_tool_requested_batch_satisfied is True
    assert [command.tool_name for command in loop_ctx.commands] == [
        "web.fetch",
        "file.write",
        "file.write",
    ]
    assert len(runtime.calls) == 4


def test_engine_limits_visible_tools_for_direct_tool_turn() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="time",
                        arguments={"timezone": "UTC"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="The current time is 10:00 UTC.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="time",
                    args={"timezone": "UTC"},
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="10:00 UTC",
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"time", "exec.run", "file.read"}),
            profile_name="general_adaptive_v1",
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content='tool time {"timezone":"UTC"}')],
        initial_state=AdaptiveToolLoopState(
            messages=[Message(role="user", content='tool time {"timezone":"UTC"}')],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("time",),
                requested_batch_signature="",
                match_by_name_only=True,
            ),
        ),
        tool_specs=_tool_specs("time", "exec.run", "file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert [spec.name for spec in runtime.calls[0]["tools"]] == ["time"]
    assert all(spec.name != "exec.run" for spec in runtime.calls[0]["tools"])


def test_engine_retries_direct_tool_finalization_without_exposing_more_tools() -> None:
    seed_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[
            ToolCall(
                id="call-1",
                name="weather",
                arguments={"location": "san francisco"},
            )
        ],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="The current weather is foggy and 11.4C.",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="The current weather is foggy and 11.4C.",
                finalization_status={"status": "final_answer"},
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(
                    tool_name="weather",
                    args={"location": "san francisco"},
                ),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="weather ok",
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"weather"}),
            profile_name="general_adaptive_v1",
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(
                role="user",
                content='tool weather {"location":"san francisco"}',
            )
        ],
        initial_state=AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content='tool weather {"location":"san francisco"}',
                )
            ],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("weather",),
                requested_batch_signature=semantic_batch_signature(
                    list(seed_response.tool_calls)
                ),
            ),
        ),
        tool_specs=_tool_specs("weather"),
        seed_response=seed_response,
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "The current weather is foggy and 11.4C."
    assert len(runtime.calls) == 1
    assert runtime.calls[0]["tools"] == []
    assert runtime.calls[0]["tool_choice"] == "none"


def test_engine_requires_typed_finalization_after_substantive_tool_work() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="web.search",
                        arguments={"query": "python release notes"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="web.fetch",
                        arguments={"url": "https://example.com/release-notes"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-3",
                        name="web.fetch",
                        arguments={"url": "https://example.com/second-source"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Good recovery so far. Let me gather the last source.",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Final comparison table delivered.",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Two substantive tool-backed sources were synthesized.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=6),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="search ok",
                    outputs={"content": "release note snippets"},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="fetch ok",
                    outputs={"content": "full release notes"},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="second fetch ok",
                    outputs={"content": "second primary source"},
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            profile_name="general_adaptive_v1",
            allowed_tools=frozenset({"web.search", "web.fetch"}),
            max_iterations=6,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="research and compare")],
        tool_specs=_tool_specs("web.search", "web.fetch"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "Final comparison table delivered."
    assert outcome.finalization_status == {
        "status": "final_answer",
        "reasoning": "Two substantive tool-backed sources were synthesized.",
        "remaining_work": "",
        "blocking_reason": "",
    }
    retry_system_messages = [
        str(message.content)
        for message in runtime.calls[3]["messages"]
        if getattr(message, "role", "") == "system"
    ]
    assert any("finalization_status" in message for message in retry_system_messages)


def test_engine_requires_typed_finalization_after_substantive_tool_work_for_coding_profile() -> (
    None
):
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="web.search",
                        arguments={"query": "python release notes"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="web.fetch",
                        arguments={"url": "https://example.com/release-notes"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-3",
                        name="web.fetch",
                        arguments={"url": "https://example.com/second-source"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Good recovery so far. Let me gather the last source.",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Final comparison table delivered.",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Two substantive tool-backed sources were synthesized.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=6),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="search ok",
                    outputs={"content": "release note snippets"},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="fetch ok",
                    outputs={"content": "full release notes"},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="second fetch ok",
                    outputs={"content": "second primary source"},
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            profile_name="coding_v1",
            allowed_tools=frozenset({"web.search", "web.fetch"}),
            max_iterations=6,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="research and compare")],
        tool_specs=_tool_specs("web.search", "web.fetch"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "Final comparison table delivered."
    assert outcome.finalization_status == {
        "status": "final_answer",
        "reasoning": "Two substantive tool-backed sources were synthesized.",
        "remaining_work": "",
        "blocking_reason": "",
    }
    retry_system_messages = [
        str(message.content)
        for message in runtime.calls[3]["messages"]
        if getattr(message, "role", "") == "system"
    ]
    assert any("finalization_status" in message for message in retry_system_messages)


def test_engine_fails_closed_when_typed_finalization_remains_missing() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="web.search",
                        arguments={"query": "python release notes"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="web.fetch",
                        arguments={"url": "https://example.com/release-notes"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-3",
                        name="web.fetch",
                        arguments={"url": "https://example.com/second-source"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Still working on it.",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Still working on it.",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Still working on it.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=6, llm_calls_max=6),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="search ok",
                    outputs={"content": "release note snippets"},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="fetch ok",
                    outputs={"content": "full release notes"},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="second fetch ok",
                    outputs={"content": "second primary source"},
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            profile_name="general_adaptive_v1",
            allowed_tools=frozenset({"web.search", "web.fetch"}),
            max_iterations=7,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="research and compare")],
        tool_specs=_tool_specs("web.search", "web.fetch"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING
    assert outcome.error_message == (
        "General act work ended without the required typed finalization_status contract."
    )


def test_engine_retries_raw_tool_result_json_as_final_answer() -> None:
    raw_tool_result_answer = json.dumps(
        {
            "status": "success",
            "summary": json.dumps(
                {
                    "bytes_written": 5,
                    "mode": "write",
                    "ok": True,
                    "path": "sample.txt",
                    "source": "file_module",
                }
            ),
            "outputs": {
                "ok": True,
                "path": "sample.txt",
                "bytes_written": 5,
                "mode": "write",
                "source": "file_module",
            },
        }
    )
    second_raw_tool_result_answer = json.dumps(
        {
            "ok": True,
            "path": "sample.txt",
            "returned_length": 5,
            "content": "hello",
        }
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="file.write",
                        arguments={"path": "sample.txt", "content": "hello"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text=raw_tool_result_answer,
                assistant_messages=[
                    Message(role="assistant", content=raw_tool_result_answer)
                ],
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text=second_raw_tool_result_answer,
                assistant_messages=[
                    Message(role="assistant", content=second_raw_tool_result_answer)
                ],
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Created sample.txt with the requested content.",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "The file write succeeded and the answer is user-facing.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4, llm_calls_max=6),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary=json.dumps(
                        {
                            "bytes_written": 5,
                            "mode": "write",
                            "ok": True,
                            "path": "sample.txt",
                            "source": "file_module",
                        }
                    ),
                    outputs={"ok": True, "path": "sample.txt", "bytes_written": 5},
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            profile_name="general_adaptive_v1",
            allowed_tools=frozenset({"file.write"}),
            max_iterations=4,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="write sample.txt")],
        tool_specs=_tool_specs("file.write"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "Created sample.txt with the requested content."
    retry_system_messages = [
        str(message.content)
        for message in runtime.calls[3]["messages"]
        if message.role == "system"
    ]
    assert (
        sum(
            "raw tool-result JSON envelope" in message
            for message in retry_system_messages
        )
        == 2
    )
    retry_assistant_messages = [
        str(message.content)
        for message in runtime.calls[3]["messages"]
        if message.role == "assistant"
    ]
    assert raw_tool_result_answer not in retry_assistant_messages
    assert second_raw_tool_result_answer not in retry_assistant_messages


def test_engine_salvages_typed_finalization_with_status_only_follow_up() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="web.search",
                        arguments={"query": "python release notes"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="web.fetch",
                        arguments={"url": "https://example.com/release-notes"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-3",
                        name="web.fetch",
                        arguments={"url": "https://example.com/second-source"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Final comparison table delivered.",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Final comparison table delivered.",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Two substantive tool-backed sources were synthesized.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=6, llm_calls_max=7),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="search ok",
                    outputs={"content": "release note snippets"},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="fetch ok",
                    outputs={"content": "full release notes"},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="second fetch ok",
                    outputs={"content": "second primary source"},
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            profile_name="general_adaptive_v1",
            allowed_tools=frozenset({"web.search", "web.fetch"}),
            max_iterations=7,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="research and compare")],
        tool_specs=_tool_specs("web.search", "web.fetch"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "Final comparison table delivered."
    assert outcome.finalization_status == {
        "status": "final_answer",
        "reasoning": "Two substantive tool-backed sources were synthesized.",
        "remaining_work": "",
        "blocking_reason": "",
    }
    assert runtime.calls[-1]["tool_choice"] == "none"
    assert runtime.calls[-1]["tools"] == []
    salvage_messages = [
        str(message.content)
        for message in runtime.calls[-1]["messages"]
        if getattr(message, "role", "") == "system"
    ]
    assert any(
        "Return only the structured finalization_status signal" in message
        for message in salvage_messages
    )


def test_engine_accepts_reason_alias_in_finalization_trailer() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="web.search",
                        arguments={"query": "uv vs pipx"},
                    ),
                    ToolCall(
                        id="call-2",
                        name="web.fetch",
                        arguments={
                            "url": "https://docs.astral.sh/uv/getting-started/installation/"
                        },
                    ),
                    ToolCall(
                        id="call-3",
                        name="web.fetch",
                        arguments={"url": "https://pipx.pypa.io/"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="PLAN\n- done\n\nTABLE\n- compared\n\nUNCERTAINTIES\n- none",
                finalization_status={
                    "status": "final_answer",
                    "reason": "alias accepted",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=6),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="search ok",
                    outputs={"content": "release note snippets"},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="fetch ok",
                    outputs={"content": "full release notes"},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="second fetch ok",
                    outputs={"content": "second primary source"},
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            profile_name="general_adaptive_v1",
            allowed_tools=frozenset({"web.search", "web.fetch"}),
            max_iterations=4,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="research and compare")],
        tool_specs=_tool_specs("web.search", "web.fetch"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert (
        outcome.final_text
        == "PLAN\n- done\n\nTABLE\n- compared\n\nUNCERTAINTIES\n- none"
    )
    assert outcome.finalization_status == {
        "status": "final_answer",
        "reasoning": "alias accepted",
        "remaining_work": "",
        "blocking_reason": "",
    }


def test_engine_does_not_require_typed_finalization_for_no_tool_general_act_answer() -> (
    None
):
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text=(
                    "# PLAN\n- Done\n\n# TABLE\n| Item | Status |\n| --- | --- |\n| Work | Complete |"
                ),
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(tool_calls=4))

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            profile_name="general_adaptive_v1",
            allowed_tools=frozenset({"file.read"}),
            max_iterations=3,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(
                role="user",
                content="complex multi-step answer with plan and table",
            )
        ],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert (
        outcome.final_text
        == "# PLAN\n- Done\n\n# TABLE\n| Item | Status |\n| --- | --- |\n| Work | Complete |"
    )
    assert outcome.finalization_status is None
    assert len(runtime.calls) == 1


def test_engine_requires_typed_finalization_after_failed_tool_work() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="web.fetch",
                        arguments={"url": "https://example.com/blocked"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="I hit a blocker and need to explain it clearly.",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                finalization_status={
                    "status": "blocked",
                    "reasoning": "The failed tool result prevented completion.",
                    "blocking_reason": "network denied",
                },
                output_text="I hit a blocker and need to explain it clearly.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4, llm_calls_max=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="failed",
                    summary="fetch denied",
                    outputs={"error": "network denied"},
                    error=ActionError(
                        code="NETWORK_DENIED",
                        message="network denied",
                    ),
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            profile_name="general_adaptive_v1",
            allowed_tools=frozenset({"web.fetch"}),
            max_iterations=4,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="check the blocked source")],
        tool_specs=_tool_specs("web.fetch"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINALIZATION_BLOCKED
    assert outcome.final_text == "I hit a blocker and need to explain it clearly."
    assert outcome.finalization_status == {
        "status": "blocked",
        "reasoning": "The failed tool result prevented completion.",
        "remaining_work": "",
        "blocking_reason": "network denied",
    }
    retry_system_messages = [
        str(message.content)
        for message in runtime.calls[2]["messages"]
        if getattr(message, "role", "") == "system"
    ]
    assert any(
        "route that requires typed finalization" in message
        for message in retry_system_messages
    )


def test_engine_does_not_require_typed_finalization_after_light_tool_work() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1", name="web.search", arguments={"query": "topic a"}
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="web.fetch",
                        arguments={"url": "https://example.com/topic-a"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Here is a concise researched answer from two light lookups.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4, llm_calls_max=4),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="search ok",
                    outputs={"results": ["topic a"]},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="fetch ok",
                    outputs={"content": "topic a details"},
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            profile_name="general_adaptive_v1",
            allowed_tools=frozenset({"web.search", "web.fetch"}),
            max_iterations=4,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(role="user", content="give me a light researched answer")
        ],
        tool_specs=_tool_specs("web.search", "web.fetch"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert (
        outcome.final_text
        == "Here is a concise researched answer from two light lookups."
    )
    assert outcome.finalization_status is None
    assert len(runtime.calls) == 3


def test_engine_does_not_require_finalization_for_non_general_profile_no_tool_answer() -> (
    None
):
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Done without general act route finalization.",
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(tool_calls=4))

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            profile_name="shared_adaptive_test",
            allowed_tools=frozenset({"file.read"}),
            max_iterations=2,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="complex is just a word")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "Done without general act route finalization."


def test_respond_decision_routes_do_not_require_finalization_status() -> None:
    answer = Decision(
        route="respond",
        respond_kind="answer",
        answer="Plain answer.",
    )
    clarify = Decision(
        route="respond",
        respond_kind="clarify",
        question="Which file should I inspect?",
    )

    assert answer.finalization_status is None
    assert clarify.finalization_status is None


def test_engine_allows_one_duplicate_tool_batch_retry_before_stopping() -> None:
    duplicate_runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="file.read", arguments={"path": "a.py"})
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-2", name="file.read", arguments={"path": "a.py"})
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done after retry",
                finish_reason="stop",
            ),
        ]
    )
    duplicate_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="read a"
                ),
            )
        ],
    )

    duplicate_outcome = run_adaptive_tool_loop(
        duplicate_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=duplicate_runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read twice")],
        tool_specs=_tool_specs("file.read"),
    )

    assert duplicate_outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert duplicate_outcome.final_text == "done after retry"
    assert len(duplicate_ctx.commands) == 1
    assert len(duplicate_runtime.calls) == 3
    assert duplicate_runtime.calls[2]["tool_choice"] == "none"
    third_call_messages = duplicate_runtime.calls[2]["messages"]
    assert len(third_call_messages) <= 3
    assert any(
        "Do not call more tools" in str(getattr(message, "content", "") or "")
        for message in third_call_messages
        if getattr(message, "role", "") == "system"
    )
    assert any(
        "Successful tool evidence already gathered"
        in str(getattr(message, "content", "") or "")
        for message in third_call_messages
        if getattr(message, "role", "") == "user"
    )


def test_engine_keeps_retryable_duplicate_batch_on_normal_tool_path() -> None:
    duplicate_runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="file.read", arguments={"path": "a.py"})
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-2", name="file.read", arguments={"path": "a.py"})
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done after retry guidance",
                finish_reason="stop",
            ),
        ]
    )
    duplicate_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="retry",
                    summary="retry with a fresh snapshot",
                ),
            )
        ],
    )

    duplicate_outcome = run_adaptive_tool_loop(
        duplicate_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=duplicate_runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read twice")],
        tool_specs=_tool_specs("file.read"),
    )

    assert duplicate_outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert duplicate_outcome.final_text == "done after retry guidance"
    assert len(duplicate_ctx.commands) == 1
    assert len(duplicate_runtime.calls) == 3
    assert duplicate_runtime.calls[2]["tool_choice"] == "auto"


def test_engine_keeps_pending_duplicate_batch_on_normal_tool_path() -> None:
    duplicate_runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="exec.run", arguments={"cmd": "sleep 1"})
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-2", name="exec.run", arguments={"cmd": "sleep 1"})
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="still pending",
                finish_reason="stop",
            ),
        ]
    )
    duplicate_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="started background job",
                ),
                job=JobHandle(
                    task_id="task-1",
                    command_id="cmd-1",
                    provider="tool",
                    status="pending",
                ),
            )
        ],
    )

    duplicate_outcome = run_adaptive_tool_loop(
        duplicate_ctx,
        profile=AdaptiveToolLoopProfile(
            profile_name="shared_adaptive_test",
            mode_name="act_adaptive",
            allowed_tools=frozenset({"exec.run"}),
            max_iterations=4,
            allow_llm_recovery_after_tool_failure=True,
            tool_choice="auto",
            provider_parallel_tool_capacity=1,
            stop_on_job_pending=False,
        ),
        runtime=duplicate_runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="run sleep")],
        tool_specs=_tool_specs("exec.run"),
    )

    assert duplicate_outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert duplicate_outcome.final_text == "still pending"
    assert len(duplicate_runtime.calls) == 3
    assert duplicate_runtime.calls[2]["tool_choice"] == "auto"


def test_engine_stops_after_duplicate_tool_batch_repeats_again() -> None:
    duplicate_runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="file.read", arguments={"path": "a.py"})
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-2", name="file.read", arguments={"path": "a.py"})
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-3", name="file.read", arguments={"path": "a.py"})
                ],
            ),
        ]
    )
    duplicate_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="read a"
                ),
            )
        ],
    )

    duplicate_outcome = run_adaptive_tool_loop(
        duplicate_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=duplicate_runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read twice")],
        tool_specs=_tool_specs("file.read"),
    )

    assert duplicate_outcome.termination_reason == ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS
    assert len(duplicate_ctx.commands) == 1
    assert len(duplicate_runtime.calls) == 3
    assert duplicate_runtime.calls[2]["tools"] == []
    assert duplicate_runtime.calls[2]["tool_choice"] == "none"
    assert "Answer-only closure returned more tool calls" in str(
        duplicate_outcome.error_message or ""
    )

    distinct_runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="file.read", arguments={"path": "a.py"})
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-2", name="file.read", arguments={"path": "b.py"})
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
    distinct_ctx = _LoopContext(
        state=_state(tool_calls=3),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="read a"
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="read b"
                ),
            ),
        ],
    )

    distinct_outcome = run_adaptive_tool_loop(
        distinct_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=distinct_runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read both")],
        tool_specs=_tool_specs("file.read"),
    )

    assert distinct_outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert [command.args["path"] for command in distinct_ctx.commands] == [
        "a.py",
        "b.py",
    ]


def test_engine_stops_on_needs_user_and_job_pending() -> None:
    needs_user_runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1", name="exec.run", arguments={"cmd": "rm -rf build"}
                    )
                ],
            )
        ]
    )
    needs_user_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="needs_user",
                    summary="Please approve the command.",
                ),
            )
        ],
    )
    needs_user_outcome = run_adaptive_tool_loop(
        needs_user_ctx,
        profile=_profile(allowed_tools=frozenset({"exec.run"})),
        runtime=needs_user_runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="run command")],
        tool_specs=_tool_specs("exec.run"),
    )
    assert needs_user_outcome.termination_reason == ADAPTIVE_TERM_NEEDS_USER
    assert needs_user_outcome.action_result is not None
    assert needs_user_outcome.action_result.summary == "Please approve the command."

    job = JobHandle(
        task_id="job-1",
        command_id=new_uuid(),
        provider="tool",
        status="pending",
        poll_after_ms=1000,
        created_at=iso_now(),
    )
    job_runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1", name="exec.run", arguments={"cmd": "long-job"}
                    )
                ],
            )
        ]
    )
    job_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="job started",
                ),
                job=job,
            )
        ],
    )
    job_outcome = run_adaptive_tool_loop(
        job_ctx,
        profile=_profile(allowed_tools=frozenset({"exec.run"})),
        runtime=job_runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="run long job")],
        tool_specs=_tool_specs("exec.run"),
    )
    assert job_outcome.termination_reason == ADAPTIVE_TERM_JOB_PENDING
    assert job_outcome.job is not None


def test_engine_stops_on_budget_iteration_cap_and_nonrecoverable_tool_failure() -> None:
    budget_runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="file.read", arguments={"path": "a.py"})
                ],
            )
        ]
    )
    budget_ctx = _LoopContext(
        state=_state(tool_calls=1),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="read ok"
                ),
            )
        ],
    )
    budget_outcome = run_adaptive_tool_loop(
        budget_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"}), max_iterations=4),
        runtime=budget_runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read once")],
        tool_specs=_tool_specs("file.read"),
    )
    assert budget_outcome.termination_reason == ADAPTIVE_TERM_BUDGET_EXHAUSTED

    cap_runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="file.read", arguments={"path": "a.py"})
                ],
            )
        ]
    )
    cap_ctx = _LoopContext(
        state=_state(tool_calls=3),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="read ok"
                ),
            )
        ],
    )
    cap_outcome = run_adaptive_tool_loop(
        cap_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"}), max_iterations=1),
        runtime=cap_runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read repeatedly")],
        tool_specs=_tool_specs("file.read"),
    )
    assert cap_outcome.termination_reason == ADAPTIVE_TERM_ITERATION_CAP

    autonomous_runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="file.read", arguments={"path": "a.py"})
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done after extension",
            ),
        ]
    )
    autonomous_session_api = _FakeSessionAPI()
    autonomous_ctx = _LoopContext(
        state=_state(tool_calls=3),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="read ok"
                ),
            )
        ],
        session_api=autonomous_session_api,
    )
    autonomous_outcome = run_adaptive_tool_loop(
        autonomous_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read"}),
            max_iterations=1,
            adaptive_budget_config=AdaptiveBudgetConfig(
                mode="autonomous",
                soft_cap=1,
                extend_by=1,
                max_extensions_per_turn=1,
            ),
        ),
        runtime=autonomous_runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read then answer")],
        tool_specs=_tool_specs("file.read"),
    )
    assert autonomous_outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert autonomous_outcome.final_text == "done after extension"
    assert autonomous_outcome.state.effective_max_iterations == 2
    assert autonomous_outcome.state.extensions_used == 1
    assert [event["event_type"] for event in autonomous_session_api.events] == [
        "budget.allocated",
        "budget.extended",
    ]

    interactive_runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="file.read", arguments={"path": "a.py"})
                ],
            )
        ]
    )
    interactive_session_api = _FakeSessionAPI()
    interactive_state = _state(tool_calls=3)
    interactive_ctx = _LoopContext(
        state=interactive_state,
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="read ok"
                ),
            )
        ],
        session_api=interactive_session_api,
    )
    interactive_outcome = run_adaptive_tool_loop(
        interactive_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read"}),
            max_iterations=1,
            adaptive_budget_config=AdaptiveBudgetConfig(
                mode="interactive",
                soft_cap=1,
                extend_by=1,
                max_extensions_per_turn=1,
            ),
        ),
        runtime=interactive_runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read then ask")],
        tool_specs=_tool_specs("file.read"),
    )
    assert interactive_outcome.termination_reason == ADAPTIVE_TERM_NEEDS_USER
    assert interactive_state.pending_confirmation_command is not None
    assert interactive_state.pending_confirmation_command.kind == "ask_user"
    assert [event["event_type"] for event in interactive_session_api.events] == [
        "budget.allocated",
        "budget.exhausted",
    ]
    assert interactive_session_api.events[-1]["payload"]["reason"] == (
        "awaiting_user_extension_approval"
    )

    failure_runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="exec.run", arguments={"cmd": "pytest"})
                ],
            )
        ]
    )
    failure_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="failed", summary="pytest failed"
                ),
            )
        ],
    )
    failure_outcome = run_adaptive_tool_loop(
        failure_ctx,
        profile=_profile(
            allowed_tools=frozenset({"exec.run"}),
            allow_llm_recovery_after_tool_failure=False,
        ),
        runtime=failure_runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="run tests")],
        tool_specs=_tool_specs("exec.run"),
    )
    assert failure_outcome.termination_reason == ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY


def test_engine_parallelizes_independent_reads_and_preserves_result_order() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-slow",
                        name="file.read",
                        arguments={"path": "/src/slow.py"},
                    ),
                    ToolCall(
                        id="call-fast",
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
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Requested file reads completed and final answer is present.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        delays_by_path={"/src/slow.py": 0.2, "/src/fast.py": 0.01},
        outcomes_by_path={
            "/src/slow.py": CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="slow result",
                ),
            ),
            "/src/fast.py": CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="fast result",
                ),
            ),
        },
    )

    started = time.monotonic()
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read"}),
            provider_parallel_tool_capacity=0,  # unlimited parallelism
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read two files")],
        tool_specs=_tool_specs("file.read"),
    )
    elapsed = time.monotonic() - started

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert elapsed < 0.35
    second_call_messages = runtime.calls[1]["messages"]
    tool_messages = [
        message for message in second_call_messages if message.role == "tool"
    ]
    assert [json.loads(message.content)["summary"] for message in tool_messages] == [
        "slow result",
        "fast result",
    ]
    payload = outcome.telemetry_payload()
    assert payload["loop.parallel_fan_out_count"] == 1
    assert payload["loop.tool_calls_parallel"] == 2
    assert payload["loop.tool_calls_sequential"] == 0


def test_engine_serializes_write_then_read_same_path() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-write",
                        name="file.write",
                        arguments={"path": "/src/alpha.py", "content": "updated"},
                    ),
                    ToolCall(
                        id="call-read",
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
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Requested file reads completed and final answer is present.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        delays_by_path={"/src/alpha.py": 0.1},
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="write ok",
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="read ok",
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read", "file.write"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="write then read")],
        tool_specs=_tool_specs("file.read", "file.write"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    write_call, read_call = loop_ctx.call_windows
    assert write_call[2] <= read_call[1]
    payload = outcome.telemetry_payload()
    assert payload["loop.parallel_fan_out_count"] == 0
    assert payload["loop.tool_calls_parallel"] == 0
    assert payload["loop.tool_calls_sequential"] == 2


def _profile_with_recovery(
    *,
    allowed_tools: frozenset[str],
    max_iterations: int = 6,
) -> AdaptiveToolLoopProfile:
    return AdaptiveToolLoopProfile(
        profile_name="shared_adaptive_test",
        mode_name="act_adaptive",
        allowed_tools=allowed_tools,
        max_iterations=max_iterations,
        allow_llm_recovery_after_tool_failure=True,
    )


def test_anomalous_result_appends_enrichment() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="exec.run",
                        arguments={"cmd": "pytest"},
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
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="failed",
                    summary="pytest failed with exit code 1",
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile_with_recovery(allowed_tools=frozenset({"exec.run"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="run tests")],
        tool_specs=_tool_specs("exec.run"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT

    second_call_messages = runtime.calls[1]["messages"]
    system_messages = [m for m in second_call_messages if m.role == "system"]
    assert any(
        "[system]" in m.content and "exec.run" in m.content for m in system_messages
    )

    scratchpad = outcome.state.scratchpad
    assert scratchpad.get("micro_correction_count") == 1


def test_failed_tool_result_appends_recovery_hint() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="weather",
                        arguments={},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="ask for location",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="failed",
                    summary="Tool execution failed",
                    error={
                        "code": "EXEC_ERROR",
                        "message": (
                            "One of location/city/query/place or latitude+longitude "
                            "is required"
                        ),
                    },
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile_with_recovery(allowed_tools=frozenset({"weather"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="weather in San Francisco")],
        tool_specs=_tool_specs("weather"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    second_call_messages = runtime.calls[1]["messages"]
    system_messages = [m for m in second_call_messages if m.role == "system"]
    assert any(
        "Do not repeat the same invalid call" in m.content and "weather" in m.content
        for m in system_messages
    )


def test_normal_result_no_enrichment() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="file.read",
                        arguments={"path": "app.py"},
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
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="file content here",
                ),
            )
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile_with_recovery(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read file")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT

    second_call_messages = runtime.calls[1]["messages"]
    system_messages = [m for m in second_call_messages if m.role == "system"]
    assert not any("[system]" in m.content for m in system_messages)

    scratchpad = outcome.state.scratchpad
    assert scratchpad.get("micro_correction_count", 0) == 0


def test_enrichment_contains_tool_name_and_score() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="exec.run",
                        arguments={"cmd": "bad-cmd"},
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
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="failed",
                    summary="command not found",
                ),
            )
        ],
    )

    run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile_with_recovery(allowed_tools=frozenset({"exec.run"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="run command")],
        tool_specs=_tool_specs("exec.run"),
    )

    second_call_messages = runtime.calls[1]["messages"]
    system_messages = [m for m in second_call_messages if m.role == "system"]
    enrichment = next(m for m in system_messages if "[system]" in m.content)

    assert "exec.run" in enrichment.content
    assert "1.00" in enrichment.content  # score formatted to 2 decimal places
    assert "command not found" in enrichment.content
    assert "Review before proceeding." in enrichment.content


def test_repeated_failure_sets_escalation_flag() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="exec.run",
                        arguments={"cmd": "pytest"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="exec.run",
                        arguments={"cmd": "pytest"},
                    ),
                    ToolCall(
                        id="call-3",
                        name="file.read",
                        arguments={"path": "log.txt"},
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
    loop_ctx = _LoopContext(
        state=_state(tool_calls=5, llm_calls_max=10),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="failed",
                    summary="pytest failed first time",
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="failed",
                    summary="pytest failed second time",
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="log content",
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile_with_recovery(
            allowed_tools=frozenset({"exec.run", "file.read"})
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="run tests")],
        tool_specs=_tool_specs("exec.run", "file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    scratchpad = outcome.state.scratchpad
    assert scratchpad.get("micro_correction_count", 0) >= 2
    assert scratchpad.get("layer2_escalation_needed") is True


def test_different_failures_no_escalation() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="exec.run",
                        arguments={"cmd": "pytest"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="exec.run",
                        arguments={"cmd": "make build"},
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
    loop_ctx = _LoopContext(
        state=_state(tool_calls=5, llm_calls_max=10),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="failed",
                    summary="pytest failed",
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="failed",
                    summary="make failed",
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile_with_recovery(allowed_tools=frozenset({"exec.run"})),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="run tests then build")],
        tool_specs=_tool_specs("exec.run"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    scratchpad = outcome.state.scratchpad
    assert scratchpad.get("layer2_escalation_needed") is not True


def _profile_with_budget_hint(
    *,
    allowed_tools: frozenset[str],
    max_iterations: int = 6,
    max_llm_calls_per_loop: int = 5,
    max_tool_calls_per_loop: int | None = None,
    budget_conserve_threshold: float = 0.20,
    profile_name: str = "shared_adaptive_test",
) -> AdaptiveToolLoopProfile:
    return AdaptiveToolLoopProfile(
        profile_name=profile_name,
        mode_name="act_adaptive",
        allowed_tools=allowed_tools,
        max_iterations=max_iterations,
        max_tool_calls_per_loop=max_tool_calls_per_loop,
        max_llm_calls_per_loop=max_llm_calls_per_loop,
        budget_conserve_threshold=budget_conserve_threshold,
        allow_llm_recovery_after_tool_failure=True,
    )


def test_tool_efficiency_guidance_injected_for_general_profile_with_budget_numbers() -> (
    None
):
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Here are three snippet-backed headlines.",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "The model declares the general act answer complete.",
                },
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state())

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            profile_name="general_adaptive_v1",
            allowed_tools=frozenset({"web.search", "web.fetch"}),
            max_iterations=12,
            max_tool_calls_per_loop=18,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="top 3 news today")],
        tool_specs=_tool_specs("web.search", "web.fetch"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    system_text = "\n".join(
        str(message.content)
        for message in runtime.calls[0]["messages"]
        if message.role == "system"
    )
    assert "Tool efficiency rules:" in system_text
    assert "summarize from the snippets directly" in system_text
    assert "current-events, latest-news, or top-N requests" in system_text
    assert "override any skill or example procedure" in system_text
    assert "budget or per-tool limit error" in system_text
    assert "12 iterations / 18 tool calls" in system_text


def test_tool_efficiency_guidance_is_absent_for_watch_and_consolidation_profiles() -> (
    None
):
    for profile_name, allowed_tools, tool_specs in (
        ("watch_check_v1", frozenset({"web.search"}), _tool_specs("web.search")),
        ("watch_action_v1", frozenset({"web.search"}), _tool_specs("web.search")),
        ("memory_consolidation_v1", frozenset(), []),
    ):
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="fake-model",
                    output_text="Done.",
                    finish_reason="stop",
                )
            ]
        )
        loop_ctx = _LoopContext(state=_state())

        run_adaptive_tool_loop(
            loop_ctx,
            profile=_profile(
                profile_name=profile_name,
                allowed_tools=allowed_tools,
                max_iterations=3,
                max_tool_calls_per_loop=5,
            ),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="background turn")],
            tool_specs=tool_specs,
        )

        system_text = "\n".join(
            str(message.content)
            for message in runtime.calls[0]["messages"]
            if message.role == "system"
        )
        assert "Tool efficiency rules:" not in system_text
        assert "summarize from the snippets directly" not in system_text
        assert "current-events, latest-news, or top-N requests" not in system_text


def test_general_profile_forces_answer_only_finalization_after_tool_budget_denial() -> (
    None
):
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="web.search",
                        arguments={"query": "latest news"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="web.search",
                        arguments={"query": "more latest news"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Here are the top three snippet-backed stories.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=6),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="search ok",
                    outputs={"content": "Search snippets for three current stories."},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="blocked",
                    summary="tool_budget_calls_exceeded",
                    error=ActionError(
                        code="BUDGET_EXCEEDED",
                        message="tool_budget_calls_exceeded",
                    ),
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            profile_name="general_adaptive_v1",
            allowed_tools=frozenset({"web.search"}),
            max_iterations=6,
            max_tool_calls_per_loop=18,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[
            Message(
                role="user",
                content="gather latest news last 48h and summarize top 3",
            )
        ],
        tool_specs=_tool_specs("web.search"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "Here are the top three snippet-backed stories."
    assert runtime.calls[-1]["tool_choice"] == "none"
    assert runtime.calls[-1]["tools"] == []
    assert outcome.state.scratchpad["budget_answer_only_finalization_forced"] is True
    finalization_system_text = "\n".join(
        str(message.content)
        for message in runtime.calls[-1]["messages"]
        if message.role == "system"
    )
    assert "per-tool limit has been reached" in finalization_system_text
    assert "write the best user-facing final answer now" in finalization_system_text


def test_budget_hint_injected_when_budget_below_threshold() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c2", name="file.read", arguments={"path": "b.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Requested file reads completed and final answer is present.",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=10, llm_calls_max=20),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="ok"
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="ok"
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile_with_budget_hint(
            allowed_tools=frozenset({"file.read"}),
            profile_name="general_adaptive_v1",
            max_llm_calls_per_loop=3,
            max_tool_calls_per_loop=6,
            budget_conserve_threshold=0.50,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read files")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.state.scratchpad.get("budget_hint_injected") is True

    third_call_messages = runtime.calls[2]["messages"]
    system_messages = [m for m in third_call_messages if m.role == "system"]
    assert any("Budget is running low" in m.content for m in system_messages)
    assert any("Tool efficiency rules:" in m.content for m in system_messages)
    assert any("6 iterations / 6 tool calls" in m.content for m in system_messages)


def test_budget_hint_not_injected_above_threshold() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a.py"})
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
    loop_ctx = _LoopContext(
        state=_state(tool_calls=10, llm_calls_max=20),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="ok"
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile_with_budget_hint(
            allowed_tools=frozenset({"file.read"}),
            max_llm_calls_per_loop=10,  # 1 call / 10 max → 90% remaining, well above 0.20
            budget_conserve_threshold=0.20,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read one file")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert not outcome.state.scratchpad.get("budget_hint_injected")

    for call in runtime.calls:
        system_messages = [m for m in call["messages"] if m.role == "system"]
        assert not any("Budget is running low" in m.content for m in system_messages)


def test_budget_hint_injected_at_most_once() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c2", name="file.read", arguments={"path": "b.py"})
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
    loop_ctx = _LoopContext(
        state=_state(tool_calls=10, llm_calls_max=20),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="ok"
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="ok"
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile_with_budget_hint(
            allowed_tools=frozenset({"file.read"}),
            max_llm_calls_per_loop=100,
            budget_conserve_threshold=0.99,  # always fires immediately
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read files")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT

    all_budget_hint_messages = 0
    for call in runtime.calls:
        system_messages = [m for m in call["messages"] if m.role == "system"]
        for m in system_messages:
            if "Budget is running low" in m.content:
                all_budget_hint_messages += 1
    assert all_budget_hint_messages >= 1


def test_three_identical_tool_sequences_trigger_circular_pattern() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c2", name="file.read", arguments={"path": "b.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c3", name="file.read", arguments={"path": "c.py"})
                ],
                finish_reason="tool_calls",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=10, llm_calls_max=20),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="ok"
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="ok"
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="ok"
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"}), max_iterations=10),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_CIRCULAR_PATTERN


def test_circular_pattern_finalization_uses_compact_reserved_answer_call() -> None:
    large_output = "x" * 5000
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c2", name="file.read", arguments={"path": "b.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c3", name="file.read", arguments={"path": "c.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Final answer from compact evidence.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=10, llm_calls_max=20),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="read a.py",
                    outputs={"content": large_output},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="read b.py",
                    outputs={"content": large_output},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="read c.py",
                    outputs={"content": large_output},
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile_with_budget_hint(
            allowed_tools=frozenset({"file.read"}),
            max_iterations=10,
            max_llm_calls_per_loop=3,
            profile_name="general_adaptive_v1",
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read and summarize")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "Final answer from compact evidence."
    assert len(runtime.calls) == 4
    final_messages = runtime.calls[-1]["messages"]
    assert len(final_messages) == 2
    assert "read and summarize" in final_messages[0].content
    assert len(final_messages[0].content) < 6000
    assert runtime.calls[-1]["tool_choice"] == "none"


def test_two_identical_tool_sequences_do_not_trigger_circular_pattern() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c2", name="file.read", arguments={"path": "b.py"})
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
    loop_ctx = _LoopContext(
        state=_state(tool_calls=10, llm_calls_max=20),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="ok"
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="ok"
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"}), max_iterations=10),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="read")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT


def test_different_tool_sequences_do_not_trigger_circular_pattern() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c2", name="exec.run", arguments={"cmd": "pytest"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c3", name="file.read", arguments={"path": "b.py"})
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
    loop_ctx = _LoopContext(
        state=_state(tool_calls=10, llm_calls_max=20),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="ok"
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="ok"
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="ok"
                ),
            ),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read", "exec.run"}),
            max_iterations=10,
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="mix of tools")],
        tool_specs=_tool_specs("file.read", "exec.run"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
