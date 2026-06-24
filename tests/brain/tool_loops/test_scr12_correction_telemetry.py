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
from openminion.modules.brain.loop.tools.contracts import AdaptiveToolLoopState
from openminion.modules.brain.loop.tools.status import (
    adaptive_status_payload,
    loop_correction_payload,
)
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.llm.schemas import LLMResponse, Message, ToolCall, ToolSpec


# Shared helpers (mirror pattern from test_reflection_telemetry.py)


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
        if self._index < len(self.responses):
            response = self.responses[self._index]
            self._index += 1
            return response
        return LLMResponse(
            ok=True,
            provider="fake",
            model=model,
            output_text="done",
        )


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
        session_id="s-scr12",
        agent_id="agent-scr12",
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


def _llm_tool_then_done(tool_name: str = "file.read") -> list[LLMResponse]:
    return [
        LLMResponse(
            ok=True,
            provider="fake",
            model="fake-model",
            output_text="",
            tool_calls=[ToolCall(id="c1", name=tool_name, arguments={"path": "a.py"})],
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


def _base_profile(*, max_macro_corrections: int = 0) -> AdaptiveToolLoopProfile:
    return AdaptiveToolLoopProfile(
        profile_name="test_profile",
        mode_name="test_mode",
        allowed_tools=frozenset({"file.read"}),
        max_iterations=4,
        max_macro_corrections=max_macro_corrections,
        macro_correction_cooldown=1,
    )


# Unit tests for loop_correction_payload


class TestLoopCorrectionPayload:
    def test_empty_scratchpad_returns_zero_values(self):
        result = loop_correction_payload({})
        assert result["loop.micro_corrections"] == 0
        assert result["loop.macro_corrections"] == 0
        assert result["loop.correction_types"] == {}
        assert result["loop.correction_budget_remaining"] == 0
        assert result["loop.correction_history_length"] == 0

    def test_micro_correction_count_propagated(self):
        result = loop_correction_payload({"micro_correction_count": 3})
        assert result["loop.micro_corrections"] == 3

    def test_macro_correction_count_propagated(self):
        result = loop_correction_payload({"macro_correction_count": 2})
        assert result["loop.macro_corrections"] == 2

    def test_correction_types_tallied_from_history(self):
        history = [
            {"correction_type": "retry_same", "iteration_index": 0, "applied": True},
            {"correction_type": "retry_same", "iteration_index": 1, "applied": True},
            {"correction_type": "replan", "iteration_index": 2, "applied": True},
        ]
        result = loop_correction_payload({"correction_history": history})
        assert result["loop.correction_types"]["retry_same"] == 2
        assert result["loop.correction_types"]["replan"] == 1

    def test_correction_budget_remaining_computed(self):
        result = loop_correction_payload(
            {"max_macro_corrections": 5, "macro_correction_count": 2}
        )
        assert result["loop.correction_budget_remaining"] == 3

    def test_correction_budget_remaining_clamped_to_zero(self):
        # macro_correction_count exceeds max (should not happen in practice, but clamp)
        result = loop_correction_payload(
            {"max_macro_corrections": 2, "macro_correction_count": 5}
        )
        assert result["loop.correction_budget_remaining"] == 0

    def test_correction_history_length_matches_list(self):
        history = [
            {"correction_type": "retry_same", "iteration_index": i, "applied": True}
            for i in range(7)
        ]
        result = loop_correction_payload({"correction_history": history})
        assert result["loop.correction_history_length"] == 7

    def test_unknown_correction_type_in_history(self):
        history = [{"iteration_index": 0, "applied": True}]
        result = loop_correction_payload({"correction_history": history})
        assert result["loop.correction_types"].get("unknown", 0) == 1

    def test_correction_types_empty_when_no_history(self):
        result = loop_correction_payload({"correction_history": []})
        assert result["loop.correction_types"] == {}


# Unit tests for adaptive_status_payload — correction fields present


class TestAdaptiveStatusPayloadCorrectionFields:
    def _make_profile(
        self, *, max_macro_corrections: int = 0
    ) -> AdaptiveToolLoopProfile:
        return AdaptiveToolLoopProfile(
            profile_name="p",
            mode_name="m",
            allowed_tools=frozenset({"file.read"}),
            max_macro_corrections=max_macro_corrections,
        )

    def _make_state(self, scratchpad: dict | None = None) -> AdaptiveToolLoopState:
        return AdaptiveToolLoopState(scratchpad=scratchpad or {})

    def test_no_corrections_all_zero(self):
        profile = self._make_profile()
        state = self._make_state()
        payload = adaptive_status_payload(profile=profile, loop_state=state)
        assert payload["loop.micro_corrections"] == 0
        assert payload["loop.macro_corrections"] == 0
        assert payload["loop.correction_types"] == {}
        assert payload["loop.correction_budget_remaining"] == 0
        assert payload["loop.correction_history_length"] == 0

    def test_micro_correction_reflected_in_payload(self):
        profile = self._make_profile()
        state = self._make_state({"micro_correction_count": 2})
        payload = adaptive_status_payload(profile=profile, loop_state=state)
        assert payload["loop.micro_corrections"] == 2

    def test_macro_correction_reflected_in_payload(self):
        profile = self._make_profile(max_macro_corrections=3)
        state = self._make_state(
            {
                "macro_correction_count": 1,
                "max_macro_corrections": 3,
                "correction_history": [
                    {
                        "correction_type": "replan",
                        "iteration_index": 0,
                        "applied": True,
                    }
                ],
            }
        )
        payload = adaptive_status_payload(profile=profile, loop_state=state)
        assert payload["loop.macro_corrections"] == 1
        assert payload["loop.correction_types"] == {"replan": 1}
        assert payload["loop.correction_budget_remaining"] == 2

    def test_existing_telemetry_fields_unchanged(self):
        profile = self._make_profile()
        state = self._make_state(
            {
                "reflection_calls": 1,
                "reflection_triggers": [{"iteration": 1}],
                "resumed_from_snapshot": True,
                "resume_iteration_index": 3,
                "loop.parallel_fan_out_count": 2,
                "loop.tool_calls_parallel": 4,
                "loop.tool_calls_sequential": 1,
            }
        )
        payload = adaptive_status_payload(profile=profile, loop_state=state)
        # Reflection
        assert payload["loop.reflection_calls"] == 1
        assert len(payload["loop.reflection_triggers"]) == 1
        # Resume
        assert payload["loop.resumed_from_snapshot"] is True
        assert payload["loop.resume_iteration_index"] == 3
        # Parallel
        assert payload["loop.parallel_fan_out_count"] == 2
        assert payload["loop.tool_calls_parallel"] == 4
        assert payload["loop.tool_calls_sequential"] == 1


# Integration tests: engine writes max_macro_corrections to scratchpad


class TestEngineStoresmaxMacroCorrectionsInScratchpad:
    def test_max_macro_corrections_stored_at_loop_start(self):
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
            profile=_base_profile(max_macro_corrections=5),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="go")],
            tool_specs=_tool_specs("file.read"),
        )
        assert outcome.state.scratchpad.get("max_macro_corrections") == 5

    def test_no_corrections_telemetry_emitted_as_zeros(self):
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
            profile=_base_profile(max_macro_corrections=2),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="go")],
            tool_specs=_tool_specs("file.read"),
        )
        assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        # Status events must include correction fields
        all_payloads = [
            s.get("payload", {}) for s in loop_ctx.statuses if s.get("payload")
        ]
        correction_payloads = [p for p in all_payloads if "loop.micro_corrections" in p]
        assert len(correction_payloads) > 0, "No status payload had correction fields"
        for p in correction_payloads:
            assert p["loop.micro_corrections"] == 0
            assert p["loop.macro_corrections"] == 0
            assert p["loop.correction_types"] == {}

    def test_micro_correction_count_incremented_on_error_result(self):
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
            profile=_base_profile(max_macro_corrections=0),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="go")],
            tool_specs=_tool_specs("file.read"),
        )
        assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert outcome.state.scratchpad.get("micro_correction_count", 0) >= 1
        # Verify it's reflected in at least one status event
        all_payloads = [
            s.get("payload", {}) for s in loop_ctx.statuses if s.get("payload")
        ]
        micro_vals = [
            p["loop.micro_corrections"]
            for p in all_payloads
            if "loop.micro_corrections" in p
        ]
        assert any(v >= 1 for v in micro_vals), (
            f"Expected at least one status with loop.micro_corrections>=1, got {micro_vals}"
        )

    def test_correction_budget_remaining_computed_correctly(self):
        scratchpad = {
            "max_macro_corrections": 4,
            "macro_correction_count": 1,
        }
        result = loop_correction_payload(scratchpad)
        assert result["loop.correction_budget_remaining"] == 3

    def test_zero_budget_profile_has_zero_budget_remaining(self):
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
        run_adaptive_tool_loop(
            loop_ctx,
            profile=_base_profile(max_macro_corrections=0),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="go")],
            tool_specs=_tool_specs("file.read"),
        )
        all_payloads = [
            s.get("payload", {}) for s in loop_ctx.statuses if s.get("payload")
        ]
        budget_vals = [
            p["loop.correction_budget_remaining"]
            for p in all_payloads
            if "loop.correction_budget_remaining" in p
        ]
        assert all(v == 0 for v in budget_vals), (
            f"Expected all budget_remaining=0, got {budget_vals}"
        )
