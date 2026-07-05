from __future__ import annotations

from http import HTTPStatus

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
    body: dict | None,
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
            manager.kill_switch(grace_s=2)
            return RouteResult(
                status=HTTPStatus.OK, payload={"ok": True, "status": "stopped"}
            )
        finally:
            if own_runtime:
                active_runtime.close()

    return None
