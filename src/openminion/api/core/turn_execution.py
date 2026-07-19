"""Shared turn-submission helpers for API sync and streaming flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from openminion.api.config import close_api_runtime_if_owned, resolve_api_runtime
from openminion.api.runtime import APIRuntime
from openminion.services.runtime.daemon import turn_chunk_to_dict, turn_response_to_dict


@dataclass
class TurnSubmission:
    manager: Any
    active_runtime: APIRuntime
    own_runtime: bool
    request: Any
    handle: Any
    timeout_s: float

    @property
    def session_id(self) -> Optional[str]:
        return getattr(self.request, "session_id", None)

    @property
    def run_id(self) -> str:
        trace_id = getattr(self.request, "trace_id", None)
        return str(trace_id or getattr(self.handle, "trace_id", "")).strip()


def open_turn_submission(
    *,
    config_path: Optional[str],
    runtime: Optional[APIRuntime],
    body: dict[str, Any],
) -> TurnSubmission:
    active_runtime, own_runtime = resolve_api_runtime(
        config_path=config_path,
        runtime=runtime,
    )
    try:
        runtime_handle = active_runtime.submit_turn(payload=body)
        return TurnSubmission(
            manager=getattr(active_runtime, "runtime_manager", None),
            active_runtime=active_runtime,
            own_runtime=own_runtime,
            request=runtime_handle.request,
            handle=runtime_handle,
            timeout_s=runtime_handle.timeout_s,
        )
    except Exception:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)
        raise


def collect_sync_turn_payload(
    submission: TurnSubmission,
    *,
    include_chunks: bool,
    chunk_timeout_s: float = 0.1,
) -> dict[str, Any]:
    turn_response = submission.handle.result(timeout_s=submission.timeout_s)
    payload: dict[str, Any] = {
        "trace_id": submission.run_id,
        "turn": {
            "trace_id": submission.run_id,
            **turn_response_to_dict(turn_response),
        },
    }
    if include_chunks:
        payload["chunks"] = [
            turn_chunk_to_dict(item)
            for item in submission.handle.stream(timeout_s=chunk_timeout_s)
        ]
    return payload


def close_submission(submission: TurnSubmission) -> None:
    close_api_runtime_if_owned(
        submission.active_runtime,
        own_runtime=submission.own_runtime,
    )
