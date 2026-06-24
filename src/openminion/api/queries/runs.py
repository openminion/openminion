"""Run query helpers for the developer API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from openminion.api.config import close_api_runtime_if_owned, resolve_api_runtime
from openminion.api.runtime import APIRuntime
from openminion.services.runtime.run_status import (
    list_session_run_events,
    list_session_runs,
)


@dataclass
class RunQueryError(RuntimeError):
    message: str
    code: str = "invalid_request"

    def __str__(self) -> str:
        return self.message


def list_runs(
    config_path: Optional[str],
    *,
    session_id: str,
    limit: int = 20,
    runtime: Optional[APIRuntime] = None,
) -> Dict[str, Any]:
    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        raise RunQueryError("`session_id` is required.", code="invalid_request")

    safe_limit = max(1, min(int(limit), 500))

    active_runtime, own_runtime = resolve_api_runtime(
        config_path=config_path,
        runtime=runtime,
    )
    try:
        session = active_runtime.sessions.get_session(normalized_session_id)
        if session is None:
            raise RunQueryError(
                f"Session '{normalized_session_id}' was not found.",
                code="session_not_found",
            )

        runs = list_session_runs(
            active_runtime.sessions,
            session_id=normalized_session_id,
            limit=safe_limit,
        )
        return {
            "session": {
                "id": session.id,
                "channel": session.channel,
                "target": session.target,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
            },
            "runs": [run.to_dict() for run in runs],
            "limit": safe_limit,
        }
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)


def list_run_events(
    config_path: Optional[str],
    *,
    session_id: str,
    run_id: str,
    limit: int = 200,
    runtime: Optional[APIRuntime] = None,
) -> Dict[str, Any]:
    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        raise RunQueryError("`session_id` is required.", code="invalid_request")

    normalized_run_id = run_id.strip()
    if not normalized_run_id:
        raise RunQueryError("`run_id` is required.", code="invalid_request")

    safe_limit = max(1, min(int(limit), 1000))

    active_runtime, own_runtime = resolve_api_runtime(
        config_path=config_path,
        runtime=runtime,
    )
    try:
        session = active_runtime.sessions.get_session(normalized_session_id)
        if session is None:
            raise RunQueryError(
                f"Session '{normalized_session_id}' was not found.",
                code="session_not_found",
            )

        events = list_session_run_events(
            active_runtime.sessions,
            session_id=normalized_session_id,
            run_id=normalized_run_id,
            limit=safe_limit,
        )
        if not events:
            raise RunQueryError(
                f"Run '{normalized_run_id}' was not found in session '{normalized_session_id}'.",
                code="run_not_found",
            )

        return {
            "session": {
                "id": session.id,
                "channel": session.channel,
                "target": session.target,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
            },
            "run_id": normalized_run_id,
            "events": [event.to_dict() for event in events],
            "limit": safe_limit,
        }
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)
