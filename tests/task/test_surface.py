from __future__ import annotations

from openminion.modules.task import InMemoryTaskCtl, TaskCreateInput
from openminion.modules.task.runtime.lifecycle import TaskManager
from openminion.modules.task.surface import build_task_surface


def test_task_surface_lists_digest_tasks_and_pending_actions() -> None:
    ctl = InMemoryTaskCtl()
    task = ctl.create_task(TaskCreateInput(task_id="t1", title="Write docs"))
    ctl.record_pending_action(
        policy_request_id="pr1",
        cursor=_cursor(task.task_id),
        reason="approval needed",
    )

    payload = build_task_surface(ctl, agent_id="agent", session_id="s1").inventory()

    assert payload["ok"] is True
    assert payload["source"] == "task_ctl"
    assert payload["tasks"][0]["id"] == "t1"
    assert payload["tasks"][0]["operator_state"] == "waiting"
    assert payload["tasks"][0]["resume_action"] == "approve"
    assert payload["tasks"][0]["pending_actions"][0]["decision_id"] == "pr1"
    assert payload["pending_actions"][0]["reason"] == "approval needed"


def test_task_surface_resolves_pending_action() -> None:
    ctl = InMemoryTaskCtl()
    ctl.create_task(TaskCreateInput(task_id="t1", title="Run command"))
    ctl.record_pending_action(
        policy_request_id="pr1",
        cursor=_cursor("t1"),
        reason="approval needed",
    )

    result = build_task_surface(ctl, session_id="s1").apply_action(
        task_id="", action="allow", decision_id="pr1"
    )

    assert result["ok"] is True
    assert build_task_surface(ctl).list_pending_actions() == []


def test_task_surface_lists_and_controls_lifecycle_tasks() -> None:
    manager = TaskManager.for_lifecycle_db(db_path=":memory:")
    manager.create_task(
        session_id="s1",
        mode_name="research",
        goal="finish long task",
        agent_id="agent",
        task_id="lt1",
        metadata={"trace_id": "trace-1"},
    )
    surface = build_task_surface(manager)

    shown = surface.show_task("lt1")
    assert shown is not None
    assert shown["title"] == "finish long task"
    assert shown["operator_state"] == "running"
    assert shown["resume_action"] == "continue"
    assert shown["activity"] == {
        "session_id": "s1",
        "session_events_path": "/sessions/s1/events",
        "session_messages_path": "/sessions/s1/messages",
        "turn_inputs_path": "/v1/sessions/s1/turn-inputs",
        "turn_stream_path": "/v1/turn/trace-1/stream",
    }
    paused = surface.apply_action(task_id="lt1", action="pause")
    assert paused["task"]["status"] == "WAITING"
    paused_task = build_task_surface(manager).show_task("lt1")
    assert paused_task is not None
    assert paused_task["operator_state"] == "waiting"
    resumed = surface.apply_action(task_id="lt1", action="resume")
    assert resumed["task"]["status"] == "ACTIVE"
    cancelled = surface.apply_action(task_id="lt1", action="cancel")
    assert cancelled["task"]["status"] == "CANCELED"

    manager.create_linked_task(
        linked_job_id="job-without-session",
        agent_id="agent",
        task_id="lt2",
    )
    without_activity = surface.show_task("lt2")
    assert without_activity is not None
    assert "activity" not in without_activity


def test_task_surface_quotes_activity_route_segments() -> None:
    manager = TaskManager.for_lifecycle_db(db_path=":memory:")
    manager.create_task(
        session_id="session/one?",
        mode_name="research",
        goal="inspect links",
        agent_id="agent",
        task_id="linked",
        metadata={"trace_id": "trace/one?"},
    )

    shown = build_task_surface(manager).show_task("linked")

    assert shown is not None
    assert shown["activity"] == {
        "session_id": "session/one?",
        "session_events_path": "/sessions/session%2Fone%3F/events",
        "session_messages_path": "/sessions/session%2Fone%3F/messages",
        "turn_inputs_path": "/v1/sessions/session%2Fone%3F/turn-inputs",
        "turn_stream_path": "/v1/turn/trace%2Fone%3F/stream",
    }


def _cursor(task_id: str):
    from datetime import datetime, timezone

    from openminion.modules.task import ResumePointer

    return ResumePointer(
        task_id=task_id,
        plan_id="p1",
        step_id="s1",
        trace_id=f"trace:{datetime.now(timezone.utc).isoformat()}",
    )
