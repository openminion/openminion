from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from openminion.modules.brain.schemas import BudgetCounters, WorkingState
from openminion.modules.brain.loop.tools.contracts import (
    ADAPTIVE_TERM_FINAL_TEXT,
    ADAPTIVE_TERM_NEEDS_USER,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
)
from openminion.modules.brain.loop.tools.correction import (
    CorrectionPlan,
    dispatch_correction_plan,
    trigger_macro_correction,
)
from openminion.modules.llm.schemas import Message


def _profile(
    *,
    max_macro_corrections: int = 3,
    macro_correction_cooldown: int = 2,
    reflection_model: str | None = None,
) -> AdaptiveToolLoopProfile:
    return AdaptiveToolLoopProfile(
        profile_name="test_profile",
        mode_name="test_mode",
        allowed_tools=frozenset({"tool.a"}),
        max_iterations=10,
        max_macro_corrections=max_macro_corrections,
        macro_correction_cooldown=macro_correction_cooldown,
        reflection_model=reflection_model,
    )


def _state(
    *, iteration: int = 5, scratchpad: dict | None = None
) -> AdaptiveToolLoopState:
    s = AdaptiveToolLoopState()
    s.iteration = iteration
    if scratchpad:
        s.scratchpad.update(scratchpad)
    return s


@dataclass
class _FakeRuntime:
    response_content: str = "{}"
    input_tokens: int = 0
    output_tokens: int = 0
    calls: list[dict] = field(default_factory=list)

    def complete(self, *, messages, tools, model, **kwargs):
        from types import SimpleNamespace

        self.calls.append({"messages": messages, "tools": tools, "model": model})
        return SimpleNamespace(
            content=self.response_content,
            usage=SimpleNamespace(
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
            ),
        )


def _loop_ctx(
    *, tokens: int = 50_000, llm_calls_used: int = 0, llm_calls_max: int = 10
) -> SimpleNamespace:
    return SimpleNamespace(
        state=WorkingState(
            session_id="correction-test",
            agent_id="correction-test",
            budgets_remaining=BudgetCounters(
                ticks=10,
                tool_calls=10,
                a2a_calls=0,
                tokens=tokens,
                time_ms=120_000,
            ),
            llm_calls_used=llm_calls_used,
            llm_calls_max=llm_calls_max,
        )
    )


def _valid_plan_json(**overrides: Any) -> str:
    base = {
        "diagnosis": "Tool returned error",
        "correction_type": "retry_same",
        "confidence": 0.8,
    }
    base.update(overrides)
    return json.dumps(base)


class TestTriggerMacroCorrection:
    def test_budget_available_triggers_correction(self):
        profile = _profile(max_macro_corrections=3, macro_correction_cooldown=1)
        loop_state = _state(iteration=5)
        runtime = _FakeRuntime(response_content=_valid_plan_json())

        plan = trigger_macro_correction(
            loop_ctx=_loop_ctx(),
            profile=profile,
            loop_state=loop_state,
            failure_context="tool failed",
            model="test-model",
            runtime=runtime,
            messages=[],
        )

        assert plan is not None
        assert isinstance(plan, CorrectionPlan)
        assert plan.correction_type == "retry_same"
        assert loop_state.scratchpad["macro_correction_count"] == 1
        assert loop_state.scratchpad["last_macro_iteration"] == 5
        assert loop_state.scratchpad["reflection_attempted_calls"] == 1
        assert loop_state.scratchpad["reflection_suggestions"] == 1
        assert loop_state.llm_calls == 1

    def test_budget_exhausted_returns_none(self):
        profile = _profile(max_macro_corrections=0)
        loop_state = _state(iteration=3)
        runtime = _FakeRuntime()

        plan = trigger_macro_correction(
            loop_ctx=_loop_ctx(),
            profile=profile,
            loop_state=loop_state,
            failure_context="failed",
            model="test-model",
            runtime=runtime,
            messages=[],
        )

        assert plan is None
        assert len(runtime.calls) == 0

    def test_cooldown_prevents_immediate_retrigger(self):
        profile = _profile(max_macro_corrections=5, macro_correction_cooldown=3)
        loop_state = _state(
            iteration=5,
            scratchpad={"last_macro_iteration": 5, "macro_correction_count": 1},
        )
        runtime = _FakeRuntime()

        plan = trigger_macro_correction(
            loop_ctx=_loop_ctx(),
            profile=profile,
            loop_state=loop_state,
            failure_context="failed",
            model="test-model",
            runtime=runtime,
            messages=[],
        )

        assert plan is None
        assert len(runtime.calls) == 0

    def test_parse_failure_returns_none(self):
        profile = _profile(max_macro_corrections=3, macro_correction_cooldown=1)
        loop_state = _state(iteration=5)
        runtime = _FakeRuntime(response_content="THIS IS NOT JSON {{{")

        plan = trigger_macro_correction(
            loop_ctx=_loop_ctx(),
            profile=profile,
            loop_state=loop_state,
            failure_context="tool failed",
            model="test-model",
            runtime=runtime,
            messages=[],
        )

        assert plan is None
        assert loop_state.scratchpad["reflection_attempted_calls"] == 1
        assert loop_state.scratchpad["reflection_failed_calls"] == 1
        assert loop_state.llm_calls == 1

    def test_uses_reflection_model_when_set(self):
        profile = _profile(
            max_macro_corrections=3,
            macro_correction_cooldown=1,
            reflection_model="reflect-model",
        )
        loop_state = _state(iteration=2)
        runtime = _FakeRuntime(response_content=_valid_plan_json())

        trigger_macro_correction(
            loop_ctx=_loop_ctx(),
            profile=profile,
            loop_state=loop_state,
            failure_context="failed",
            model="base-model",
            runtime=runtime,
            messages=[],
        )

        assert len(runtime.calls) == 1
        assert runtime.calls[0]["model"] == "reflect-model"

    def test_increments_macro_correction_count(self):
        profile = _profile(max_macro_corrections=5, macro_correction_cooldown=1)
        loop_state = _state(
            iteration=10,
            scratchpad={"macro_correction_count": 2, "last_macro_iteration": 0},
        )
        runtime = _FakeRuntime(response_content=_valid_plan_json())

        trigger_macro_correction(
            loop_ctx=_loop_ctx(),
            profile=profile,
            loop_state=loop_state,
            failure_context="failed",
            model="test-model",
            runtime=runtime,
            messages=[],
        )

        assert loop_state.scratchpad["macro_correction_count"] == 3

    def test_cooldown_respected_at_boundary(self):
        profile = _profile(max_macro_corrections=5, macro_correction_cooldown=3)
        loop_state = _state(
            iteration=6,
            scratchpad={"last_macro_iteration": 3, "macro_correction_count": 1},
        )
        runtime = _FakeRuntime(response_content=_valid_plan_json())

        plan = trigger_macro_correction(
            loop_ctx=_loop_ctx(),
            profile=profile,
            loop_state=loop_state,
            failure_context="failed",
            model="test-model",
            runtime=runtime,
            messages=[],
        )

        assert plan is not None

    def test_budget_count_at_exact_max_returns_none(self):
        profile = _profile(max_macro_corrections=2)
        loop_state = _state(
            iteration=5,
            scratchpad={"macro_correction_count": 2},
        )
        runtime = _FakeRuntime()

        plan = trigger_macro_correction(
            loop_ctx=_loop_ctx(),
            profile=profile,
            loop_state=loop_state,
            failure_context="failed",
            model="m",
            runtime=runtime,
            messages=[],
        )

        assert plan is None

    def test_global_call_cap_prevents_dispatch(self):
        profile = _profile(max_macro_corrections=2)
        loop_state = _state()
        runtime = _FakeRuntime(response_content=_valid_plan_json())

        plan = trigger_macro_correction(
            loop_ctx=_loop_ctx(llm_calls_used=2, llm_calls_max=2),
            profile=profile,
            loop_state=loop_state,
            failure_context="failed",
            model="m",
            runtime=runtime,
            messages=[],
        )

        assert plan is None
        assert runtime.calls == []
        assert loop_state.scratchpad.get("macro_correction_count", 0) == 0

    def test_profile_call_cap_prevents_dispatch(self):
        profile = _profile(max_macro_corrections=2)
        profile = replace(profile, max_llm_calls_per_loop=1)
        loop_state = _state()
        loop_state.llm_calls = 1
        runtime = _FakeRuntime(response_content=_valid_plan_json())

        plan = trigger_macro_correction(
            loop_ctx=_loop_ctx(),
            profile=profile,
            loop_state=loop_state,
            failure_context="failed",
            model="m",
            runtime=runtime,
            messages=[],
        )

        assert plan is None
        assert runtime.calls == []

    def test_provider_failure_is_counted_once(self):
        class _FailingRuntime:
            def complete(self, **kwargs):
                raise RuntimeError("provider unavailable")

        loop_ctx = _loop_ctx()
        loop_state = _state()

        plan = trigger_macro_correction(
            loop_ctx=loop_ctx,
            profile=_profile(macro_correction_cooldown=1),
            loop_state=loop_state,
            failure_context="failed",
            model="m",
            runtime=_FailingRuntime(),
            messages=[],
        )

        assert plan is None
        assert loop_ctx.state.llm_calls_used == 1
        assert loop_state.llm_calls == 1
        assert loop_state.scratchpad["macro_correction_count"] == 1
        assert loop_state.scratchpad["reflection_failed_calls"] == 1

    def test_provider_reported_tokens_use_shared_budget(self):
        runtime = _FakeRuntime(
            response_content=_valid_plan_json(),
            input_tokens=120,
            output_tokens=30,
        )
        loop_ctx = _loop_ctx(tokens=1_000)

        trigger_macro_correction(
            loop_ctx=loop_ctx,
            profile=_profile(macro_correction_cooldown=1),
            loop_state=_state(),
            failure_context="failed",
            model="m",
            runtime=runtime,
            messages=[],
        )

        assert loop_ctx.state.budgets_remaining.tokens == 850

    def test_cancellation_after_dispatch_is_counted_and_propagated(self):
        class _CancelledRuntime:
            def complete(self, **kwargs):
                raise KeyboardInterrupt

        loop_ctx = _loop_ctx()
        loop_state = _state()

        with pytest.raises(KeyboardInterrupt):
            trigger_macro_correction(
                loop_ctx=loop_ctx,
                profile=_profile(macro_correction_cooldown=1),
                loop_state=loop_state,
                failure_context="failed",
                model="m",
                runtime=_CancelledRuntime(),
                messages=[],
            )

        assert loop_ctx.state.llm_calls_used == 1
        assert loop_state.llm_calls == 1
        assert loop_state.scratchpad["macro_correction_count"] == 1


class TestDispatchCorrectionPlan:
    def _make_state(self, iteration: int = 3) -> AdaptiveToolLoopState:
        s = AdaptiveToolLoopState()
        s.iteration = iteration
        return s

    def test_dispatch_retry_same(self):
        plan = CorrectionPlan(
            diagnosis="transient error",
            correction_type="retry_same",
            confidence=0.9,
        )
        loop_state = self._make_state()
        messages: list[Message] = []

        result = dispatch_correction_plan(
            plan=plan,
            loop_ctx=MagicMock(),
            loop_state=loop_state,
            messages=messages,
            profile=MagicMock(),
        )

        assert result is None
        assert len(messages) == 0

    def test_dispatch_retry_different(self):
        plan = CorrectionPlan(
            diagnosis="wrong path",
            correction_type="retry_different",
            corrected_args={"path": "/new/path"},
            confidence=0.8,
        )
        loop_state = self._make_state()
        messages: list[Message] = []

        result = dispatch_correction_plan(
            plan=plan,
            loop_ctx=MagicMock(),
            loop_state=loop_state,
            messages=messages,
            profile=MagicMock(),
        )

        assert result is None
        assert len(messages) == 1
        assert "Correction suggestion" in messages[0].content
        assert "/new/path" in messages[0].content

    def test_dispatch_replan(self):
        plan = CorrectionPlan(
            diagnosis="approach failed",
            correction_type="replan",
            replan_hint="use grep instead",
            confidence=0.7,
        )
        loop_state = self._make_state(iteration=5)
        messages: list[Message] = []

        result = dispatch_correction_plan(
            plan=plan,
            loop_ctx=MagicMock(),
            loop_state=loop_state,
            messages=messages,
            profile=MagicMock(),
        )

        assert result is None
        assert loop_state.iteration == 5
        assert len(messages) == 1
        assert "use grep instead" in messages[0].content

    def test_dispatch_ask_user(self):
        plan = CorrectionPlan(
            diagnosis="need more info",
            correction_type="ask_user",
            confidence=0.4,
        )
        loop_state = self._make_state()

        result = dispatch_correction_plan(
            plan=plan,
            loop_ctx=MagicMock(),
            loop_state=loop_state,
            messages=[],
            profile=MagicMock(),
        )

        assert result == ADAPTIVE_TERM_NEEDS_USER

    def test_dispatch_accept_partial(self):
        plan = CorrectionPlan(
            diagnosis="partial is good enough",
            correction_type="accept_partial",
            confidence=0.6,
        )
        loop_state = self._make_state()

        result = dispatch_correction_plan(
            plan=plan,
            loop_ctx=MagicMock(),
            loop_state=loop_state,
            messages=[],
            profile=MagicMock(),
        )

        assert result == ADAPTIVE_TERM_FINAL_TEXT

    def test_correction_history_recorded(self):
        plan = CorrectionPlan(
            diagnosis="Something went wrong during execution",
            correction_type="retry_same",
            confidence=0.8,
        )
        loop_state = self._make_state(iteration=7)

        dispatch_correction_plan(
            plan=plan,
            loop_ctx=MagicMock(),
            loop_state=loop_state,
            messages=[],
            profile=MagicMock(),
        )

        history = loop_state.scratchpad.get("correction_history", [])
        assert len(history) == 1
        record = history[0]
        assert record["correction_type"] == "retry_same"
        assert record["iteration_index"] == 7
        assert record["applied"] is False
        assert "Something went wrong" in record["diagnosis_summary"]

    def test_dispatch_records_history_on_multiple_calls(self):
        loop_state = self._make_state(iteration=1)

        for i in range(3):
            loop_state.iteration = i + 1
            plan = CorrectionPlan(
                diagnosis=f"error {i}",
                correction_type="retry_same",
                confidence=0.7,
            )
            dispatch_correction_plan(
                plan=plan,
                loop_ctx=MagicMock(),
                loop_state=loop_state,
                messages=[],
                profile=MagicMock(),
            )

        history = loop_state.scratchpad.get("correction_history", [])
        assert len(history) == 3
        assert history[2]["iteration_index"] == 3
