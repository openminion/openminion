from __future__ import annotations

import pytest

from openminion.modules.task import InMemoryTaskCtl, TaskCreateInput
from openminion.modules.task.runtime.lifecycle import TaskManager
from openminion.modules.task.surface import build_task_surface
from openminion.modules.session.storage.repository import create_sqlite_cron_repository


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
    surface = build_task_surface(manager, agent_id="agent")

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
    paused_task = build_task_surface(manager, agent_id="agent").show_task("lt1")
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

    shown = build_task_surface(manager, agent_id="agent").show_task("linked")

    assert shown is not None
    assert shown["activity"] == {
        "session_id": "session/one?",
        "session_events_path": "/sessions/session%2Fone%3F/events",
        "session_messages_path": "/sessions/session%2Fone%3F/messages",
        "turn_inputs_path": "/v1/sessions/session%2Fone%3F/turn-inputs",
        "turn_stream_path": "/v1/turn/trace%2Fone%3F/stream",
    }


def test_task_surface_hides_lifecycle_tasks_without_exact_agent_scope() -> None:
    manager = TaskManager.for_lifecycle_db(db_path=":memory:")
    manager.create_task(
        session_id="s1",
        mode_name="research",
        goal="private task",
        agent_id="agent-a",
        task_id="lt1",
    )

    assert build_task_surface(manager).list_tasks() == []
    with pytest.raises(PermissionError):
        build_task_surface(manager, agent_id="agent-b").show_task("lt1")
    assert len(build_task_surface(manager, agent_id="agent-a").list_tasks()) == 1


def test_task_surface_projects_schedule_and_bounded_runs(tmp_path) -> None:
    manager = TaskManager.from_cron_repository(
        create_sqlite_cron_repository(db_path=tmp_path / "tasks.db")
    )
    record = manager.schedule_task(
        name="daily",
        schedule={"kind": "cron", "expr": "0 9 * * *", "tz": "UTC"},
        payload={"kind": "agentTurn", "message": "summarize"},
        agent_id="agent-a",
        delete_after_run=False,
    )
    run_id = getattr(manager, "_cron_repository").trigger_cron_run(record.cron_job_id)
    getattr(manager, "_cron_repository").finish_cron_run(
        run_id,
        state="failed",
        error={"code": "provider_failed", "message": "unavailable"},
    )
    manager.reconcile_scheduled_outcomes(record.cron_job_id)

    task = build_task_surface(manager, agent_id="agent-a").show_task(record.task_id)

    assert task is not None
    assert task["lifecycle_state"] == "active"
    assert task["task_kind"] == "scheduled_recurring"
    assert task["schedule_summary"] == "cron:0 9 * * * tz=UTC"
    assert "T" in task["due_at"]
    assert task["due_at"].endswith("+00:00")
    assert task["last_run"]["run_id"] == run_id
    assert task["last_run"]["last_error"]["code"] == "provider_failed"
    assert task["valid_actions"] == ["pause", "cancel"]


def test_task_surface_merges_digest_and_lifecycle_sources(tmp_path) -> None:
    manager = TaskManager.from_cron_repository(
        create_sqlite_cron_repository(db_path=tmp_path / "merged.db")
    )
    manager.schedule_task(
        name="scheduled",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "work"},
        agent_id="agent-a",
        job_id="scheduled-1",
    )

    class Source:
        lifecycle_repository = manager.lifecycle_repository
        get_task = manager.get_task
        get_scheduled_job = manager.get_scheduled_job
        list_scheduled_runs = manager.list_scheduled_runs

        def get_digest(self, *, agent_id: str, session_id: str, limit: int):
            del agent_id, session_id, limit
            task = type(
                "Task",
                (),
                {
                    "task_id": "ordinary-1",
                    "title": "ordinary",
                    "status": "ACTIVE",
                    "due_at": None,
                    "next_step_id": "",
                    "next_step_title": "",
                    "metadata": {},
                },
            )()
            return type(
                "Digest",
                (),
                {"tasks_active": [task], "tasks_ready": [], "current_task": None},
            )()

    task_ids = {
        item["id"]
        for item in build_task_surface(Source(), agent_id="agent-a").list_tasks()
    }

    assert task_ids == {"ordinary-1", "scheduled-1"}


def _cursor(task_id: str):
    from datetime import datetime, timezone

    from openminion.modules.task import ResumePointer

    return ResumePointer(
        task_id=task_id,
        plan_id="p1",
        step_id="s1",
        trace_id=f"trace:{datetime.now(timezone.utc).isoformat()}",
    )
