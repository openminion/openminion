from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from threading import Event
from types import SimpleNamespace

from openminion.api.routes.contracts import APIRouteContext
from openminion.api.routes.turns import handle_request
from openminion.services.runtime import AgentRuntimeManager, TurnRequest, TurnResponse
from openminion.services.runtime.turn_input import TurnInputQueue
from openminion.services.runtime.turn_input.lifecycle import (
    project_terminal_runtime_event,
)


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
        return SimpleNamespace(
            id=session_id,
            channel="console",
            target="api-user",
        )

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
        self.handle = _Handle(trace_id="trace-1", session_id="s1", agent_id="a1")

    def get_turn_handle(self, trace_id: str):
        return self.handle if trace_id == self.handle.trace_id else None

    def cancel_turn(self, trace_id: str) -> bool:
        self.cancelled.append(trace_id)
        return trace_id != "missing"


@dataclass(frozen=True)
class _Handle:
    trace_id: str
    session_id: str
    agent_id: str

    def result(self, timeout_s=None):  # noqa: ANN001
        del timeout_s
        return object()


@dataclass(frozen=True)
class _TimeoutHandle:
    trace_id: str
    session_id: str
    agent_id: str

    def result(self, timeout_s=None):  # noqa: ANN001
        raise TimeoutError(f"turn result timed out after {timeout_s}s")


class _Runtime:
    def __init__(self) -> None:
        ids = iter(["q1", "q2", "q3", "q4"])
        self.turn_input_queue = TurnInputQueue(
            max_pending_per_session=2,
            id_factory=lambda: next(ids),
        )
        self.sessions = _Sessions()
        self.runtime_manager = _Manager()
        self.config = SimpleNamespace(
            gateway=SimpleNamespace(api_turn_timeout_seconds=1)
        )
        self.submitted: list[dict] = []
        self.closed = False

    def submit_turn(self, *, payload: dict):
        self.submitted.append(dict(payload))
        return _Handle(
            trace_id=str(payload["trace_id"]),
            session_id=str(payload["session_id"]),
            agent_id=str(payload["agent_id"]),
        )

    def close(self) -> None:
        self.closed = True


class _FailingRuntime(_Runtime):
    def submit_turn(self, *, payload: dict):
        del payload
        raise RuntimeError("runtime stopped")


class _ManagedRuntime(_Runtime):
    def __init__(self, manager: AgentRuntimeManager) -> None:
        super().__init__()
        self.runtime_manager = manager

    def submit_turn(self, *, payload: dict):
        return self.runtime_manager.submit_turn(
            TurnRequest(
                trace_id=str(payload["trace_id"]),
                agent_id=str(payload["agent_id"]),
                session_id=str(payload["session_id"]),
                input_text=str(payload["input_text"]),
                meta={"channel": payload["channel"], "user": payload["user"]},
            )
        )


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


def test_cancel_and_run_next_dispatches_head_and_reports_conflict() -> None:
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
    assert runtime.submitted == [
        {
            "trace_id": result.payload["next_trace_id"],
            "input_text": "next",
            "agent_id": "a1",
            "session_id": "s1",
            "channel": "console",
            "user": "api-user",
        }
    ]
    assert result.payload["entry"]["status"] == "running"
    assert [event.event_type for event in runtime.sessions.events[-3:]] == [
        "turn_input.cancel_requested",
        "turn_input.cancel_acknowledged",
        "turn_input.running",
    ]

    handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/sessions/s1/turn-inputs",
        body={"agent_id": "a1", "text": "another"},
        query=None,
    )
    changed = handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/turn/trace-1/cancel-and-run-next",
        body={"session_id": "s1", "agent_id": "a1", "expected_queue_id": "other"},
        query=None,
    )
    assert changed is not None
    assert changed.status == HTTPStatus.CONFLICT


def test_cancel_and_run_next_releases_reservation_when_dispatch_fails() -> None:
    runtime = _FailingRuntime()
    handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/sessions/s1/turn-inputs",
        body={"agent_id": "a1", "text": "next"},
        query=None,
    )

    result = handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/turn/trace-1/cancel-and-run-next",
        body={"session_id": "s1", "agent_id": "a1"},
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.CONFLICT
    assert result.payload["error"]["code"] == "QUEUE_DISPATCH_FAILED"
    entry = runtime.turn_input_queue.list_entries(session_id="s1")[0]
    assert entry.status.value == "queued"
    assert runtime.sessions.events[-1].event_type == "turn_input.requeued"


def test_cancel_and_run_next_requeues_when_cancellation_does_not_settle() -> None:
    runtime = _Runtime()
    runtime.runtime_manager.handle = _TimeoutHandle(
        trace_id="trace-1",
        session_id="s1",
        agent_id="a1",
    )
    handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/sessions/s1/turn-inputs",
        body={"agent_id": "a1", "text": "next"},
        query=None,
    )

    result = handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/turn/trace-1/cancel-and-run-next",
        body={"session_id": "s1", "agent_id": "a1"},
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.GATEWAY_TIMEOUT
    assert result.payload["error"]["code"] == "QUEUE_CANCEL_SETTLEMENT_TIMEOUT"
    assert runtime.turn_input_queue.list_entries(session_id="s1")[0].status.value == (
        "queued"
    )


def test_cancel_and_run_next_rejects_wrong_queue_scope() -> None:
    runtime = _Runtime()
    handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/sessions/s1/turn-inputs",
        body={"agent_id": "a1", "text": "next"},
        query=None,
    )

    result = handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/turn/trace-1/cancel-and-run-next",
        body={"session_id": "s1", "agent_id": "other"},
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.CONFLICT
    assert runtime.runtime_manager.cancelled == []
    assert runtime.turn_input_queue.list_entries(session_id="s1")[0].status.value == (
        "queued"
    )


def test_cancel_and_run_next_waits_for_terminal_before_next_turn() -> None:
    first_started = Event()
    turn_order: list[str] = []

    def execute(request, _emit, cancel_event):  # noqa: ANN001
        turn_order.append(f"start:{request.input_text}")
        if request.input_text == "current":
            first_started.set()
            assert cancel_event.wait(timeout=2)
            turn_order.append("terminal:current")
        return TurnResponse(final_text=request.input_text)

    manager = AgentRuntimeManager(turn_executor=execute)
    runtime = _ManagedRuntime(manager)
    current = manager.submit_turn(
        TurnRequest(
            trace_id="trace-current",
            agent_id="a1",
            session_id="s1",
            input_text="current",
        )
    )
    assert first_started.wait(timeout=2)
    handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/sessions/s1/turn-inputs",
        body={"agent_id": "a1", "text": "next"},
        query=None,
    )

    try:
        result = handle_request(
            _ctx(runtime),
            method_name="POST",
            path="/v1/turn/trace-current/cancel-and-run-next",
            body={"session_id": "s1", "agent_id": "a1"},
            query=None,
        )
        assert result is not None
        assert result.status == HTTPStatus.ACCEPTED
        next_handle = manager.get_turn_handle(result.payload["next_trace_id"])
        assert next_handle is not None
        next_handle.result(timeout_s=2)
    finally:
        current.result(timeout_s=2)
        manager.shutdown()

    assert turn_order == ["start:current", "terminal:current", "start:next"]


def test_cancel_and_run_next_advances_from_running_queue_entry() -> None:
    runtime = _Runtime()
    first = runtime.turn_input_queue.enqueue(
        session_id="s1",
        agent_id="a1",
        text="current",
    )
    runtime.turn_input_queue.enqueue(
        session_id="s1",
        agent_id="a1",
        text="next",
    )
    runtime.turn_input_queue.reserve_next(session_id="s1", agent_id="a1")
    runtime.turn_input_queue.mark_running(
        queue_id=first.queue_id,
        trace_id="trace-1",
    )

    result = handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/turn/trace-1/cancel-and-run-next",
        body={"session_id": "s1", "agent_id": "a1"},
        query=None,
    )

    assert result is not None
    assert result.status == HTTPStatus.ACCEPTED
    assert result.payload["entry"]["queue_id"] == "q2"
    assert result.payload["entry"]["status"] == "running"


def test_cancel_and_run_next_records_fast_completion() -> None:
    holder = {}

    def on_runtime_event(event_type: str, payload: dict) -> None:
        runtime = holder["runtime"]
        project_terminal_runtime_event(
            queue=runtime.turn_input_queue,
            sessions=runtime.sessions,
            event_type=event_type,
            payload=payload,
        )

    current_started = Event()

    def execute(request, _emit, cancel_event):  # noqa: ANN001
        if request.input_text == "current":
            current_started.set()
            cancel_event.wait(timeout=2)
        return TurnResponse(final_text=request.input_text)

    manager = AgentRuntimeManager(
        turn_executor=execute,
        on_runtime_event=on_runtime_event,
    )
    runtime = _ManagedRuntime(manager)
    holder["runtime"] = runtime
    current = manager.submit_turn(
        TurnRequest(
            trace_id="trace-current",
            agent_id="a1",
            session_id="s1",
            input_text="current",
        )
    )
    assert current_started.wait(timeout=2)
    handle_request(
        _ctx(runtime),
        method_name="POST",
        path="/v1/sessions/s1/turn-inputs",
        body={"agent_id": "a1", "text": "next"},
        query=None,
    )

    try:
        result = handle_request(
            _ctx(runtime),
            method_name="POST",
            path="/v1/turn/trace-current/cancel-and-run-next",
            body={"session_id": "s1", "agent_id": "a1"},
            query=None,
        )
        assert result is not None
        next_handle = manager.get_turn_handle(result.payload["next_trace_id"])
        if next_handle is not None:
            next_handle.result(timeout_s=2)
    finally:
        current.result(timeout_s=2)
        manager.shutdown()

    entry = runtime.turn_input_queue.list_entries(session_id="s1")[0]
    assert entry.status.value == "completed"
    assert [event.event_type for event in runtime.sessions.events].count(
        "turn_input.completed"
    ) == 1
