from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs

from openminion.api.core.deps import (
    resolve_runtime_manager,
    v1_capability_report,
    v1_runtime_posture,
    v1_runtime_self_model,
)

from .contracts import APIRouteContext, RouteResult, runtime_unavailable_route_result


def handle_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict | None,
    query: str | None,
) -> RouteResult | None:
    del body
    if method_name != "GET":
        return None
    if path not in {
        "/v1/runtime/capabilities",
        "/v1/runtime/posture",
        "/v1/runtime/self-model",
    }:
        return None

    try:
        _, active_runtime, own_runtime = resolve_runtime_manager(
            config_path=ctx.config_path,
            runtime=ctx.runtime,
        )
    except Exception as exc:  # noqa: BLE001
        return runtime_unavailable_route_result(path=path, exc=exc)
    try:
        agent_id = _query_value(query, "agent_id")
        if path == "/v1/runtime/capabilities":
            payload = {
                "ok": True,
                "capabilities": v1_capability_report(active_runtime, agent_id=agent_id),
            }
        elif path == "/v1/runtime/posture":
            payload = {
                "ok": True,
                "runtime": v1_runtime_posture(active_runtime, agent_id=agent_id),
            }
        else:
            try:
                snapshot = v1_runtime_self_model(active_runtime, agent_id=agent_id)
            except Exception as exc:  # noqa: BLE001
                return runtime_unavailable_route_result(path=path, exc=exc)
            payload = {
                "ok": True,
                "self_model": snapshot,
                "health": snapshot.get("health", "unavailable"),
            }
        return RouteResult(status=_status_for_payload(payload), payload=payload)
    finally:
        if own_runtime:
            active_runtime.close()


def _query_value(query: str | None, name: str) -> str | None:
    values = parse_qs(str(query or ""), keep_blank_values=False).get(name, [])
    if not values:
        return None
    value = str(values[0] or "").strip()
    return value or None


def _status_for_payload(payload: dict) -> HTTPStatus:
    if "self_model" not in payload:
        return HTTPStatus.OK
    self_model = dict(payload.get("self_model", {}) or {})
    return HTTPStatus.OK if self_model else HTTPStatus.SERVICE_UNAVAILABLE
