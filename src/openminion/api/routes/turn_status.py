"""Active-turn status route."""

from http import HTTPStatus
from typing import Any, cast

from openminion.api.core.deps import resolve_runtime_manager
from .contracts import (
    APIRouteContext,
    RouteResult,
    error_route_result,
    runtime_unavailable_route_result,
)


def handle_turn_status(
    ctx: APIRouteContext,
    *,
    path: str,
    trace_id: str,
) -> RouteResult:
    try:
        manager, active_runtime, own_runtime = resolve_runtime_manager(
            config_path=ctx.config_path,
            runtime=ctx.runtime,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return runtime_unavailable_route_result(path=path, exc=exc)
    try:
        handle = cast(Any, manager).get_turn_handle(trace_id)
        if handle is None:
            return error_route_result(
                HTTPStatus.NOT_FOUND,
                code="trace_not_found",
                message=f"Active trace not found: {trace_id}",
                details={"trace_id": trace_id},
                retryable=False,
            )
        return RouteResult(
            status=HTTPStatus.OK,
            payload={
                "ok": True,
                "trace_id": trace_id,
                "session_id": handle.session_id,
                "agent_id": handle.agent_id,
                "status": handle.current_phase_status(),
                "stream": {
                    "schema_version": handle.stream_schema_version,
                    "replay_floor_sequence": handle.replay_floor_sequence,
                },
            },
            session_id=handle.session_id or None,
            run_id=trace_id,
        )
    finally:
        if own_runtime:
            active_runtime.close()


__all__ = ["handle_turn_status"]
