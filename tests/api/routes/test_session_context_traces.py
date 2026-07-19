from __future__ import annotations

from http import HTTPStatus
from types import SimpleNamespace

from openminion.api.routes.contracts import APIRouteContext
from openminion.api.routes.sessions import handle_request


class _SessionStore:
    def __init__(self, events: list[dict] | None = None) -> None:
        self._events = list(events or [])

    def get_session(self, session_id: str):
        if session_id == "missing":
            return None
        return SimpleNamespace(id=session_id)

    def list_events(self, session_id: str, *, event_type: str, limit: int, **kwargs):
        del session_id, kwargs
        return [
            event for event in self._events[:limit] if event["event_type"] == event_type
        ]


def _ctx(events: list[dict]) -> APIRouteContext:
    return APIRouteContext(
        config_path=None,
        runtime=SimpleNamespace(sessions=_SessionStore(events)),
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="req-test",
    )


def _event() -> dict:
    return {
        "id": "evt-1",
        "event_type": "context.manifest.created",
        "created_at": "2026-07-17T00:00:00Z",
        "payload": {
            "decision_trace": {
                "trace_version": "context-decision.v1",
                "session_id": "sess-1",
                "turn_id": "turn-1",
                "pack_version": "pack-1",
                "persistence_status": "persisted",
                "decisions": [
                    {
                        "segment_id": "retrieval:1",
                        "bucket": "retrieval",
                        "action": "included",
                        "reason_code": "selected",
                    }
                ],
            }
        },
    }


def test_context_traces_route_returns_persisted_trace() -> None:
    result = handle_request(
        _ctx([_event()]),
        method_name="GET",
        path="/sessions/sess-1/context-traces",
        body=None,
        query="turn_id=turn-1",
    )

    assert result is not None
    assert result.status == HTTPStatus.OK
    assert result.payload["ok"] is True
    assert result.payload["count"] == 1
    assert result.payload["traces"][0]["decision_trace"]["turn_id"] == "turn-1"


def test_context_traces_route_returns_404_without_durable_trace() -> None:
    result = handle_request(
        _ctx([]),
        method_name="GET",
        path="/sessions/sess-1/context-traces",
        body=None,
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.NOT_FOUND
    assert result.payload["error"]["code"] == "CONTEXT_TRACE_NOT_FOUND"


def test_context_traces_route_returns_404_for_missing_session() -> None:
    result = handle_request(
        _ctx([]),
        method_name="GET",
        path="/sessions/missing/context-traces",
        body=None,
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.NOT_FOUND
    assert result.payload["error"]["code"] == "session_not_found"
