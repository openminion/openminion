"""Session query helpers for the developer API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Mapping

from openminion.api.config import close_api_runtime_if_owned, resolve_api_runtime
from openminion.api.runtime import APIRuntime


@dataclass
class SessionQueryError(RuntimeError):
    message: str
    code: str = "invalid_request"

    def __str__(self) -> str:
        return self.message


def list_session_messages(
    config_path: Optional[str],
    *,
    session_id: str,
    limit: int = 100,
    runtime: Optional[APIRuntime] = None,
) -> Dict[str, Any]:
    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        raise SessionQueryError("`session_id` is required.", code="invalid_request")

    safe_limit = max(1, min(int(limit), 500))

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
        messages: List[Dict[str, Any]] = [
            {
                "id": record.id,
                "session_id": record.session_id,
                "role": record.role,
                "body": record.body,
                "metadata": record.metadata,
                "created_at": record.created_at,
            }
            for record in records
        ]
        return {
            "session": {
                "id": session.id,
                "channel": session.channel,
                "target": session.target,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
            },
            "messages": messages,
            "limit": safe_limit,
        }
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)


def append_session_event(
    config_path: Optional[str],
    *,
    session_id: str,
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    runtime: Optional[APIRuntime] = None,
) -> Dict[str, Any]:
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
