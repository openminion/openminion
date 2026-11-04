from __future__ import annotations

from types import SimpleNamespace

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
    assert payload["tasks"][0]["resume_action"] == "inspect-blocker"
    assert payload["tasks"][0]["pending_actions"][0]["decision_id"] == "pr1"
    assert payload["pending_actions"][0]["reason"] == "approval needed"


def test_task_surface_does_not_expose_pending_actions_for_hidden_tasks() -> None:
    ctl = InMemoryTaskCtl()
    visible = ctl.create_task(TaskCreateInput(task_id="visible", title="Visible"))
    ctl.record_pending_action(
        policy_request_id="visible-decision",
        cursor=_cursor(visible.task_id),
        reason="approval needed",
    )
    ctl.record_pending_action(
        policy_request_id="hidden-decision",
        cursor=_cursor("hidden"),
        reason="belongs to another scope",
    )
    source = SimpleNamespace(
        get_digest=lambda **_kwargs: {
            "tasks_active": [{"task_id": "visible", "title": "Visible"}],
            "tasks_ready": [],
        },
        list_events=ctl.list_events,
    )

    payload = build_task_surface(source, agent_id="agent", session_id="s1").inventory()

    assert [task["id"] for task in payload["tasks"]] == ["visible"]
    assert [item["decision_id"] for item in payload["pending_actions"]] == [
        "visible-decision"
    ]
    assert not hasattr(build_task_surface(source), "apply_action")


def test_task_surface_lists_lifecycle_tasks_without_mutation_api() -> None:
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
    assert shown["valid_actions"] == ["pause", "cancel"]
    assert shown["activity"] == {
        "session_id": "s1",
        "session_events_path": "/sessions/s1/events",
        "session_messages_path": "/sessions/s1/messages",
        "turn_inputs_path": "/v1/sessions/s1/turn-inputs",
        "turn_stream_path": "/v1/turn/trace-1/stream",
    }
    assert not hasattr(surface, "apply_action")

    manager.create_linked_task(
        linked_job_id="job-without-session",
        agent_id="agent",
        task_id="lt2",
    )
    without_activity = surface.show_task("lt2")
    assert without_activity is not None
    assert "activity" not in without_activity

    project = manager.create_task(
        session_id="s1",
        mode_name="project",
        goal="specialized project",
        agent_id="agent",
        task_id="project-task",
    )
    projected = surface.show_task(project.task_id)
    assert projected is not None
    assert projected["valid_actions"] == []


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
        output={"delivery": {"state": "failed", "error": "sink unavailable"}},
        isolated_session_id="result-session",
    )
    getattr(manager, "_cron_repository").mark_cron_delivery_target(
        run_id, target="session:origin"
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
    assert task["last_run"]["isolated_session_id"] == "result-session"
    assert task["last_run"]["delivery"] == {
        "state": "failed",
        "error": "sink unavailable",
    }
    assert task["last_run"]["delivery_targets"] == ["session:origin"]
    assert task["session_target"] == "isolated"
    assert task["valid_actions"] == [
        "pause",
        "cancel",
        "run-now",
        "update",
        "delete-history",
    ]


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


def test_task_surface_applies_limit_after_merging_sources(tmp_path) -> None:
    manager = TaskManager.from_cron_repository(
        create_sqlite_cron_repository(db_path=tmp_path / "bounded.db")
    )
    for index in range(3):
        manager.schedule_task(
            name=f"scheduled-{index}",
            schedule={"kind": "every", "every_ms": 60_000},
            payload={"kind": "agentTurn", "message": "work"},
            agent_id="agent-a",
            job_id=f"scheduled-{index}",
        )

    class Source:
        lifecycle_repository = manager.lifecycle_repository
        get_task = manager.get_task
        get_scheduled_job = manager.get_scheduled_job
        list_scheduled_runs = manager.list_scheduled_runs

        def get_digest(self, *, agent_id: str, session_id: str, limit: int):
            del agent_id, session_id, limit
            tasks = [
                type(
                    "Task",
                    (),
                    {
                        "task_id": f"ordinary-{index}",
                        "title": f"ordinary-{index}",
                        "status": "ACTIVE",
                        "due_at": None,
                        "next_step_id": "",
                        "next_step_title": "",
                        "metadata": {},
                    },
                )()
                for index in range(3)
            ]
            return type(
                "Digest",
                (),
                {"tasks_active": tasks, "tasks_ready": [], "current_task": None},
            )()

    tasks = build_task_surface(Source(), agent_id="agent-a", limit=2).list_tasks()

    assert len(tasks) == 2


def test_task_surface_does_not_advertise_update_for_specialized_watch(tmp_path) -> None:
    manager = TaskManager.from_cron_repository(
        create_sqlite_cron_repository(db_path=tmp_path / "watch.db")
    )
    record = manager.schedule_task(
        name="watch",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={
            "kind": "agentTurn",
            "message": "check status",
            "_openminion_watch": {"mode": "check"},
        },
        agent_id="agent-a",
        delete_after_run=False,
    )

    task = build_task_surface(manager, agent_id="agent-a").show_task(record.task_id)

    assert task is not None
    assert task["valid_actions"] == ["pause", "cancel", "run-now", "delete-history"]


def test_task_surface_bounds_recent_run_limit(tmp_path) -> None:
    manager = TaskManager.from_cron_repository(
        create_sqlite_cron_repository(db_path=tmp_path / "run-limit.db")
    )
    record = manager.schedule_task(
        name="bounded-runs",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "check"},
        agent_id="agent-a",
    )
    repository = getattr(manager, "_cron_repository")
    for index in range(25):
        run_id = repository.trigger_cron_run(record.cron_job_id)
        repository.finish_cron_run(run_id, state="finished", summary=f"run-{index}")

    default_task = build_task_surface(manager, agent_id="agent-a").show_task(
        record.task_id
    )
    surface = build_task_surface(manager, agent_id="agent-a")
    explicit_task = surface.show_task(record.task_id, runs_limit=8)
    capped_task = surface.show_task(record.task_id, runs_limit=100)
    minimum_task = surface.show_task(record.task_id, runs_limit=0)

    assert default_task is not None and len(default_task["recent_runs"]) == 5
    assert explicit_task is not None and len(explicit_task["recent_runs"]) == 8
    assert capped_task is not None and len(capped_task["recent_runs"]) == 20
    assert minimum_task is not None and len(minimum_task["recent_runs"]) == 1
    with pytest.raises(ValueError):
        surface.show_task(record.task_id, runs_limit="invalid")


def test_task_surface_distinguishes_backend_failure_from_empty_inventory() -> None:
    class BrokenRepository:
        def list(self, **kwargs):
            del kwargs
            raise RuntimeError("database unavailable")

    class Source:
        lifecycle_repository = BrokenRepository()

    with pytest.raises(RuntimeError) as excinfo:
        build_task_surface(Source(), agent_id="agent-a").inventory()

    assert getattr(excinfo.value, "code", None) == "TASK_INVENTORY_UNAVAILABLE"


def test_task_surface_caps_list_limit_for_all_entry_points() -> None:
    seen: dict[str, int] = {}

    class Source:
        def get_digest(self, *, agent_id: str, session_id: str, limit: int):
            del agent_id, session_id
            seen["limit"] = limit
            return {"tasks_active": [], "tasks_ready": []}

    build_task_surface(Source(), agent_id="agent-a", limit=5_000).inventory()

    assert seen["limit"] == 100


def _cursor(task_id: str):
    from datetime import datetime, timezone

    from openminion.modules.task import ResumePointer

    return ResumePointer(
        task_id=task_id,
        plan_id="p1",
        step_id="s1",
        trace_id=f"trace:{datetime.now(timezone.utc).isoformat()}",
    )


@pytest.mark.parametrize(
    "latest", ["none", "completed", "partial", "failed", "missing", "running"]
)
def test_security_scan_detail_keeps_current_attempt_separate(tmp_path, latest):
    from tests.tools.security.test_schedule import admission

    manager = TaskManager.from_cron_repository(
        create_sqlite_cron_repository(db_path=tmp_path / "tasks.db")
    )
    scope = admission(tmp_path)
    created = manager.schedule_user_task(
        name="source assessment",
        instruction="audit",
        schedule={"kind": "every", "every_ms": 60000},
        agent_id=scope.agent_id,
        security_scan=scope.model_dump(mode="json"),
    )
    record = created["record"]
    first = {
        "job_id": record.cron_job_id,
        "run_id": "first",
        "status": "completed",
        "revision": "a" * 40,
        "report_ref": "artifact://sha256/" + "a" * 64,
        "sarif_ref": "artifact://sha256/" + "b" * 64,
        "comparison_ref": None,
        "baseline_ref": None,
        "private_payload": "not a public field",
    }
    if latest != "none":
        manager.mutate_task_metadata(
            task_id=record.task_id,
            mutate=lambda metadata: {
                **metadata,
                "security_scan": {"admitted_runs": 1, "last_admitted_run_id": "first"},
            },
        )
        manager.record_scheduled_outcome(
            cron_job_id=record.cron_job_id,
            run={
                "run_id": "first",
                "state": "finished",
                "output": {"security_scan": first},
            },
        )
    if latest in {"partial", "failed", "missing", "running"}:
        manager.mutate_task_metadata(
            task_id=record.task_id,
            mutate=lambda metadata: {
                **metadata,
                "security_scan": {
                    **metadata["security_scan"],
                    "admitted_runs": 2,
                    "last_admitted_run_id": "second",
                    "status": "running",
                },
            },
        )
        if latest != "running":
            manager.record_scheduled_outcome(
                cron_job_id=record.cron_job_id,
                run={
                    "run_id": "second",
                    "state": "failed",
                    "error": {"code": "provider_failed", "message": "unavailable"},
                    "output": {
                        "security_scan": {
                            "job_id": record.cron_job_id,
                            "run_id": "second",
                            "status": latest,
                            "reason_code": "incomplete_report",
                            "report_ref": "artifact://sha256/" + "c" * 64,
                            "evidence_refs": [],
                            "private_payload": "not public",
                        }
                    }
                    if latest in {"partial", "failed"}
                    else {},
                },
            )
        manager.set_scheduled_job_enabled(record.task_id, enabled=False)
    surface = build_task_surface(manager, agent_id=scope.agent_id)
    task = surface.show_task(record.task_id)
    scan = task["security_scan"]
    assert scan["checks"] == ["code"]
    assert scan["expires_at"] == scope.model_dump(mode="json")["expires_at"]
    assert scan["max_runs"] == 2
    assert "private_payload" not in str(scan)
    assert "config_fingerprint" not in scan and "root" not in scan
    if latest == "none":
        assert scan["admitted_runs"] is None and scan["remaining_runs"] is None
        assert scan["status"] is None and scan["last_successful_evidence"] is None
    else:
        assert scan["last_successful_evidence"]["run_id"] == "first"
        assert scan["last_successful_evidence"]["report_ref"] == first["report_ref"]
        assert scan["remaining_runs"] == (1 if latest == "completed" else 0)
    if latest in {"completed", "partial", "failed"}:
        assert scan["last_attempt"]["status"] == latest
        assert scan["last_attempt"]["run_id"] == (
            "first" if latest == "completed" else "second"
        )
        if latest != "completed":
            assert scan["last_attempt"]["report_ref"] != first["report_ref"]
    else:
        assert scan["last_attempt"] is None
    if latest == "missing":
        assert scan["status"] == "failed" and scan["completed_run_id"] == "second"
        assert task["last_run"]["last_error"]["code"] == "provider_failed"
    with pytest.raises(PermissionError):
        build_task_surface(manager, agent_id="other").show_task(record.task_id)
    assert build_task_surface(manager, agent_id="other").list_tasks() == []
    if latest == "completed":
        manager.pause_task(record.task_id)
        assert surface.show_task(record.task_id)["security_scan"] == scan
        manager.resume_task(record.task_id)
        manager.cancel_task(record.task_id)
        cancelled = surface.show_task(record.task_id)
        assert cancelled["lifecycle_state"] == "cancelled"
        assert cancelled["security_scan"] == scan
    manager.close()
