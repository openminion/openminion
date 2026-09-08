from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus
from types import SimpleNamespace

from openminion.api.operations import tasks as task_operations
from openminion.api.routes.contracts import APIRouteContext
from openminion.api.routes.tasks import handle_request
from openminion.modules.task import InMemoryTaskCtl, TaskCreateInput
from openminion.modules.session.storage.repository import create_sqlite_cron_repository
from openminion.modules.task import TaskManager


def _ctx() -> APIRouteContext:
    ctl = InMemoryTaskCtl()
    ctl.create_task(TaskCreateInput(task_id="t1", title="Inspect route"))
    return APIRouteContext(
        config_path=None,
        runtime=SimpleNamespace(task_ctl=ctl),
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="test-request",
    )


def test_tasks_route_lists_tasks() -> None:
    result = handle_request(
        _ctx(), method_name="GET", path="/v1/tasks", body=None, query="agent_id=agent-a"
    )

    assert result is not None
    assert result.status == HTTPStatus.OK
    assert result.payload["tasks"][0]["id"] == "t1"


def test_tasks_route_shows_task() -> None:
    result = handle_request(
        _ctx(),
        method_name="GET",
        path="/v1/tasks/t1",
        body=None,
        query="agent_id=agent-a",
    )

    assert result is not None
    assert result.status == HTTPStatus.OK
    assert result.payload["task"]["title"] == "Inspect route"


def test_tasks_route_returns_none_for_other_paths() -> None:
    assert (
        handle_request(
            _ctx(), method_name="GET", path="/v1/unknown", body=None, query=""
        )
        is None
    )


def test_tasks_route_creates_and_deduplicates_user_schedule(
    tmp_path, monkeypatch
) -> None:
    repository = create_sqlite_cron_repository(db_path=tmp_path / "tasks.db")
    manager = TaskManager.from_cron_repository(repository)
    config_path = tmp_path / "openminion.json"
    health_calls = []
    heartbeat = datetime.now(timezone.utc).isoformat()

    def _health_snapshot(*, config_path, runtime):
        health_calls.append((config_path, runtime))
        return {
            "normalized_health_snapshot": {
                "components": [
                    {
                        "component": {"component_kind": "cron_scheduler"},
                        "readiness": "ready",
                        "last_heartbeat_at": heartbeat,
                    }
                ]
            }
        }

    monkeypatch.setattr(task_operations, "collect_health_snapshot", _health_snapshot)
    runtime = SimpleNamespace(task_manager=manager, config_path=config_path)
    ctx = APIRouteContext(
        config_path=None,
        runtime=runtime,
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="test-request",
    )
    body = {
        "name": "daily",
        "instruction": "summarize the repository",
        "schedule": {"kind": "cron", "expr": "0 9 * * *", "tz": "UTC"},
    }

    created = handle_request(
        ctx,
        method_name="POST",
        path="/v1/tasks",
        body=body,
        query="agent_id=agent-a&session_id=session-a",
    )
    duplicate = handle_request(
        ctx,
        method_name="POST",
        path="/v1/tasks",
        body=body,
        query="agent_id=agent-a&session_id=session-a",
    )

    assert created is not None
    assert created.status == HTTPStatus.CREATED
    assert created.payload["task"]["agent_id"] == "agent-a"
    assert created.payload["task"]["task_kind"] == "scheduled_recurring"
    assert created.payload["scheduler"]["state"] == "ready"
    assert health_calls == [(str(config_path), runtime), (str(config_path), runtime)]
    assert duplicate is not None
    assert duplicate.status == HTTPStatus.OK
    assert duplicate.payload["job_id"] == created.payload["job_id"]

    task_id = str(created.payload["task"]["id"])
    listed = handle_request(
        ctx,
        method_name="GET",
        path="/v1/tasks",
        body=None,
        query="agent_id=agent-a",
    )
    shown = handle_request(
        ctx,
        method_name="GET",
        path=f"/v1/tasks/{task_id}",
        body=None,
        query="agent_id=agent-a",
    )
    assert listed is not None and listed.payload["count"] == 1
    assert shown is not None and shown.payload["task"]["id"] == task_id

    for action, expected_state in (
        ("pause", "paused"),
        ("resume", "active"),
        ("cancel", "cancelled"),
    ):
        changed = handle_request(
            ctx,
            method_name="POST",
            path=f"/v1/tasks/{task_id}/{action}",
            body={},
            query="agent_id=agent-a",
        )
        assert changed is not None
        assert changed.status == HTTPStatus.OK
        assert changed.payload["task"]["lifecycle_state"] == expected_state

    cross_agent = handle_request(
        ctx,
        method_name="GET",
        path=f"/v1/tasks/{task_id}",
        body=None,
        query="agent_id=agent-b",
    )
    assert cross_agent is not None
    assert cross_agent.status == HTTPStatus.FORBIDDEN
    assert cross_agent.payload["error"]["code"] == "task_scope_denied"


def test_tasks_route_health_failure_does_not_report_a_persisted_task_as_failed(
    tmp_path, monkeypatch
) -> None:
    repository = create_sqlite_cron_repository(db_path=tmp_path / "tasks.db")
    manager = TaskManager.from_cron_repository(repository)
    runtime = SimpleNamespace(
        task_manager=manager,
        config_path=tmp_path / "openminion.json",
    )
    ctx = APIRouteContext(
        config_path=None,
        runtime=runtime,
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="test-request",
    )

    def _health_failure(**_kwargs):
        raise AttributeError("health unavailable")

    monkeypatch.setattr(task_operations, "collect_health_snapshot", _health_failure)

    result = handle_request(
        ctx,
        method_name="POST",
        path="/v1/tasks",
        body={
            "instruction": "summarize the repository",
            "schedule": {"kind": "cron", "expr": "0 9 * * *", "tz": "UTC"},
        },
        query="agent_id=agent-a",
    )

    assert result is not None
    assert result.status == HTTPStatus.CREATED
    assert result.payload["scheduler"]["state"] != "ready"
    assert len(manager.list_scheduled_jobs(limit=10)) == 1


def test_tasks_route_caps_list_limit() -> None:
    limits: list[int] = []

    class Source:
        lifecycle_repository = SimpleNamespace(list=lambda **_kwargs: [])

        def get_digest(self, *, agent_id, session_id, limit):
            del agent_id, session_id
            limits.append(limit)
            return SimpleNamespace(
                tasks_active=[],
                tasks_ready=[],
                current_task=None,
            )

    ctx = APIRouteContext(
        config_path=None,
        runtime=SimpleNamespace(task_ctl=Source()),
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="test-request",
    )

    result = handle_request(
        ctx,
        method_name="GET",
        path="/v1/tasks",
        body=None,
        query="agent_id=agent-a&limit=100000",
    )

    assert result is not None
    assert result.status == HTTPStatus.OK
    assert limits == [100]


def test_tasks_route_requires_agent_for_creation() -> None:
    result = handle_request(
        _ctx(),
        method_name="POST",
        path="/v1/tasks",
        body={
            "instruction": "work",
            "schedule": {"kind": "at", "at": "2030-01-01T00:00:00Z"},
        },
        query="",
    )

    assert result is not None
    assert result.status == HTTPStatus.BAD_REQUEST
    assert result.payload["error"]["code"] == "agent_id_required"


def test_tasks_route_requires_agent_for_reads() -> None:
    listed = handle_request(
        _ctx(), method_name="GET", path="/v1/tasks", body=None, query=""
    )
    shown = handle_request(
        _ctx(), method_name="GET", path="/v1/tasks/t1", body=None, query=""
    )

    assert listed is not None
    assert listed.status == HTTPStatus.BAD_REQUEST
    assert listed.payload["error"]["code"] == "agent_id_required"
    assert shown is not None
    assert shown.status == HTTPStatus.BAD_REQUEST
    assert shown.payload["error"]["code"] == "agent_id_required"


def test_tasks_route_rejects_unknown_creation_fields() -> None:
    result = handle_request(
        _ctx(),
        method_name="POST",
        path="/v1/tasks",
        body={
            "instruction": "work",
            "schedule": {"kind": "at", "at": "2030-01-01T00:00:00Z"},
            "agent_id": "agent-b",
        },
        query="agent_id=agent-a",
    )

    assert result is not None
    assert result.status == HTTPStatus.BAD_REQUEST
    assert result.payload["error"]["code"] == "invalid_task"


def test_tasks_route_requires_exact_pending_action_owner() -> None:
    resume_calls: list[str] = []
    source = SimpleNamespace(
        get_digest=lambda **_kwargs: None,
        list_pending_actions=lambda *, agent_id, session_id, limit: (
            [
                {
                    "policy_request_id": "pr1",
                    "agent_id": "agent-a",
                    "session_id": "session-a",
                    "cursor": {"task_id": "t1"},
                }
            ]
            if agent_id == "agent-a" and session_id == "session-a" and limit > 0
            else []
        ),
        get_pending_action=lambda policy_request_id, *, agent_id, session_id: (
            {
                "policy_request_id": "pr1",
                "agent_id": "agent-a",
                "session_id": "session-a",
                "cursor": {"task_id": "t1"},
            }
            if (
                policy_request_id == "pr1"
                and agent_id == "agent-a"
                and session_id == "session-a"
            )
            else None
        ),
        resume_pending_action=lambda **kwargs: resume_calls.append(
            kwargs["policy_request_id"]
        ),
    )
    ctx = APIRouteContext(
        config_path=None,
        runtime=SimpleNamespace(task_ctl=source),
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="test-request",
    )

    for query in (
        "agent_id=agent-a",
        "agent_id=agent-b&session_id=session-a",
        "agent_id=agent-a&session_id=session-b",
    ):
        rejected = handle_request(
            ctx,
            method_name="POST",
            path="/v1/tasks/pending/pr1/allow",
            body={},
            query=query,
        )
        assert rejected is not None
        assert rejected.status == HTTPStatus.BAD_REQUEST
        assert rejected.payload["error"]["code"] == "invalid_task_action"

    allowed = handle_request(
        ctx,
        method_name="POST",
        path="/v1/tasks/pending/pr1/allow",
        body={},
        query="agent_id=agent-a&session_id=session-a",
    )

    assert allowed is not None
    assert allowed.status == HTTPStatus.OK
    assert resume_calls == ["pr1"]
