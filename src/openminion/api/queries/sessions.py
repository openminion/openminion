"""Session query helpers for the developer API."""

from dataclasses import dataclass
from typing import Any, Mapping

from openminion.api.config import close_api_runtime_if_owned, resolve_api_runtime
from openminion.api.runtime import APIRuntime
from openminion.base.config import resolve_agent_config
from openminion.modules.context.trace_inspection import (
    ContextTraceLookupError,
    list_context_traces,
)
from openminion.modules.telemetry.usage import RunStats


@dataclass
class SessionQueryError(RuntimeError):
    message: str
    code: str = "invalid_request"

    def __str__(self) -> str:
        return self.message


def list_session_messages(
    config_path: str | None,
    *,
    session_id: str,
    limit: int = 100,
    runtime: APIRuntime | None = None,
) -> dict[str, Any]:
    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        raise SessionQueryError("`session_id` is required.", code="invalid_request")

    safe_limit = max(1, min(limit, 500))

    active_runtime, own_runtime = resolve_api_runtime(
        config_path=config_path,
        runtime=runtime,
    )
    try:
        session = active_runtime.sessions.get_session(normalized_session_id)
        if session is None:
            raise SessionQueryError(
                f"Session '{normalized_session_id}' was not found.",
                code="session_not_found",
            )

        records = active_runtime.sessions.list_messages(
            session_id=normalized_session_id, limit=safe_limit
        )
        messages: list[dict[str, Any]] = []
        for record in records:
            stats = RunStats.from_message_metadata(record.metadata)
            messages.append(
                {
                    "id": record.id,
                    "session_id": record.session_id,
                    "role": record.role,
                    "body": record.body,
                    "metadata": record.metadata,
                    "stats": stats.as_payload() if stats is not None else {},
                    "created_at": record.created_at,
                }
            )
        return {
            "session": {
                "id": session.id,
                "channel": session.channel,
                "target": session.target,
                "turn_usage_display": resolve_agent_config(
                    active_runtime.config,
                    session.owner_agent_id,
                ).turn_usage_display,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
            },
            "messages": messages,
            "limit": safe_limit,
        }
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)


def list_session_events(
    config_path: str | None,
    *,
    session_id: str,
    after_id: int = 0,
    limit: int = 100,
    runtime: APIRuntime | None = None,
) -> dict[str, Any]:
    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        raise SessionQueryError("`session_id` is required.", code="invalid_request")
    if after_id < 0:
        raise SessionQueryError("`after_id` must not be negative.")

    safe_limit = max(1, min(limit, 1000))
    active_runtime, own_runtime = resolve_api_runtime(
        config_path=config_path,
        runtime=runtime,
    )
    try:
        session = active_runtime.sessions.get_session(normalized_session_id)
        if session is None:
            raise SessionQueryError(
                f"Session '{normalized_session_id}' was not found.",
                code="session_not_found",
            )
        high_water_id = active_runtime.sessions.event_high_water(
            session_id=normalized_session_id
        )
        records = active_runtime.sessions.list_events_after_id(
            session_id=normalized_session_id,
            after_id=after_id,
            high_water_id=high_water_id,
            limit=safe_limit,
        )
        events = [
            {
                "id": record.id,
                "session_id": record.session_id,
                "event_type": record.event_type,
                "created_at": record.created_at,
                "canonical_event_id": record.canonical_event_id,
            }
            for record in records
        ]
        return {
            "session_id": normalized_session_id,
            "events": events,
            "after_id": after_id,
            "next_after_id": records[-1].id if records else after_id,
            "high_water_id": high_water_id,
            "limit": safe_limit,
        }
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)


def append_session_event(
    config_path: str | None,
    *,
    session_id: str,
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    runtime: APIRuntime | None = None,
) -> dict[str, Any]:
    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        raise SessionQueryError("`session_id` is required.", code="invalid_request")
    normalized_event = str(event_type or "").strip()
    if not normalized_event:
        raise SessionQueryError("`event_type` is required.", code="invalid_request")

    active_runtime, own_runtime = resolve_api_runtime(
        config_path=config_path,
        runtime=runtime,
    )
    try:
        session = active_runtime.sessions.get_session(normalized_session_id)
        if session is None:
            raise SessionQueryError(
                f"Session '{normalized_session_id}' was not found.",
                code="session_not_found",
            )
        event = active_runtime.sessions.append_event(
            session_id=normalized_session_id,
            event_type=normalized_event,
            payload=dict(payload or {}),
        )
        return {
            "session_id": normalized_session_id,
            "event": {
                "id": event.id,
                "session_id": event.session_id,
                "event_type": event.event_type,
                "payload": event.payload,
                "created_at": event.created_at,
            },
        }
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)


def list_session_context_traces(
    config_path: str | None,
    *,
    session_id: str,
    trace_session_id: str | None = None,
    turn_id: str | None = None,
    limit: int = 50,
    runtime: APIRuntime | None = None,
) -> dict[str, Any]:
    active_runtime, own_runtime = resolve_api_runtime(
        config_path=config_path,
        runtime=runtime,
    )
    trace_store = getattr(active_runtime, "context_trace_store", None)
    own_trace_store = trace_store is None
    try:
        if active_runtime.sessions.get_session(session_id) is None:
            raise SessionQueryError(
                f"Session '{session_id}' was not found.",
                code="session_not_found",
            )
        if trace_store is None:
            from openminion.modules.brain.paths import resolve_brain_sessions_db_path
            from openminion.modules.session.runtime.factory import (
                build_module_session_store,
            )
            from openminion.modules.storage.engine import StorageEngineConfig

            session_path = resolve_brain_sessions_db_path(
                storage_path=active_runtime.storage_path
            )
            trace_store = build_module_session_store(
                config=StorageEngineConfig(
                    root_dir=session_path.parent,
                    sqlite_path=session_path,
                    fallback_root=session_path.parent,
                    record_backend=active_runtime.config.storage.record_backend(),
                    record_backend_options=(
                        active_runtime.config.storage.record_backend_options()
                    ),
                ),
                database_path=session_path,
                env=active_runtime.config_manager.env,
            )
        return list_context_traces(
            trace_store,
            session_id=str(trace_session_id or session_id).strip(),
            turn_id=turn_id,
            limit=limit,
        )
    except ContextTraceLookupError as exc:
        raise SessionQueryError(str(exc), code=exc.code) from exc
    finally:
        if own_trace_store and trace_store is not None:
            trace_store.close()
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)
