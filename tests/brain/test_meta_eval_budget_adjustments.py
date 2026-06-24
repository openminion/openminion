from __future__ import annotations

from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.brain.meta import apply_meta_directive
from openminion.modules.brain.schemas import (
    BudgetCounters,
    WorkingState,
)
from openminion.modules.brain.meta.schemas import (
    BudgetAdjust as CanonicalBudgetAdjust,
    MetaDirective as CanonicalMetaDirective,
)


class LegacyBudgetAdjust(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raise_context_limits: bool = False
    lower_context_limits: bool = False
    raise_llm_calls_max: int | None = None
    lower_llm_calls_max: int | None = None


class LegacyMetaDirective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier_override: str | None = None
    prompt_constraints: list[str] = Field(default_factory=list)
    budget_adjust: LegacyBudgetAdjust | None = None


class _CaptureLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, payload: dict, trace_id: str | None = None) -> None:
        _ = trace_id
        self.events.append((event_type, payload))


def _state() -> WorkingState:
    return WorkingState(
        session_id="meta-budget",
        agent_id="agent",
        trace_id="trace-budget",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=10,
            a2a_calls=5,
            tokens=900,
            time_ms=10000,
        ),
        llm_calls_max=8,
    )


def _runner() -> SimpleNamespace:
    return SimpleNamespace(
        profile=SimpleNamespace(budgets=SimpleNamespace(max_total_llm_tokens=1000)),
        _last_meta_application=None,
    )


def _apply_directive(directive) -> tuple[SimpleNamespace, WorkingState]:
    runner = _runner()
    state = _state()
    apply_meta_directive(
        runner,
        state=state,
        directive=directive,
        logger=_CaptureLogger(),
        hook="before_act",
        meta_state="CAUTIOUS",
    )
    return runner, state


def test_apply_meta_directive_prefers_budget_adjustments() -> None:
    runner, state = _apply_directive(
        CanonicalMetaDirective(
            budget_adjustments=CanonicalBudgetAdjust(
                lower_context_limits=True,
                lower_llm_calls_max=2,
            )
        )
    )

    assert state.budgets_remaining.tokens <= 800
    assert state.llm_calls_max == 2
    assert runner._last_meta_application is not None
    assert runner._last_meta_application.budgets_adjusted


def test_apply_meta_directive_legacy_budget_adjust_fallback() -> None:
    runner, state = _apply_directive(
        LegacyMetaDirective(
            budget_adjust=LegacyBudgetAdjust(
                lower_context_limits=True,
                lower_llm_calls_max=3,
            )
        )
    )

    assert state.budgets_remaining.tokens <= 800
    assert state.llm_calls_max == 3
    assert runner._last_meta_application is not None
    assert runner._last_meta_application.budgets_adjusted
