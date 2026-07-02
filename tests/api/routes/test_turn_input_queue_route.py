from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus

from openminion.api.routes.base import APIRouteContext
from openminion.api.routes.turns import handle_request
from openminion.services.runtime.turn_input import TurnInputQueue


@dataclass(frozen=True)
class _Event:
    id: str
    session_id: str
    event_type: str
    payload: dict
    created_at: str = "2026-07-01T00:00:00Z"


class _Sessions:
    def __init__(self) -> None:
        self.events: list[_Event] = []

    def get_session(self, session_id: str):
        return {"id": session_id}

    def append_event(self, *, session_id: str, event_type: str, payload: dict):
        event = _Event(
            id=f"ev-{len(self.events) + 1}",
            session_id=session_id,
            event_type=event_type,
            payload=dict(payload),
        )
        self.events.append(event)
        return event


class _Manager:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    def cancel_turn(self, trace_id: str) -> bool:
        self.cancelled.append(trace_id)
        return trace_id != "missing"


class _Runtime:
    def __init__(self) -> None:
        ids = iter(["q1", "q2", "q3", "q4"])
        self.turn_input_queue = TurnInputQueue(
            max_pending_per_session=2,
            id_factory=lambda: next(ids),
        )
        self.sessions = _Sessions()
        self.runtime_manager = _Manager()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _ctx(runtime: _Runtime) -> APIRouteContext:
    return APIRouteContext(
        config_path=None,
        runtime=runtime,  # type: ignore[arg-type]
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="req-1",
    )


def test_enqueue_and_list_turn_inputs_with_session_event() -> None:
    runtime = _Runtime()

    result = handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/sessions/s1/turn-inputs",
        body={"agent_id": "a1", "text": "next question", "source_client": "api"},
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.ACCEPTED
    assert result.payload["entry"]["queue_id"] == "q1"
    assert result.payload["entry"]["status"] == "queued"
    assert runtime.sessions.events[-1].event_type == "turn_input.enqueued"
    assert "text" not in runtime.sessions.events[-1].payload
    assert runtime.sessions.events[-1].payload["text_preview"] == "next question"

    listed = handle_request(
        _ctx(runtime),
        method_name="GET",
        path="/v1/sessions/s1/turn-inputs",
        body=None,
        query=None,
    )

    assert listed is not None
    assert listed.status == HTTPStatus.OK
    assert [entry["queue_id"] for entry in listed.payload["entries"]] == ["q1"]


def test_drop_turn_input_uses_status_version_and_records_event() -> None:
    runtime = _Runtime()
    created = handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/sessions/s1/turn-inputs",
        body={"agent_id": "a1", "text": "drop me"},
        query=None,
    )
    assert created is not None
    entry = created.payload["entry"]

    stale = handle_request(
        _ctx(runtime),
        method_name="DELETE",
        path="/v1/sessions/s1/turn-inputs/q1",
        body={"status_version": 99},
        query=None,
    )
    assert stale is not None
    assert stale.status == HTTPStatus.CONFLICT
    assert stale.payload["error"]["code"] == "QUEUE_CONFLICT"

    dropped = handle_request(
        _ctx(runtime),
        method_name="DELETE",
        path="/v1/sessions/s1/turn-inputs/q1",
        body={"status_version": entry["status_version"]},
        query=None,
    )
    assert dropped is not None
    assert dropped.status == HTTPStatus.OK
    assert dropped.payload["entry"]["status"] == "dropped"
    assert runtime.sessions.events[-1].event_type == "turn_input.dropped"


def test_move_turn_input_request_body_and_conflict_handling() -> None:
    runtime = _Runtime()
    for text in ("first", "second"):
        handle_request(
            _ctx(runtime),
            method_name="POST",
            path="/v1/sessions/s1/turn-inputs",
            body={"agent_id": "a1", "text": text},
            query=None,
        )

    conflict = handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/sessions/s1/turn-inputs/q2/move",
        body={"status_version": 99, "before_queue_id": "q1"},
        query=None,
    )
    assert conflict is not None
    assert conflict.status == HTTPStatus.CONFLICT

    moved = handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/sessions/s1/turn-inputs/q2/move",
        body={"status_version": 1, "before_queue_id": "q1"},
        query=None,
    )
    assert moved is not None
    assert moved.status == HTTPStatus.OK
    assert moved.payload["entry"]["queue_id"] == "q2"
    assert runtime.sessions.events[-1].event_type == "turn_input.moved"

    listed = handle_request(
        _ctx(runtime),
        method_name="GET",
        path="/v1/sessions/s1/turn-inputs",
        body=None,
        query=None,
    )
    assert listed is not None
    assert [entry["queue_id"] for entry in listed.payload["entries"]] == ["q2", "q1"]


def test_queue_full_returns_structured_error() -> None:
    runtime = _Runtime()
    for text in ("first", "second"):
        handle_request(
            _ctx(runtime),
            method_name="POST",
            path="/v1/sessions/s1/turn-inputs",
            body={"agent_id": "a1", "text": text},
            query=None,
        )

    result = handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/sessions/s1/turn-inputs",
        body={"agent_id": "a1", "text": "third"},
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.TOO_MANY_REQUESTS
    assert result.payload["error"]["code"] == "QUEUE_FULL"


def test_steer_current_is_deferred_and_queued_for_next_turn() -> None:
    runtime = _Runtime()

    result = handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/sessions/s1/turn-inputs",
        body={"agent_id": "a1", "text": "steer", "intent": "steer_current"},
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.ACCEPTED
    assert result.payload["entry"]["status"] == "queued"
    assert result.payload["entry"]["metadata"]["steer_status"] == "steer_deferred"
    assert [event.event_type for event in runtime.sessions.events] == [
        "turn_input.steer_deferred",
        "turn_input.enqueued",
    ]


def test_cancel_and_run_next_reserves_head_and_reports_conflict() -> None:
    runtime = _Runtime()
    created = handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/sessions/s1/turn-inputs",
        body={"agent_id": "a1", "text": "next"},
        query=None,
    )
    assert created is not None
    queue_id = created.payload["entry"]["queue_id"]

    result = handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/turn/trace-1/cancel-and-run-next",
        body={"session_id": "s1", "agent_id": "a1", "expected_queue_id": queue_id},
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.ACCEPTED
    assert runtime.runtime_manager.cancelled == ["trace-1"]
    assert result.payload["reserved_entry"]["status"] == "reserved"
    assert runtime.sessions.events[-2].event_type == "turn_input.cancel_requested"
    assert runtime.sessions.events[-1].event_type == "turn_input.cancel_acknowledged"

    changed = handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/turn/trace-2/cancel-and-run-next",
        body={"session_id": "s1", "agent_id": "a1", "expected_queue_id": "other"},
        query=None,
    )
    assert changed is not None
    assert changed.status == HTTPStatus.CONFLICT
