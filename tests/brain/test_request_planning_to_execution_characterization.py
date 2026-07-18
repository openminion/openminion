from __future__ import annotations

from openminion.modules.brain.schemas import ActDecision, RequestReadiness
from openminion.modules.brain.execution.intent_state import record_decision_metadata
from openminion.modules.brain.schemas import BudgetCounters, WorkingState


def _state() -> WorkingState:
    return WorkingState(
        session_id="s-hlpe-characterization",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=4, tool_calls=2, a2a_calls=0, tokens=2000, time_ms=10000
        ),
    )


def test_ready_execute_decision_copies_readiness_to_working_state() -> None:
    state = _state()
    decision = ActDecision(
        request_readiness=RequestReadiness(
            posture="direct",
            requested_outcome="execute",
            state="ready",
        )
    )

    record_decision_metadata(state=state, decision=decision, plan=None)

    assert state.request_readiness == decision.request_readiness


def test_missing_payload_keeps_legacy_working_state_empty() -> None:
    state = _state()

    record_decision_metadata(state=state, decision=ActDecision(), plan=None)

    assert state.request_readiness is None

