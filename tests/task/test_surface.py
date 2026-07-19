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
    )
    surface = build_task_surface(manager)

    assert surface.show_task("lt1")["title"] == "finish long task"  # type: ignore[index]
    paused = surface.apply_action(task_id="lt1", action="pause")
    assert paused["task"]["status"] == "WAITING"
    resumed = surface.apply_action(task_id="lt1", action="resume")
    assert resumed["task"]["status"] == "ACTIVE"
    cancelled = surface.apply_action(task_id="lt1", action="cancel")
    assert cancelled["task"]["status"] == "CANCELED"


def _cursor(task_id: str):
    from datetime import datetime, timezone

    from openminion.modules.task import ResumePointer

    return ResumePointer(
        task_id=task_id,
        plan_id="p1",
        step_id="s1",
        trace_id=f"trace:{datetime.now(timezone.utc).isoformat()}",
    )
