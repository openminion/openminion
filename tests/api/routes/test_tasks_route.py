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
        runtime=SimpleNamespace(
            task_manager=manager,
            scheduler_readiness=lambda: {
                "state": "ready",
                "hosted_by": "daemon",
                "reason": None,
            },
        ),
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
    assert "check_command" not in created.payload["scheduler"]
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


def test_tasks_route_filters_owner_before_limit(tmp_path) -> None:
    manager = TaskManager.from_cron_repository(
        create_sqlite_cron_repository(db_path=tmp_path / "tasks.db")
    )
    owned = manager.schedule_task(
        name="owned-old-row",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "owned"},
        agent_id="agent-a",
    )
    for index in range(60):
        manager.schedule_task(
            name=f"foreign-{index}",
            schedule={"kind": "every", "every_ms": 60_000},
            payload={"kind": "agentTurn", "message": "foreign"},
            agent_id="agent-b",
        )
    ctx = APIRouteContext(
        config_path=None,
        runtime=SimpleNamespace(task_manager=manager),
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="test-request",
    )

    result = handle_request(
        ctx,
        method_name="GET",
        path="/v1/tasks",
        body=None,
        query="agent_id=agent-a&limit=1",
    )

    assert result is not None
    assert result.status == HTTPStatus.OK
    assert [task["id"] for task in result.payload["tasks"]] == [owned.task_id]


def test_tasks_route_uses_shared_relative_schedule_contract(tmp_path) -> None:
    manager = TaskManager.from_cron_repository(
        create_sqlite_cron_repository(db_path=tmp_path / "tasks.db")
    )
    ctx = APIRouteContext(
        config_path=None,
        runtime=SimpleNamespace(
            task_manager=manager,
            scheduler_readiness=lambda: {"state": "ready"},
        ),
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="test-request",
    )

    relative = handle_request(
        ctx,
        method_name="POST",
        path="/v1/tasks",
        body={
            "instruction": "run later",
            "schedule": {"kind": "at", "after_seconds": 120},
        },
        query="agent_id=agent-a",
    )
    alias = handle_request(
        ctx,
        method_name="POST",
        path="/v1/tasks",
        body={
            "instruction": "do not widen API aliases",
            "schedule": {"kind": "every", "minutes": 5},
        },
        query="agent_id=agent-a",
    )

    assert relative is not None
    assert relative.status == HTTPStatus.CREATED
    assert relative.payload["task"]["schedule"]["kind"] == "at"
    assert alias is not None
    assert alias.status == HTTPStatus.BAD_REQUEST
    assert alias.payload["error"]["code"] == "invalid_task"


def test_tasks_route_maps_inventory_and_expired_resume_errors(tmp_path) -> None:
    class BrokenRepository:
        def list(self, **kwargs):
            del kwargs
            raise RuntimeError("database unavailable")

    class BrokenManager:
        lifecycle_repository = BrokenRepository()

        def get_task(self, task_id: str):
            del task_id
            return None

    broken_ctx = APIRouteContext(
        config_path=None,
        runtime=SimpleNamespace(task_manager=BrokenManager()),
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="test-request",
    )
    unavailable = handle_request(
        broken_ctx,
        method_name="GET",
        path="/v1/tasks",
        body=None,
        query="agent_id=agent-a",
    )
    assert unavailable is not None
    assert unavailable.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert unavailable.payload["error"]["code"] == "TASK_INVENTORY_UNAVAILABLE"

    manager = TaskManager.from_cron_repository(
        create_sqlite_cron_repository(db_path=tmp_path / "expired.db")
    )
    record = manager.schedule_task(
        name="expired",
        schedule={"kind": "at", "at": "2000-01-01T00:00:00Z"},
        payload={"kind": "agentTurn", "message": "expired"},
        agent_id="agent-a",
        enabled=False,
    )
    manager.transition_task(task_id=record.task_id, to_state="paused")
    expired_ctx = APIRouteContext(
        config_path=None,
        runtime=SimpleNamespace(task_manager=manager),
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="test-request",
    )
    rejected = handle_request(
        expired_ctx,
        method_name="POST",
        path=f"/v1/tasks/{record.task_id}/resume",
        body={},
        query="agent_id=agent-a",
    )
    assert rejected is not None
    assert rejected.status == HTTPStatus.BAD_REQUEST
    assert rejected.payload["error"]["code"] == "TASK_RESUME_EXPIRED_ONE_SHOT"
