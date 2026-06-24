from __future__ import annotations

from openminion.cli.status.activity_ledger import (
    KIND_APPROVAL,
    KIND_BACKGROUND,
    KIND_BUDGET,
    KIND_ERROR,
    KIND_PLAN,
    KIND_TOOL,
    activity_from_progress_payload,
)


# Payload families confirmed to reach the runtime progress callback
# today. Each entry is (payload, expected_kind, expected_state).
_DELIVERED_PAYLOADS = [
    (
        {
            "kind": "tool_started",
            "tool_name": "bash",
            "args": {"command": "ls"},
            "call_id": "c1",
        },
        KIND_TOOL,
        "running",
    ),
    (
        {
            "kind": "tool_completed",
            "tool_name": "bash",
            "args": {"command": "ls"},
            "call_id": "c1",
            "ok": True,
            "duration_ms": 12,
        },
        KIND_TOOL,
        "completed",
    ),
    (
        {"kind": "budget_event", "event_type": "budget.exhausted"},
        KIND_BUDGET,
        "summary",
    ),
    (
        {"kind": "budget_event", "event_type": "budget.noop_guard"},
        KIND_BUDGET,
        "summary",
    ),
    (
        {"kind": "budget_event", "event_type": "budget.user_declined"},
        KIND_BUDGET,
        "summary",
    ),
    (
        {"kind": "budget_event", "event_type": "budget.user_timeout"},
        KIND_BUDGET,
        "summary",
    ),
]


# Payload families the adapter handles speculatively. Runtime does not
# emit these to the interactive progress callback today; the adapter is
# the ledger model.
_SPECULATIVE_PAYLOADS = [
    (
        {
            "kind": "task_plan",
            "plan": {"summary": "x", "items": []},
        },
        KIND_PLAN,
    ),
    ({"kind": "task_plan_completed", "plan": {"summary": "x"}}, KIND_PLAN),
    ({"kind": "task_plan_step_completed", "step_text": "ship"}, KIND_PLAN),
    ({"kind": "task_plan_step_blocked", "step_text": "deploy"}, KIND_PLAN),
    ({"kind": "approval_request", "tool_name": "git.reset"}, KIND_APPROVAL),
    (
        {
            "kind": "approval_decision",
            "tool_name": "git.reset",
            "decision": "denied",
        },
        KIND_APPROVAL,
    ),
    ({"kind": "background_started", "title": "research"}, KIND_BACKGROUND),
    (
        {"kind": "background_completed", "title": "research", "duration_ms": 100},
        KIND_BACKGROUND,
    ),
    ({"kind": "error", "title": "RuntimeError", "message": "boom"}, KIND_ERROR),
]


def test_delivered_payload_families_map_deterministically() -> None:
    for payload, expected_kind, expected_state in _DELIVERED_PAYLOADS:
        event = activity_from_progress_payload(payload)
        assert event is not None, f"adapter dropped delivered payload: {payload}"
        assert event.kind == expected_kind, (
            f"{payload['kind']} expected {expected_kind} got {event.kind}"
        )
        assert event.state == expected_state, (
            f"{payload['kind']} expected state {expected_state} got {event.state}"
        )


def test_speculative_payload_families_map_default_safely() -> None:
    for payload, expected_kind in _SPECULATIVE_PAYLOADS:
        event = activity_from_progress_payload(payload)
        assert event is not None, f"adapter dropped speculative payload: {payload}"
        assert event.kind == expected_kind, (
            f"{payload['kind']} expected {expected_kind} got {event.kind}"
        )


def test_phase_status_model_dump_falls_through_to_status_event() -> None:
    phase_status_dump = {
        "trace_id": "t1",
        "status_key": "executing",
        "label": "Running tool",
        "detail_text": "bash",
        "terminal": False,
    }
    event = activity_from_progress_payload(phase_status_dump)
    assert event is not None
    assert event.kind == "status"
    assert event.title in {"Running tool", "executing"}
