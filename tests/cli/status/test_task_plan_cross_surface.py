from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from openminion.cli.status.activity_ledger import (
    KIND_PLAN,
    activity_from_progress_payload,
)
from openminion.cli.interactive.screen import FocusScreen
from openminion.cli.presentation.models import MessageKind


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


def test_focus_rich_pushes_durable_plan_row() -> None:
    screen = object.__new__(FocusScreen)
    pushed: list = []
    transcript_mock = SimpleNamespace(
        push_message=lambda msg: pushed.append(msg) or msg,
    )
    screen.query_one = MagicMock(return_value=transcript_mock)  # type: ignore[attr-defined]
    result = screen._push_durable_activity_row(_FULL_PLAN_PAYLOAD)
    assert result is True
    assert len(pushed) == 1
    msg = pushed[0]
    assert msg.kind == MessageKind.SYSTEM
    assert "Plan" in msg.body
    assert "[x] lint" in msg.body


def test_runtime_emission_of_task_plan_events_is_documented_followup() -> None:
    assert activity_from_progress_payload(_FULL_PLAN_PAYLOAD) is not None
