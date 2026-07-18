from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.loop.tools.plan import (
    _append_task_plan_event,
    _emit_task_plan_progress_event,
)


class _FakeRunner:
    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self._progress_callback = self.received.append
        self.session_api = None


def _ctx_for(runner: _FakeRunner) -> SimpleNamespace:
    return SimpleNamespace(
        _runner=runner,
        state=SimpleNamespace(session_id="sess", agent_id="agent", trace_id="trace"),
        session_api=None,
    )


def _emit_progress(
    event_type: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    runner = _FakeRunner()
    _emit_task_plan_progress_event(
        _ctx_for(runner),
        event_type=event_type,
        payload=payload,
    )
    return runner.received


def test_emit_task_plan_declared_routes_to_task_plan_kind() -> None:
    plan_dump = {
        "plan_id": "p1",
        "objective": "Smoke",
        "steps": [{"step_id": "s1", "description": "lint"}],
    }
    payload = _emit_progress("task_plan.declared", {"plan": plan_dump})[0]
    assert payload["kind"] == "task_plan"
    assert payload["plan"] == plan_dump


def test_emit_task_plan_step_completed_translates_step_id_to_step_text() -> None:
    payload = _emit_progress(
        "task_plan.step_completed",
        {
            "plan_id": "p1",
            "step_id": "ship",
            "output_summary": "shipped to staging",
        },
    )[0]
    assert payload["kind"] == "task_plan_step_completed"
    assert payload["step_text"] == "ship"
    assert payload["note"] == "shipped to staging"


def test_emit_task_plan_step_blocked_translates_blocker_details_to_reason() -> None:
    payload = _emit_progress(
        "task_plan.step_blocked",
        {
            "plan_id": "p1",
            "step_id": "deploy",
            "blocker_type": "missing_credential",
            "blocker_details": {"detail": "no DEPLOY_TOKEN env"},
        },
    )[0]
    assert payload["kind"] == "task_plan_step_blocked"
    assert payload["step_text"] == "deploy"
    assert payload["reason"] == "missing_credential: no DEPLOY_TOKEN env"


def test_emit_task_plan_step_blocked_blocker_type_only_still_renders() -> None:
    payload = _emit_progress(
        "task_plan.step_blocked",
        {
            "plan_id": "p1",
            "step_id": "deploy",
            "blocker_type": "user_input_required",
        },
    )[0]
    assert payload["reason"] == "user_input_required"


def test_emit_task_plan_revised_translates_to_revision_kind() -> None:
    payload = _emit_progress(
        "task_plan.revised",
        {"plan_id": "p1", "added_step_ids": ["new_step"]},
    )[0]
    assert payload["kind"] == "task_plan_revision"
    assert payload["plan_id"] == "p1"


def test_emit_task_plan_completed_translates_to_completed_kind() -> None:
    payload = _emit_progress(
        "task_plan.completed",
        {"plan_id": "p1", "plan": {"summary": "Done"}},
    )[0]
    assert payload["kind"] == "task_plan_completed"
    assert payload["plan"] == {"summary": "Done"}


def test_emit_task_plan_abandoned_routes_to_completed_kind() -> None:
    payload = _emit_progress(
        "task_plan.abandoned",
        {"plan_id": "p1", "reason": "replaced_by_new_task_plan"},
    )[0]
    assert payload["kind"] == "task_plan_completed"


def test_unknown_event_type_drops_silently() -> None:
    assert _emit_progress("task_plan.unrelated", {"plan_id": "p1"}) == []


def test_no_runner_drops_silently() -> None:
    ctx = SimpleNamespace(_runner=None, state=SimpleNamespace(), session_api=None)
    _emit_task_plan_progress_event(
        ctx,
        event_type="task_plan.step_completed",
        payload={"plan_id": "p1", "step_id": "s1"},
    )


def test_no_progress_callback_drops_silently() -> None:
    runner = SimpleNamespace(_progress_callback=None, session_api=None)
    ctx = SimpleNamespace(_runner=runner, state=SimpleNamespace())
    _emit_task_plan_progress_event(
        ctx,
        event_type="task_plan.declared",
        payload={"plan": {}},
    )


def test_callback_exception_is_swallowed() -> None:
    def _crashy(_payload: Any) -> None:
        raise RuntimeError("boom")

    runner = SimpleNamespace(_progress_callback=_crashy, session_api=None)
    ctx = SimpleNamespace(_runner=runner, state=SimpleNamespace())
    _emit_task_plan_progress_event(
        ctx,
        event_type="task_plan.declared",
        payload={"plan": {}},
    )


def test_append_task_plan_event_fires_progress_bridge() -> None:
    runner = _FakeRunner()
    ctx = _ctx_for(runner)
    _append_task_plan_event(
        ctx,
        event_type="task_plan.step_completed",
        payload={"plan_id": "p1", "step_id": "ship"},
    )
    assert len(runner.received) == 1
    assert runner.received[0]["kind"] == "task_plan_step_completed"
    assert runner.received[0]["step_text"] == "ship"


def test_bridge_payload_renders_via_tal_activity_ledger() -> None:
    from openminion.cli.status.activity_ledger import (
        KIND_PLAN,
        STATE_COMPLETED,
        activity_from_progress_payload,
        format_activity_line,
    )

    payload = _emit_progress(
        "task_plan.step_completed",
        {
            "plan_id": "p1",
            "step_id": "ship",
            "output_summary": "deployed",
        },
    )[0]
    event = activity_from_progress_payload(payload)
    assert event is not None
    assert event.kind == KIND_PLAN
    assert event.state == STATE_COMPLETED
    line = format_activity_line(event)
    assert line == "Plan step done: ship"
