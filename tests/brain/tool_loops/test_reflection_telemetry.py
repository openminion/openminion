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
    ADAPTIVE_TERM_FINAL_TEXT,
    AdaptiveToolLoopProfile,
    run_adaptive_tool_loop,
)
from openminion.modules.brain.loop.tools.status import (
    adaptive_status_payload,
    loop_reflection_payload,
)
from openminion.modules.brain.loop.tools.contracts import (
    AdaptiveToolLoopState,
)
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.llm.schemas import LLMResponse, Message, ToolCall, ToolSpec


# Shared helpers


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
        del include_reflect
        outcome = self.outcomes[self._index]
        self._index += 1
        return outcome

    def emit_status(self, **kwargs) -> None:
        self.statuses.append(dict(kwargs))


def _state() -> WorkingState:
    return WorkingState(
        session_id="s-test",
        agent_id="agent-test",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=10,
            a2a_calls=0,
            tokens=50000,
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


def _ok_result(summary: str = "ok") -> ActionResult:
    return ActionResult(command_id=new_uuid(), status="success", summary=summary)


def _error_result(summary: str = "failed") -> ActionResult:
    return ActionResult(
        command_id=new_uuid(),
        status="failed",
        summary=summary,
    )


def _profile_with_policy(
    policy: str, threshold: float = 0.6
) -> AdaptiveToolLoopProfile:
    return AdaptiveToolLoopProfile(
        profile_name="test_profile",
        mode_name="test_mode",
        allowed_tools=frozenset({"file.read"}),
        max_iterations=4,
        reflection_policy=policy,  # type: ignore[arg-type]
        reflection_anomaly_threshold=threshold,
    )


def _llm_tool_then_done() -> list[LLMResponse]:
    return [
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


# Unit tests for loop_reflection_payload


class TestLoopReflectionPayload:
    def test_empty_scratchpad_returns_zero_calls_and_empty_triggers(self):
        result = loop_reflection_payload({})
        assert result["loop.reflection_calls"] == 0
        assert result["loop.reflection_triggers"] == []

    def test_reflection_calls_propagated(self):
        result = loop_reflection_payload({"reflection_calls": 3})
        assert result["loop.reflection_calls"] == 3

    def test_reflection_triggers_propagated(self):
        triggers = [
            {
                "iteration": 1,
                "tool_name": "file.read",
                "anomaly_score": 1.0,
                "triggered": True,
            }
        ]
        result = loop_reflection_payload({"reflection_triggers": triggers})
        assert result["loop.reflection_triggers"] == triggers

    def test_missing_keys_default_to_zero_and_empty(self):
        result = loop_reflection_payload({"some_other_key": 99})
        assert result["loop.reflection_calls"] == 0
        assert result["loop.reflection_triggers"] == []


# Unit tests for adaptive_status_payload including reflection fields


class TestAdaptiveStatusPayloadReflection:
    def _make_profile(self, policy: str = "never") -> AdaptiveToolLoopProfile:
        return AdaptiveToolLoopProfile(
            profile_name="p",
            mode_name="m",
            allowed_tools=frozenset({"file.read"}),
            reflection_policy=policy,  # type: ignore[arg-type]
        )

    def _make_loop_state(self, scratchpad: dict | None = None) -> AdaptiveToolLoopState:
        return AdaptiveToolLoopState(scratchpad=scratchpad or {})

    def test_never_policy_emits_reflection_calls_zero(self):
        profile = self._make_profile("never")
        state = self._make_loop_state()
        payload = adaptive_status_payload(profile=profile, loop_state=state)
        assert payload["loop.reflection_calls"] == 0
        assert payload["loop.reflection_triggers"] == []

    def test_always_policy_emits_reflection_calls_from_scratchpad(self):
        profile = self._make_profile("always")
        state = self._make_loop_state({"reflection_calls": 2})
        payload = adaptive_status_payload(profile=profile, loop_state=state)
        assert payload["loop.reflection_calls"] == 2

    def test_anomaly_policy_emits_triggers_from_scratchpad(self):
        profile = self._make_profile("anomaly")
        triggers = [
            {
                "iteration": 1,
                "tool_name": "exec.run",
                "anomaly_score": 0.9,
                "triggered": True,
            }
        ]
        state = self._make_loop_state(
            {"reflection_calls": 1, "reflection_triggers": triggers}
        )
        payload = adaptive_status_payload(profile=profile, loop_state=state)
        assert payload["loop.reflection_calls"] == 1
        assert payload["loop.reflection_triggers"] == triggers


# Integration tests: engine records anomaly evaluation in scratchpad


class TestEngineAnomalyRecording:
    def test_never_policy_no_triggers_recorded(self):
        runtime = _FakeRuntime(responses=_llm_tool_then_done())
        loop_ctx = _LoopContext(
            state=_state(),
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=_ok_result(),
                ),
            ],
        )
        outcome = run_adaptive_tool_loop(
            loop_ctx,
            profile=_profile_with_policy("never"),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="go")],
            tool_specs=_tool_specs("file.read"),
        )
        assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert outcome.state.scratchpad.get("reflection_calls", 0) == 0
        assert outcome.state.scratchpad.get("reflection_triggers", []) == []

    def test_anomaly_policy_normal_result_records_non_trigger(self):
        runtime = _FakeRuntime(responses=_llm_tool_then_done())
        loop_ctx = _LoopContext(
            state=_state(),
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=_ok_result("content of file"),
                ),
            ],
        )
        outcome = run_adaptive_tool_loop(
            loop_ctx,
            profile=_profile_with_policy("anomaly", threshold=0.6),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="go")],
            tool_specs=_tool_specs("file.read"),
        )
        assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        triggers = outcome.state.scratchpad.get("reflection_triggers", [])
        assert len(triggers) == 1
        entry = triggers[0]
        assert entry["tool_name"] == "file.read"
        assert entry["triggered"] is False
        assert entry["anomaly_score"] == 0.0
        # reflection_calls should NOT be incremented for non-triggering result
        assert outcome.state.scratchpad.get("reflection_calls", 0) == 0

    def test_anomaly_policy_error_result_records_trigger(self):
        runtime = _FakeRuntime(responses=_llm_tool_then_done())
        loop_ctx = _LoopContext(
            state=_state(),
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=_error_result("file not found"),
                ),
            ],
        )
        outcome = run_adaptive_tool_loop(
            loop_ctx,
            profile=_profile_with_policy("anomaly", threshold=0.6),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="go")],
            tool_specs=_tool_specs("file.read"),
        )
        assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        triggers = outcome.state.scratchpad.get("reflection_triggers", [])
        assert len(triggers) == 1
        entry = triggers[0]
        assert entry["tool_name"] == "file.read"
        assert entry["triggered"] is True
        assert entry["anomaly_score"] >= 0.6
        assert outcome.state.scratchpad.get("reflection_calls", 0) == 1

    def test_always_policy_no_anomaly_detection_in_scratchpad(self):
        runtime = _FakeRuntime(responses=_llm_tool_then_done())
        loop_ctx = _LoopContext(
            state=_state(),
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=_ok_result(),
                ),
            ],
        )
        outcome = run_adaptive_tool_loop(
            loop_ctx,
            profile=_profile_with_policy("always"),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="go")],
            tool_specs=_tool_specs("file.read"),
        )
        assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        # No anomaly data written by engine for 'always' policy
        assert outcome.state.scratchpad.get("reflection_calls", 0) == 0
        assert outcome.state.scratchpad.get("reflection_triggers", []) == []

    def test_trigger_entry_contains_iteration_field(self):
        runtime = _FakeRuntime(responses=_llm_tool_then_done())
        loop_ctx = _LoopContext(
            state=_state(),
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=_ok_result(),
                ),
            ],
        )
        outcome = run_adaptive_tool_loop(
            loop_ctx,
            profile=_profile_with_policy("anomaly"),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="go")],
            tool_specs=_tool_specs("file.read"),
        )
        triggers = outcome.state.scratchpad.get("reflection_triggers", [])
        assert len(triggers) == 1
        assert "iteration" in triggers[0]
        assert triggers[0]["iteration"] == 1

    def test_status_payload_includes_reflection_telemetry(self):
        runtime = _FakeRuntime(responses=_llm_tool_then_done())
        loop_ctx = _LoopContext(
            state=_state(),
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=_error_result("err"),
                ),
            ],
        )
        run_adaptive_tool_loop(
            loop_ctx,
            profile=_profile_with_policy("anomaly"),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="go")],
            tool_specs=_tool_specs("file.read"),
        )
        # At least one status event should carry loop.reflection_calls
        all_payloads = [
            s.get("payload", {}) for s in loop_ctx.statuses if s.get("payload")
        ]
        found = any("loop.reflection_calls" in p for p in all_payloads)
        assert found, "No status payload contained 'loop.reflection_calls'"
