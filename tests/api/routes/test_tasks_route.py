from __future__ import annotations

from http import HTTPStatus
from types import SimpleNamespace

from openminion.api.routes.contracts import APIRouteContext
from openminion.api.routes.tasks import handle_request
from openminion.modules.task import InMemoryTaskCtl, TaskCreateInput


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
        _ctx(), method_name="GET", path="/v1/tasks", body=None, query=""
    )

    assert result is not None
    assert result.status == HTTPStatus.OK
    assert result.payload["tasks"][0]["id"] == "t1"


def test_tasks_route_shows_task() -> None:
    result = handle_request(
        _ctx(), method_name="GET", path="/v1/tasks/t1", body=None, query=""
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
