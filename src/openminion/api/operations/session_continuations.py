"""Transport-neutral API operations for local session continuation."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from openminion.api.routes.contracts import (
    APIRouteContext,
    RouteResult,
    error_route_result,
)
from openminion.modules.policy import is_local_gateway_host
from openminion.modules.session.diagnostics.continuation import (
    continuation_telemetry_sink,
)
from openminion.modules.session import SessionContinuationService
from openminion.modules.session.schemas import ContinuationError


def resolve_session_continuation_store(runtime: Any) -> Any:
    explicit = getattr(runtime, "session_continuation_store", None)
    if explicit is not None:
        return explicit
    from openminion.modules.brain.paths import resolve_brain_sessions_db_path
    from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
    from openminion.modules.storage.runtime.sqlite import resolve_database_path

    storage_path = resolve_database_path(runtime.config.storage.path)
    return SQLiteSessionStore(resolve_brain_sessions_db_path(storage_path=storage_path))


def handle_build_continuation(
    ctx: APIRouteContext,
    *,
    source_session_id: str,
    body: dict[str, Any] | None,
) -> RouteResult:
    denied = _local_runtime_error(ctx, session_id=source_session_id)
    if denied is not None:
        return denied
    payload = dict(body or {})
    target_agent_id = str(payload.get("target_agent_id") or "").strip()
    if not target_agent_id:
        return _invalid_continuation_request(
            source_session_id,
            "`target_agent_id` is required.",
        )
    store = resolve_session_continuation_store(ctx.runtime)
    owned_store = getattr(ctx.runtime, "session_continuation_store", None) is None
    service = SessionContinuationService(
        store,
        telemetry_sink=continuation_telemetry_sink(
            ctx.runtime,
            session_id=source_session_id,
        ),
    )
    try:
        if bool(payload.get("dry_run", False)):
            preview = service.preview(
                source_session_id,
                target_agent_id=target_agent_id,
                expires_in_seconds=int(payload.get("expires_in_seconds") or 86_400),
            )
            result = {
                "status": "previewed",
                "preview": preview.model_dump(mode="json"),
            }
        else:
            built = service.create(
                source_session_id,
                target_agent_id=target_agent_id,
                expires_in_seconds=int(payload.get("expires_in_seconds") or 86_400),
            )
            result = built.model_dump(mode="json")
    except (ContinuationError, TypeError, ValueError) as exc:
        return _continuation_error(exc, session_id=source_session_id)
    finally:
        if owned_store:
            store.close()
    return RouteResult(
        status=HTTPStatus.OK,
        payload={"ok": True, "continuation": result},
        session_id=source_session_id,
    )


def handle_apply_continuation(
    ctx: APIRouteContext,
    *,
    target_session_id: str,
    packet_id: str,
) -> RouteResult:
    denied = _local_runtime_error(ctx, session_id=target_session_id)
    if denied is not None:
        return denied
    store = resolve_session_continuation_store(ctx.runtime)
    owned_store = getattr(ctx.runtime, "session_continuation_store", None) is None
    service = SessionContinuationService(
        store,
        telemetry_sink=continuation_telemetry_sink(
            ctx.runtime,
            session_id=target_session_id,
        ),
    )
    try:
        applied = service.apply(target_session_id, packet_id=packet_id)
    except ContinuationError as exc:
        return _continuation_error(exc, session_id=target_session_id)
    finally:
        if owned_store:
            store.close()
    status = HTTPStatus.OK if applied.status != "rejected" else HTTPStatus.CONFLICT
    return RouteResult(
        status=status,
        payload={"ok": applied.status != "rejected", **applied.model_dump(mode="json")},
        session_id=target_session_id,
    )


def _local_runtime_error(
    ctx: APIRouteContext,
    *,
    session_id: str,
) -> RouteResult | None:
    if ctx.runtime is None:
        return error_route_result(
            HTTPStatus.SERVICE_UNAVAILABLE,
            code="runtime_unavailable",
            message="API runtime is unavailable.",
            retryable=True,
            session_id=session_id,
        )
    host = getattr(getattr(ctx.runtime.config, "gateway", None), "host", None)
    if is_local_gateway_host(host):
        return None
    return error_route_result(
        HTTPStatus.FORBIDDEN,
        code="external_api_continuation_disabled",
        message="Session continuation is enabled only on a local gateway bind.",
        retryable=False,
        session_id=session_id,
    )


def _continuation_error(exc: Exception, *, session_id: str) -> RouteResult:
    code = getattr(exc, "code", "invalid_continuation_request")
    status = (
        HTTPStatus.NOT_FOUND
        if code in {"continuation_source_not_found", "continuation_packet_not_found"}
        else HTTPStatus.BAD_REQUEST
    )
    return error_route_result(
        status,
        code=code,
        message=str(exc),
        retryable=False,
        session_id=session_id,
    )


def _invalid_continuation_request(session_id: str, message: str) -> RouteResult:
    return error_route_result(
        HTTPStatus.BAD_REQUEST,
        code="invalid_request",
        message=message,
        retryable=False,
        session_id=session_id,
    )


__all__ = [
    "handle_apply_continuation",
    "handle_build_continuation",
    "resolve_session_continuation_store",
]
