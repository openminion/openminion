from __future__ import annotations

from datetime import datetime, timezone

from openminion.modules.brain.schemas.state import BudgetCounters, WorkingState
from openminion.modules.context.compress.eligibility import (
    CompactionBudgetState,
    DefaultCompactionEligibility,
    REASON_ALREADY_COMPACTED_THIS_TURN,
    REASON_BELOW_THRESHOLD,
    REASON_CONSOLIDATION_NOT_YET_RUN,
    REASON_OK,
    compaction_state_hash,
)
from openminion.modules.memory.runtime.consolidation.coordinator import (
    MAINTENANCE_MODULE_STATE_KEY,
)


def _state() -> WorkingState:
    return WorkingState(
        session_id="session-1",
        agent_id="agent-1",
        goal="ship the compaction feature",
        budgets_remaining=BudgetCounters(
            ticks=1,
            tool_calls=1,
            a2a_calls=0,
            tokens=100,
            time_ms=1000,
        ),
    )


def test_compaction_eligibility_reports_below_threshold() -> None:
    checker = DefaultCompactionEligibility()

    result = checker.is_eligible(
        _state(),
        prompt_token_estimate=60,
        budget_state=CompactionBudgetState(max_prompt_tokens=100),
        now=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
    )

    assert result.is_eligible is False
    assert result.reason_code == REASON_BELOW_THRESHOLD


def test_compaction_eligibility_reports_ok() -> None:
    checker = DefaultCompactionEligibility()

    result = checker.is_eligible(
        _state(),
        prompt_token_estimate=90,
        budget_state=CompactionBudgetState(max_prompt_tokens=100),
        now=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
    )

    assert result.is_eligible is True
    assert result.reason_code == REASON_OK
    assert result.state_hash


def test_compaction_eligibility_reports_already_compacted_this_turn() -> None:
    checker = DefaultCompactionEligibility()
    state = _state()
    budget_state = CompactionBudgetState(max_prompt_tokens=100)
    state_hash = compaction_state_hash(
        state,
        prompt_token_estimate=90,
        budget_state=budget_state,
    )
    state.module_state = {
        MAINTENANCE_MODULE_STATE_KEY: {
            "last_compaction_state_hash": state_hash,
        }
    }

    result = checker.is_eligible(
        state,
        prompt_token_estimate=90,
        budget_state=budget_state,
        now=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
    )

    assert result.is_eligible is False
    assert result.reason_code == REASON_ALREADY_COMPACTED_THIS_TURN


def test_compaction_eligibility_reports_consolidation_not_yet_run() -> None:
    checker = DefaultCompactionEligibility()

    result = checker.is_eligible(
        _state(),
        prompt_token_estimate=90,
        budget_state=CompactionBudgetState(
            max_prompt_tokens=100,
            consolidation_eligible=True,
            consolidation_completed=False,
        ),
        now=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
    )

    assert result.is_eligible is False
    assert result.reason_code == REASON_CONSOLIDATION_NOT_YET_RUN
