from __future__ import annotations

from openminion.modules.brain.diagnostics.status import (
    format_phase_status_text,
    phase_status_from_request_readiness,
)
from openminion.modules.brain.schemas import RequestReadiness


def test_hlpe_focus_status_matrix_is_safe_without_live_provider() -> None:
    scenarios = [
        (
            "direct_execute",
            RequestReadiness(
                posture="direct",
                requested_outcome="execute",
                state="ready",
            ),
            "executing",
        ),
        (
            "plan_review",
            RequestReadiness(
                posture="review_before_act",
                requested_outcome="execute",
                state="needs_plan_review",
            ),
            "awaiting_plan_review",
        ),
        (
            "operation_approval",
            RequestReadiness(
                posture="direct",
                requested_outcome="execute",
                state="needs_operation_approval",
            ),
            "awaiting_confirmation",
        ),
        (
            "blocked",
            RequestReadiness(
                posture="brief_plan",
                requested_outcome="execute",
                state="blocked",
            ),
            "blocked",
        ),
    ]

    rendered = {}
    for name, readiness, expected_status in scenarios:
        status = phase_status_from_request_readiness(
            trace_id=f"trace-{name}",
            readiness=readiness,
        )
        rendered[name] = format_phase_status_text(status)
        assert status.status_key == expected_status

    assert "Executing" in rendered["direct_execute"]
    assert "plan review" in rendered["plan_review"].lower()
    assert "confirmation" in rendered["operation_approval"].lower()
    assert "Blocked" in rendered["blocked"]

