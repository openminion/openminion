"""Transport-neutral API operations for local session continuation."""

import re
from http import HTTPStatus
from typing import Any
from urllib.parse import unquote

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
from openminion.modules.session.schemas import ContinuationError, RoomHandoffBinding
from openminion.modules.storage import is_room_session_key

_CONTINUATIONS_RE = re.compile(r"(?:/v1)?/sessions/([^/]+)/continuations")
_CONTINUATION_APPLY_RE = re.compile(
    r"(?:/v1)?/sessions/([^/]+)/continuations/([^/]+)/apply"
)
_ROOM_HANDOFFS_RE = re.compile(r"(?:/v1)?/rooms/([^/]+)/handoffs")


def maybe_handle_session_continuation_request(
    ctx: APIRouteContext,
    *,
    method_name: str,
    path: str,
    body: dict[str, Any] | None,
) -> RouteResult | None:
    if method_name != "POST":
        return None
    if (handoff := _ROOM_HANDOFFS_RE.fullmatch(path)) is not None:
        return handle_build_room_handoff(
            ctx,
            source_room_session_id=unquote(handoff.group(1)),
            body=body,
        )
    if (apply := _CONTINUATION_APPLY_RE.fullmatch(path)) is not None:
        return handle_apply_continuation(
            ctx,
            target_session_id=unquote(apply.group(1)),
            packet_id=unquote(apply.group(2)),
        )
    if (build := _CONTINUATIONS_RE.fullmatch(path)) is not None:
        return handle_build_continuation(
            ctx,
            source_session_id=unquote(build.group(1)),
            body=body,
        )
    return None


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
    payload = body or {}
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


def handle_build_room_handoff(
    ctx: APIRouteContext,
    *,
    source_room_session_id: str,
    body: dict[str, Any] | None,
) -> RouteResult:
    denied = _local_runtime_error(ctx, session_id=source_room_session_id)
    if denied is not None:
        return denied
    payload = body or {}
    target_agent_id = str(payload.get("target_agent_id") or "").strip()
    if not target_agent_id:
        return _invalid_continuation_request(
            source_room_session_id,
            "`target_agent_id` is required.",
        )
    target_session_id = str(payload.get("target_session_id") or "").strip()
    if not target_session_id:
        return _invalid_continuation_request(
            source_room_session_id,
            "`target_session_id` is required.",
        )
    task_step_id = str(payload.get("task_step_id") or "").strip()
    if not task_step_id:
        return _invalid_continuation_request(
            source_room_session_id,
            "`task_step_id` is required.",
        )
    store = resolve_session_continuation_store(ctx.runtime)
    owned_store = getattr(ctx.runtime, "session_continuation_store", None) is None
    service = SessionContinuationService(
        store,
        telemetry_sink=continuation_telemetry_sink(
            ctx.runtime,
            session_id=source_room_session_id,
        ),
    )
    try:
        binding = _room_handoff_binding(
            ctx.runtime,
            room_session_id=source_room_session_id,
            target_agent_id=target_agent_id,
            target_session_id=target_session_id,
            task_step_id=task_step_id,
        )
        if bool(payload.get("dry_run", False)):
            result: dict[str, Any] = {
                "status": "previewed",
                "preview": service.preview_room_handoff(
                    binding,
                    expires_in_seconds=int(payload.get("expires_in_seconds") or 86_400),
                ).model_dump(mode="json"),
            }
        else:
            result = service.create_room_handoff(
                binding,
                expires_in_seconds=int(payload.get("expires_in_seconds") or 86_400),
            ).model_dump(mode="json")
    except (ContinuationError, TypeError, ValueError) as exc:
        return _continuation_error(exc, session_id=source_room_session_id)
    finally:
        if owned_store:
            store.close()
    return RouteResult(
        status=HTTPStatus.OK,
        payload={"ok": True, "continuation": result},
        session_id=source_room_session_id,
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
        packet = service.get_packet(packet_id)
        binding = None
        if packet.payload.continuation_kind == "room_agent_handoff":
            try:
                binding = _room_handoff_binding(
                    ctx.runtime,
                    room_session_id=packet.source_session_id,
                    target_agent_id=packet.payload.target_agent_id,
                    target_session_id=target_session_id,
                    task_step_id=(
                        packet.payload.room_handoff_binding.task_step_id
                        if packet.payload.room_handoff_binding is not None
                        else ""
                    ),
                )
            except ContinuationError:
                binding = None
        applied = service.apply(
            target_session_id,
            packet_id=packet_id,
            room_binding=binding,
        )
    except (ContinuationError, TypeError, ValueError) as exc:
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


def _room_handoff_binding(
    runtime: Any,
    *,
    room_session_id: str,
    target_agent_id: str,
    target_session_id: str,
    task_step_id: str = "",
) -> RoomHandoffBinding:
    sessions = runtime.sessions
    room = sessions.get_session(room_session_id)
    if room is None or not is_room_session_key(str(room.session_key or "")):
        raise ContinuationError("continuation_room_source_required")
    local_human_id = str(room.metadata.get("local_human_id") or "").strip()
    owner = sessions.get_participant(room_session_id, "human", local_human_id)
    if not local_human_id or owner is None or owner.role != "owner":
        raise ContinuationError("continuation_room_owner_required")
    source_agent_id = str(room.active_agent_id or "").strip()
    source = sessions.get_participant(room_session_id, "agent", source_agent_id)
    target = sessions.get_participant(room_session_id, "agent", target_agent_id)
    if source is None:
        raise ContinuationError("continuation_room_source_agent_inactive")
    if target is None:
        raise ContinuationError("continuation_room_target_agent_inactive")
    if source_agent_id == target_agent_id:
        raise ContinuationError("continuation_room_target_must_be_distinct")
    return RoomHandoffBinding(
        room_session_id=room_session_id,
        local_human_authority_id=local_human_id,
        source_agent_id=source_agent_id,
        target_agent_id=target_agent_id,
        target_session_id=target_session_id,
        task_step_id=task_step_id,
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
    "handle_build_room_handoff",
    "maybe_handle_session_continuation_request",
    "resolve_session_continuation_store",
]
