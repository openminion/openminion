from __future__ import annotations

from openminion.cli.status.activity_ledger import (
    KIND_PLAN,
    activity_from_progress_payload,
)


_FULL_PLAN_PAYLOAD = {
    "kind": "task_plan",
    "plan": {
        "summary": "Smoke",
        "items": [
            {"text": "lint", "status": "done"},
            {"text": "test", "status": "in_progress"},
            {"text": "ship", "status": "todo"},
        ],
    },
}

_STEP_COMPLETED_PAYLOAD = {
    "kind": "task_plan_step_completed",
    "step_text": "lint",
}

_STEP_BLOCKED_PAYLOAD = {
    "kind": "task_plan_step_blocked",
    "step_text": "ship",
    "reason": "missing token",
}


def test_task_plan_payload_routes_to_plan_kind() -> None:
    event = activity_from_progress_payload(_FULL_PLAN_PAYLOAD)
    assert event is not None and event.kind == KIND_PLAN
    assert event.plan["summary"] == "Smoke"


def test_task_plan_step_completed_routes_to_plan_kind() -> None:
    event = activity_from_progress_payload(_STEP_COMPLETED_PAYLOAD)
    assert event is not None and event.kind == KIND_PLAN


def test_runtime_emission_of_task_plan_events_is_documented_followup() -> None:
    assert activity_from_progress_payload(_FULL_PLAN_PAYLOAD) is not None
