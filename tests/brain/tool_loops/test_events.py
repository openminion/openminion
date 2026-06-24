from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    WorkingState,
    new_uuid,
)
from openminion.modules.brain.loop.tools import (
    AdaptiveToolLoopProfile,
    run_adaptive_tool_loop,
)
from openminion.modules.brain.loop.tools.events import (
    AdaptiveLoopIterationEvent,
    IterationToolCallRecord,
)
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.llm.schemas import LLMResponse, Message, ToolCall, ToolSpec


# Shared test helpers


def _working_state(tool_calls: int = 10, tokens: int = 10000) -> WorkingState:
    return WorkingState(
        session_id="s-events-test",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=20,
            tool_calls=tool_calls,
            a2a_calls=0,
            tokens=tokens,
            time_ms=120000,
        ),
        llm_calls_max=10,
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
    max_iterations: int = 5,
    allowed_tools: frozenset[str] = frozenset({"file.read"}),
) -> AdaptiveToolLoopProfile:
    return AdaptiveToolLoopProfile(
        profile_name="events_test_profile",
        mode_name="act_adaptive",
        allowed_tools=allowed_tools,
        max_iterations=max_iterations,
    )


@dataclass
class _FakeRuntime:
    responses: list[LLMResponse] = field(default_factory=list)
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
        response = self.responses[self._index]
        self._index += 1
        return response


@dataclass
class _LoopContext:
    state: WorkingState
    outcomes: list[CommandExecutionOutcome] = field(default_factory=list)
    statuses: list[dict[str, Any]] = field(default_factory=list)
    _index: int = 0

    def execute_command(self, *, command, include_reflect: bool = False):
        outcome = self.outcomes[self._index]
        self._index += 1
        return outcome

    def emit_status(self, **kwargs) -> None:
        self.statuses.append(dict(kwargs))


def _ok_action_result() -> ActionResult:
    return ActionResult(
        command_id=new_uuid(),
        status="success",
        summary="ok",
    )


def _final_response(text: str = "done") -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text=text,
        finish_reason="stop",
    )


def _tool_response(tool_name: str = "file.read") -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[ToolCall(id="call-1", name=tool_name, arguments={"path": "x.py"})],
        finish_reason="tool_calls",
    )


def _iter_events(statuses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        s["payload"]
        for s in statuses
        if s.get("source_event") == "adaptive_loop_iteration"
        and isinstance(s.get("payload"), dict)
    ]


# Test 1: Event emitted per iteration with correct fields


def test_iteration_event_emitted_per_iteration() -> None:
    runtime = _FakeRuntime(
        responses=[
            _tool_response("file.read"),
            _final_response(),
        ]
    )
    loop_ctx = _LoopContext(
        state=_working_state(),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=_ok_action_result(),
            )
        ],
    )

    run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="go")],
        tool_specs=_tool_specs("file.read"),
    )

    events = _iter_events(loop_ctx.statuses)
    # Two iterations: one with a tool call, one that produces final text
    assert len(events) == 2

    # First iteration: has a tool call
    first = events[0]
    assert first["event_type"] == "adaptive_loop_iteration"
    assert first["iteration_index"] == 1
    assert isinstance(first["llm_call_duration_ms"], int)
    assert first["llm_call_duration_ms"] >= 0
    assert isinstance(first["tool_calls"], list)
    assert isinstance(first["tokens_used_this_iteration"], int)
    assert isinstance(first["budget_remaining"], dict)
    assert isinstance(first["reflection_triggered"], bool)
    assert first["termination_reason"] is None

    # Second iteration: no tool calls (final text)
    second = events[1]
    assert second["iteration_index"] == 2
    assert second["tool_calls"] == []


# Test 2: Tool call records include cache_hit and parallel flags


def test_tool_call_records_have_cache_hit_and_parallel_flags() -> None:
    runtime = _FakeRuntime(
        responses=[
            _tool_response("file.read"),
            _final_response(),
        ]
    )
    loop_ctx = _LoopContext(
        state=_working_state(),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=_ok_action_result(),
            )
        ],
    )

    run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="go")],
        tool_specs=_tool_specs("file.read"),
    )

    events = _iter_events(loop_ctx.statuses)
    first = events[0]
    assert len(first["tool_calls"]) == 1

    tc_record = first["tool_calls"][0]
    assert tc_record["tool_name"] == "file.read"
    assert isinstance(tc_record["duration_ms"], int)
    assert isinstance(tc_record["status"], str)
    assert tc_record["status"] == "success"
    assert isinstance(tc_record["cache_hit"], bool)
    assert isinstance(tc_record["parallel"], bool)
    # First call is not a cache hit
    assert tc_record["cache_hit"] is False


# Test 3: Termination reason populated only on final iteration


def test_termination_reason_none_on_non_final_iterations() -> None:
    # Use distinct args for each tool call to avoid duplicate-batch termination
    r1 = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[ToolCall(id="c1", name="file.read", arguments={"path": "a.py"})],
        finish_reason="tool_calls",
    )
    r2 = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[ToolCall(id="c2", name="file.read", arguments={"path": "b.py"})],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(responses=[r1, r2, _final_response()])
    loop_ctx = _LoopContext(
        state=_working_state(),
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=_ok_action_result(),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=_ok_action_result(),
            ),
        ],
    )

    run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(max_iterations=5),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="go")],
        tool_specs=_tool_specs("file.read"),
    )

    events = _iter_events(loop_ctx.statuses)
    # Three iterations: two with tool calls, one with final text
    assert len(events) == 3
    for ev in events:
        assert ev["termination_reason"] is None


# Test 4: Zero-tool-call iteration handled gracefully


def test_zero_tool_call_iteration() -> None:
    runtime = _FakeRuntime(responses=[_final_response()])
    loop_ctx = _LoopContext(state=_working_state())

    run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="go")],
        tool_specs=_tool_specs("file.read"),
    )

    events = _iter_events(loop_ctx.statuses)
    assert len(events) == 1
    ev = events[0]
    assert ev["tool_calls"] == []
    assert ev["event_type"] == "adaptive_loop_iteration"
    assert ev["iteration_index"] == 1


# Unit tests for AdaptiveLoopIterationEvent.to_dict


def test_event_to_dict_structure() -> None:
    tc = IterationToolCallRecord(
        tool_name="exec.run",
        duration_ms=42,
        status="success",
        cache_hit=True,
        parallel=False,
    )
    event = AdaptiveLoopIterationEvent(
        iteration_index=3,
        llm_call_duration_ms=150,
        tool_calls=(tc,),
        tokens_used_this_iteration=200,
        budget_remaining={"tokens": 500, "tool_calls": 8},
        reflection_triggered=False,
        termination_reason=None,
    )
    d = event.to_dict()
    assert d["event_type"] == "adaptive_loop_iteration"
    assert d["iteration_index"] == 3
    assert d["llm_call_duration_ms"] == 150
    assert len(d["tool_calls"]) == 1
    assert d["tool_calls"][0]["tool_name"] == "exec.run"
    assert d["tool_calls"][0]["cache_hit"] is True
    assert d["tool_calls"][0]["parallel"] is False
    assert d["tokens_used_this_iteration"] == 200
    assert d["budget_remaining"] == {"tokens": 500, "tool_calls": 8}
    assert d["reflection_triggered"] is False
    assert d["termination_reason"] is None
