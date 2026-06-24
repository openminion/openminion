from __future__ import annotations

from openminion.modules.brain.schemas.state import BudgetCounters, WorkingState
from openminion.modules.memory.runtime.consolidation.coordinator import (
    MAINTENANCE_MODULE_STATE_KEY,
    apply_consolidation_marker,
)


def _state() -> WorkingState:
    return WorkingState(
        session_id="session-1",
        agent_id="agent-1",
        budgets_remaining=BudgetCounters(
            ticks=1, tool_calls=1, a2a_calls=0, tokens=0, time_ms=0
        ),
        session_work_summary="preserve me",
    )


def test_apply_consolidation_marker_writes_shared_seam_without_touching_summary() -> (
    None
):
    state = _state()

    result = apply_consolidation_marker(
        state,
        session_id="session-1",
        turn_id="turn-1",
        marker="marker-1",
        state_hash="hash-1",
        input_ref="extract-1",
        output_ref="merge-1",
        reason="phase2 complete",
    )

    assert result.applied is True
    assert result.reason_code == "OK"
    assert state.session_work_summary == "preserve me"
    assert (
        state.module_state[MAINTENANCE_MODULE_STATE_KEY]["last_consolidation_marker"]
        == "marker-1"
    )
    assert (
        state.module_state[MAINTENANCE_MODULE_STATE_KEY][
            "last_consolidation_state_hash"
        ]
        == "hash-1"
    )
    assert result.audit_payload["operation"] == "memory_consolidation"
    assert result.audit_payload["marker"] == "marker-1"
    assert result.audit_payload["state_hash"] == "hash-1"


def test_apply_consolidation_marker_is_idempotent_for_same_state_hash() -> None:
    state = _state()
    first = apply_consolidation_marker(
        state,
        session_id="session-1",
        turn_id="turn-1",
        marker="marker-1",
        state_hash="hash-1",
    )
    second = apply_consolidation_marker(
        state,
        session_id="session-1",
        turn_id="turn-2",
        marker="marker-2",
        state_hash="hash-1",
    )

    assert first.applied is True
    assert second.applied is False
    assert second.reason_code == "ALREADY_CONSOLIDATED"
    assert (
        state.module_state[MAINTENANCE_MODULE_STATE_KEY]["last_consolidation_marker"]
        == "marker-1"
    )
    assert (
        state.module_state[MAINTENANCE_MODULE_STATE_KEY][
            "last_consolidation_state_hash"
        ]
        == "hash-1"
    )
    assert state.session_work_summary == "preserve me"
