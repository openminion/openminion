from __future__ import annotations

import json
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
from openminion.modules.brain.loop.tools.snapshot import (
    LoopSnapshot,
    LoopToolCallRecord,
    compress_transcript,
    hash_args,
)
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.llm.schemas import LLMResponse, Message, ToolCall, ToolSpec


# Helpers shared across tests


def _working_state(
    tool_calls: int = 5,
    tokens: int = 5000,
    trace_id: str | None = None,
) -> WorkingState:
    return WorkingState(
        session_id="s-snap-test",
        agent_id="agent",
        trace_id=trace_id,
        budgets_remaining=BudgetCounters(
            ticks=10,
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
    profile_name: str = "snap_test_profile",
    allowed_tools: frozenset[str] = frozenset({"file.read"}),
    max_iterations: int = 4,
) -> AdaptiveToolLoopProfile:
    return AdaptiveToolLoopProfile(
        profile_name=profile_name,
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


# Snapshot serialization round-trip


def test_snapshot_round_trip() -> None:
    original = LoopSnapshot(
        turn_scope_id="trace-original",
        iteration_index=3,
        message_transcript=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ],
        tool_call_history=[
            LoopToolCallRecord(
                tool_name="file.read",
                args_hash="abc12345",
                result_summary="contents of file",
            ),
        ],
        budgets_consumed={"llm_calls": 2, "tool_calls": 1},
        profile_name="my_profile",
        model="claude-3-sonnet",
        allowed_tools=frozenset({"file.read", "exec.run"}),
    )

    restored = LoopSnapshot.from_dict(original.to_dict())

    assert restored.iteration_index == original.iteration_index
    assert restored.message_transcript == original.message_transcript
    assert len(restored.tool_call_history) == 1
    assert restored.tool_call_history[0].tool_name == "file.read"
    assert restored.tool_call_history[0].args_hash == "abc12345"
    assert restored.tool_call_history[0].result_summary == "contents of file"
    assert restored.budgets_consumed == original.budgets_consumed
    assert restored.profile_name == original.profile_name
    assert restored.model == original.model
    assert restored.allowed_tools == original.allowed_tools


def test_snapshot_json_round_trip() -> None:
    snap = LoopSnapshot(
        turn_scope_id="trace-json",
        iteration_index=1,
        message_transcript=[{"role": "user", "content": "hi"}],
        tool_call_history=[],
        budgets_consumed={"llm_calls": 1, "tool_calls": 0},
        profile_name="p",
        model="m",
        allowed_tools=frozenset({"tool_a"}),
    )
    restored = LoopSnapshot.from_json(snap.to_json())
    assert restored.iteration_index == 1
    assert restored.allowed_tools == frozenset({"tool_a"})


def test_hash_args_is_deterministic() -> None:
    h1 = hash_args({"b": 2, "a": 1})
    h2 = hash_args({"a": 1, "b": 2})
    assert h1 == h2
    assert len(h1) == 16


# Transcript compression


def test_small_transcript_not_compressed() -> None:
    messages = [{"role": "user", "content": "hi"}]
    result = compress_transcript(messages, max_chars=10000)
    assert result == messages


def test_oversized_transcript_compressed() -> None:
    big_content = "x" * 500
    messages = [{"role": "user", "content": big_content} for _ in range(30)]
    total = sum(len(json.dumps(m, default=str)) for m in messages)
    assert total > 10000, "precondition: messages must exceed max_chars"

    result = compress_transcript(messages, max_chars=10000)
    compressed_total = sum(len(json.dumps(m, default=str)) for m in result)
    assert compressed_total <= 10000
    assert len(result) < len(messages)
    # First two messages must be preserved
    assert result[0] == messages[0]
    assert result[1] == messages[1]
    # A compression marker must be present
    assert any("compressed" in str(m.get("content", "")) for m in result)


# Resume semantics


def test_missing_module_state_initializes() -> None:
    state = _working_state()
    assert state.module_state == {}  # no snapshot present

    runtime = _FakeRuntime(responses=[_final_response()])
    loop_ctx = _LoopContext(state=state)

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="go")],
        tool_specs=_tool_specs("file.read"),
    )
    # Should complete without error
    assert outcome.termination_reason == "final_text"
    # Scratchpad should have resume telemetry with no resume
    assert outcome.state.scratchpad["resumed_from_snapshot"] is False
    assert outcome.state.scratchpad["resume_iteration_index"] is None


def test_resume_matching_conditions() -> None:
    snap = LoopSnapshot(
        turn_scope_id="trace-resume-match",
        iteration_index=2,
        message_transcript=[],
        tool_call_history=[],
        budgets_consumed={"llm_calls": 2, "tool_calls": 1},
        profile_name="snap_test_profile",
        model="fake-model",
        allowed_tools=frozenset({"file.read"}),
    )
    state = _working_state(trace_id="trace-resume-match")
    state.module_state = {"adaptive_loop": snap.to_dict()}

    runtime = _FakeRuntime(responses=[_final_response()])
    loop_ctx = _LoopContext(state=state)

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(profile_name="snap_test_profile"),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="go")],
        tool_specs=_tool_specs("file.read"),
    )
    # Iteration should start from snap.iteration_index + 1 = 3
    # The engine increments at top of loop, so first iteration value seen is 4
    assert outcome.state.iteration >= 3
    assert outcome.state.llm_calls >= 2  # preserved from snapshot
    assert outcome.state.scratchpad["resumed_from_snapshot"] is True
    assert outcome.state.scratchpad["resume_iteration_index"] == 2
    # Snapshot should have been consumed (cleared)
    assert "adaptive_loop" not in (state.module_state or {})


def test_resume_mismatched_profile_discards() -> None:
    snap = LoopSnapshot(
        turn_scope_id="trace-resume-profile",
        iteration_index=5,
        message_transcript=[],
        tool_call_history=[],
        budgets_consumed={"llm_calls": 5, "tool_calls": 3},
        profile_name="OTHER_PROFILE",  # mismatch
        model="fake-model",
        allowed_tools=frozenset({"file.read"}),
    )
    state = _working_state(trace_id="trace-resume-profile")
    state.module_state = {"adaptive_loop": snap.to_dict()}

    runtime = _FakeRuntime(responses=[_final_response()])
    loop_ctx = _LoopContext(state=state)

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(profile_name="snap_test_profile"),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="go")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.state.scratchpad["resumed_from_snapshot"] is False
    assert outcome.state.scratchpad["resume_iteration_index"] is None
    # iteration starts from 1 (not resumed from 5+1)
    assert outcome.state.iteration == 1


def test_resume_mismatched_model_discards() -> None:
    snap = LoopSnapshot(
        turn_scope_id="trace-resume-model",
        iteration_index=3,
        message_transcript=[],
        tool_call_history=[],
        budgets_consumed={"llm_calls": 3, "tool_calls": 2},
        profile_name="snap_test_profile",
        model="different-model",  # mismatch
        allowed_tools=frozenset({"file.read"}),
    )
    state = _working_state(trace_id="trace-resume-model")
    state.module_state = {"adaptive_loop": snap.to_dict()}

    runtime = _FakeRuntime(responses=[_final_response()])
    loop_ctx = _LoopContext(state=state)

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(profile_name="snap_test_profile"),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="go")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.state.scratchpad["resumed_from_snapshot"] is False
    assert outcome.state.iteration == 1


def test_resume_mismatched_tools_discards() -> None:
    snap = LoopSnapshot(
        turn_scope_id="trace-resume-tools",
        iteration_index=2,
        message_transcript=[],
        tool_call_history=[],
        budgets_consumed={"llm_calls": 2, "tool_calls": 1},
        profile_name="snap_test_profile",
        model="fake-model",
        allowed_tools=frozenset({"exec.run"}),  # mismatch
    )
    state = _working_state(trace_id="trace-resume-tools")
    state.module_state = {"adaptive_loop": snap.to_dict()}

    runtime = _FakeRuntime(responses=[_final_response()])
    loop_ctx = _LoopContext(state=state)

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            profile_name="snap_test_profile",
            allowed_tools=frozenset({"file.read"}),
        ),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="go")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.state.scratchpad["resumed_from_snapshot"] is False
    assert outcome.state.iteration == 1


# Resume telemetry in status payload


def test_resume_telemetry() -> None:
    snap = LoopSnapshot(
        turn_scope_id="trace-status",
        iteration_index=1,
        message_transcript=[],
        tool_call_history=[],
        budgets_consumed={"llm_calls": 1, "tool_calls": 0},
        profile_name="snap_test_profile",
        model="fake-model",
        allowed_tools=frozenset({"file.read"}),
    )
    state = _working_state(trace_id="trace-status")
    state.module_state = {"adaptive_loop": snap.to_dict()}

    runtime = _FakeRuntime(responses=[_final_response()])
    loop_ctx = _LoopContext(state=state)

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(profile_name="snap_test_profile"),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="go")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.state.scratchpad["resumed_from_snapshot"] is True
    assert outcome.state.scratchpad["resume_iteration_index"] == 1

    # Verify the telemetry appears in at least one emitted status
    resume_payloads = [
        s["payload"]
        for s in loop_ctx.statuses
        if isinstance(s.get("payload"), dict)
        and "loop.resumed_from_snapshot" in s["payload"]
    ]
    assert resume_payloads, "No status payload contained loop.resumed_from_snapshot"
    assert resume_payloads[0]["loop.resumed_from_snapshot"] is True
    assert resume_payloads[0]["loop.resume_iteration_index"] == 1


def test_snapshot_saved_to_module_state_after_tool_call() -> None:
    state = _working_state()

    runtime = _FakeRuntime(
        responses=[
            _tool_response("file.read"),
            _final_response(),
        ]
    )
    loop_ctx = _LoopContext(
        state=state,
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

    assert "adaptive_loop" in state.module_state
    snap_dict = state.module_state["adaptive_loop"]
    assert str(snap_dict["turn_scope_id"])
    assert snap_dict["turn_scope_id"] == state.trace_id
    assert snap_dict["profile_name"] == "snap_test_profile"
    assert snap_dict["model"] == "fake-model"
    assert "file.read" in snap_dict["allowed_tools"]


def test_resume_mismatched_turn_scope_discards_and_clears() -> None:
    snap = LoopSnapshot(
        turn_scope_id="trace-old",
        iteration_index=7,
        message_transcript=[],
        tool_call_history=[],
        budgets_consumed={"llm_calls": 7, "tool_calls": 4},
        profile_name="snap_test_profile",
        model="fake-model",
        allowed_tools=frozenset({"file.read"}),
    )
    state = _working_state(trace_id="trace-new")
    state.module_state = {"adaptive_loop": snap.to_dict()}

    runtime = _FakeRuntime(responses=[_final_response()])
    loop_ctx = _LoopContext(state=state)

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(profile_name="snap_test_profile"),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="go")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.state.scratchpad["resumed_from_snapshot"] is False
    assert outcome.state.iteration == 1
    assert "adaptive_loop" not in (state.module_state or {})


def test_legacy_snapshot_without_turn_scope_discards_and_clears() -> None:
    legacy_snapshot = {
        "iteration_index": 4,
        "message_transcript": [],
        "tool_call_history": [],
        "budgets_consumed": {"llm_calls": 4, "tool_calls": 2},
        "profile_name": "snap_test_profile",
        "model": "fake-model",
        "allowed_tools": ["file.read"],
    }
    state = _working_state(trace_id="trace-legacy")
    state.module_state = {"adaptive_loop": legacy_snapshot}

    runtime = _FakeRuntime(responses=[_final_response()])
    loop_ctx = _LoopContext(state=state)

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(profile_name="snap_test_profile"),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="go")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.state.scratchpad["resumed_from_snapshot"] is False
    assert outcome.state.iteration == 1
    assert "adaptive_loop" not in (state.module_state or {})


def test_missing_trace_id_generates_fresh_scope_and_discards_stale_resume() -> None:
    snap = LoopSnapshot(
        turn_scope_id="trace-stale",
        iteration_index=6,
        message_transcript=[],
        tool_call_history=[],
        budgets_consumed={"llm_calls": 6, "tool_calls": 3},
        profile_name="snap_test_profile",
        model="fake-model",
        allowed_tools=frozenset({"file.read"}),
    )
    state = _working_state(trace_id=None)
    state.module_state = {"adaptive_loop": snap.to_dict()}

    runtime = _FakeRuntime(responses=[_final_response()])
    loop_ctx = _LoopContext(state=state)

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(profile_name="snap_test_profile"),
        runtime=runtime,
        model="fake-model",
        initial_messages=[Message(role="user", content="go")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.state.scratchpad["resumed_from_snapshot"] is False
    assert outcome.state.iteration == 1
    assert str(state.trace_id or "").strip()
    assert "adaptive_loop" not in (state.module_state or {})
