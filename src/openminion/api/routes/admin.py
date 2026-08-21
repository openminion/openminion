from http import HTTPStatus
from typing import Any, cast

from openminion.api.core.deps import resolve_runtime_manager

from .contracts import (
    APIRouteContext,
    RouteResult,
    runtime_unavailable_route_result,
)


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
    query: str | None,
) -> RouteResult | None:
    del body, query
    if method_name == "POST" and path == "/v1/admin/kill":
        try:
            manager, active_runtime, own_runtime = resolve_runtime_manager(
                config_path=ctx.config_path,
                runtime=ctx.runtime,
            )
        except Exception as exc:  # noqa: BLE001
            return runtime_unavailable_route_result(path=path, exc=exc)
        try:
            cast(Any, manager).kill_switch(grace_s=2)
            return RouteResult(HTTPStatus.OK, {"ok": True, "status": "stopped"})
        finally:
            if own_runtime:
                active_runtime.close()

    return None
