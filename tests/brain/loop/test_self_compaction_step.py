from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from openminion.modules.brain.loop.self_compaction import (
    SELF_COMPACTION_EVENT_TYPE,
    apply_self_compaction_result,
    run_self_compaction_step,
)
from openminion.modules.brain.schemas.state import BudgetCounters, WorkingState
from openminion.modules.context.compress.eligibility import (
    CompactionBudgetState,
    DefaultCompactionEligibility,
)
from openminion.modules.llm.schemas import LLMResponse


@dataclass
class _FakeRuntime:
    output_text: str
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete(
        self, *, messages, tools, model, tool_choice, max_output_tokens, metadata
    ):
        self.calls.append(
            {
                "messages": list(messages),
                "tools": list(tools),
                "model": model,
                "tool_choice": tool_choice,
                "max_output_tokens": max_output_tokens,
                "metadata": dict(metadata or {}),
            }
        )
        return LLMResponse(
            ok=True,
            provider="fake",
            model=model,
            output_text=self.output_text,
            finish_reason="stop",
        )


class _ContextService:
    def __init__(self) -> None:
        self._checker = DefaultCompactionEligibility()

    def evaluate_self_compaction_eligibility(
        self, *, working_state, prompt_token_estimate, budget_state, now
    ):
        return self._checker.is_eligible(
            working_state,
            prompt_token_estimate=prompt_token_estimate,
            budget_state=budget_state,
            now=now,
        )


@dataclass
class _SessionAPI:
    events: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def append_event(
        self,
        session_id,
        event_type,
        payload,
        **kwargs,
    ) -> None:
        del kwargs
        self.events.append((session_id, event_type, dict(payload)))


def _state() -> WorkingState:
    return WorkingState(
        session_id="session-1",
        agent_id="agent-1",
        goal="finish compaction",
        budgets_remaining=BudgetCounters(
            ticks=1,
            tool_calls=1,
            a2a_calls=0,
            tokens=100,
            time_ms=1000,
        ),
        session_work_summary="prior checkpoint",
    )


def test_run_self_compaction_step_uses_runtime_and_writes_summary() -> None:
    runtime = _FakeRuntime(
        output_text="Finished parser wiring. Next: land smoke tests."
    )
    state = _state()
    session_api = _SessionAPI()

    result = run_self_compaction_step(
        working_state=state,
        runtime=runtime,
        model="gpt-4.2-mini",
        context_service=_ContextService(),
        prompt_token_estimate=90,
        budget_state=CompactionBudgetState(max_prompt_tokens=100),
        session_api=session_api,
        now=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
        recent_work="Finished parser wiring and validation.",
    )

    assert result.applied is True
    assert (
        state.session_work_summary == "Finished parser wiring. Next: land smoke tests."
    )
    assert runtime.calls[0]["tools"] == []
    assert runtime.calls[0]["tool_choice"] == "none"
    assert session_api.events[0][1] == SELF_COMPACTION_EVENT_TYPE


def test_apply_self_compaction_result_preserves_consolidation_marker() -> None:
    state = _state()
    state.module_state = {
        "memory_context_maintenance": {
            "last_consolidation_marker": "2026-05-22T11:59:59+00:00",
        }
    }
    eligibility = _ContextService().evaluate_self_compaction_eligibility(
        working_state=state,
        prompt_token_estimate=90,
        budget_state=CompactionBudgetState(max_prompt_tokens=100),
        now=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
    )

    result = apply_self_compaction_result(
        state,
        eligibility=eligibility,
        summary_text="Fresh summary for future me.",
        now=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
    )

    maintenance = state.module_state["memory_context_maintenance"]
    assert result.applied is True
    assert maintenance["last_consolidation_marker"] == "2026-05-22T11:59:59+00:00"
    assert maintenance["last_compaction_marker"] == "2026-05-22T12:00:00+00:00"
    assert result.audit_payload["operation"] == "self_compaction"
