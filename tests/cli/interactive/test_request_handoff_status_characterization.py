from __future__ import annotations

from openminion.modules.brain.diagnostics.status import (
    format_phase_status_text,
    phase_status_from_request_readiness,
)
from openminion.modules.brain.schemas import RequestReadiness


def test_request_readiness_plan_review_status_is_terminal_safe() -> None:
    status = phase_status_from_request_readiness(
        trace_id="trace-hlpe",
        readiness=RequestReadiness(
            posture="review_before_act",
            requested_outcome="execute",
            state="needs_plan_review",
        ),
    )

    assert status.status_key == "awaiting_plan_review"
    assert "plan review" in format_phase_status_text(status).lower()


def test_request_readiness_operation_approval_status_is_distinct() -> None:
    status = phase_status_from_request_readiness(
        trace_id="trace-hlpe",
        readiness=RequestReadiness(
            posture="direct",
            requested_outcome="execute",
            state="needs_operation_approval",
        ),
    )

    assert status.status_key == "awaiting_confirmation"
    assert "confirmation" in format_phase_status_text(status).lower()

