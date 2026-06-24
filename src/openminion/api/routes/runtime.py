from __future__ import annotations

from http import HTTPStatus

from openminion.api.core.deps import (
    resolve_runtime_manager,
    v1_capability_report,
    v1_runtime_posture,
)

from .base import APIRouteContext, RouteResult


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict | None,
    query: str | None,
) -> RouteResult | None:
    del body, query
    if method_name != "GET":
        return None
    if path not in {"/v1/runtime/capabilities", "/v1/runtime/posture"}:
        return None

    _, active_runtime, own_runtime = resolve_runtime_manager(
        config_path=ctx.config_path,
        runtime=ctx.runtime,
    )
    try:
        if path == "/v1/runtime/capabilities":
            payload = {
                "ok": True,
                "capabilities": v1_capability_report(active_runtime),
            }
        else:
            payload = {
                "ok": True,
                "runtime": v1_runtime_posture(active_runtime),
            }
        return RouteResult(status=HTTPStatus.OK, payload=payload)
    finally:
        if own_runtime:
            active_runtime.close()
