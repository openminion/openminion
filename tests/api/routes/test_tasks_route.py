from __future__ import annotations

from http import HTTPStatus
from types import SimpleNamespace

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


def test_tasks_route_creates_and_deduplicates_user_schedule(tmp_path) -> None:
    repository = create_sqlite_cron_repository(db_path=tmp_path / "tasks.db")
    manager = TaskManager.from_cron_repository(repository)
    ctx = APIRouteContext(
        config_path=None,
        runtime=SimpleNamespace(task_manager=manager),
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
    assert created.payload["scheduler"]["state"] == "unknown"
    assert created.payload["scheduler"]["check_command"].endswith("service status cron")
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
