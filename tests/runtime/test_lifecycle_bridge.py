from __future__ import annotations

import logging
from threading import Event, Thread
from types import SimpleNamespace

from openminion.services.runtime import AgentRuntimeManager, TurnRequest, TurnResponse
from openminion.services.runtime import daemon as runtime_daemon
from openminion.services.runtime.turn_input import (
    TurnInputQueue,
    TurnInputQueueStatus,
)
from openminion.services.runtime.turn_input.lifecycle import (
    project_terminal_runtime_event,
)


def test_build_runtime_manager_records_canonical_lifecycle_events(
    monkeypatch,
    tmp_path,
) -> None:
    recorded = []

    class FakeTelemetryService:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def record_event_sync(self, event) -> None:
            recorded.append(event)

        def close_sync(self) -> None:
            return None

    def _fake_execute_turn(*, runtime, request, emit_chunk, cancel_event):  # noqa: ANN001
        del runtime, request, emit_chunk, cancel_event
        return TurnResponse(final_text="ok")

    monkeypatch.setattr(runtime_daemon, "TelemetryService", FakeTelemetryService)
    monkeypatch.setattr(runtime_daemon, "execute_turn", _fake_execute_turn)

    runtime = SimpleNamespace(
        logger=logging.getLogger("tests.runtime.lifecycle"),
        home_root=tmp_path,
        config=SimpleNamespace(runtime=SimpleNamespace(env={})),
        evict_agent_runtime=lambda agent_id, reason: None,
        turn_input_queue=TurnInputQueue(),
        sessions=SimpleNamespace(append_event=lambda **_kwargs: None),
    )

    manager = runtime_daemon.build_runtime_manager(runtime)
    try:
        handle = manager.submit_turn(
            TurnRequest(
                trace_id="trace-1",
                agent_id="agent-lifecycle",
                session_id="sess-1",
                input_text="hello",
            )
        )
        handle.result(timeout_s=3)
    finally:
        manager.shutdown()

    event_types = [event.event_type for event in recorded]
    assert "component.started" in event_types
    assert "component.heartbeat" in event_types
    assert "component.stopped" in event_types

    started = next(
        event
        for event in recorded
        if event.data.get("component", {}).get("component_kind") == "agent_runtime"
        and event.data.get("component", {}).get("component_id") == "agent-lifecycle"
        and event.event_type == "component.started"
    )
    assert started.data["component"]["component_kind"] == "agent_runtime"
    assert started.data["component"]["component_id"] == "agent-lifecycle"
    assert started.data["source_classification"] == "native_canonical"

    stopped = next(
        event
        for event in recorded
        if event.data.get("component", {}).get("component_kind") == "runtime_manager"
        and event.event_type == "component.stopped"
    )
    assert stopped.data["component"]["component_kind"] == "runtime_manager"
    assert stopped.event_type == "component.stopped"
    assert stopped.data["source_classification"] == "native_canonical"


def test_turn_input_projection_records_each_terminal_status_once() -> None:
    cases = (
        ("runtime.turn.completed", TurnInputQueueStatus.COMPLETED),
        ("runtime.turn.failed", TurnInputQueueStatus.FAILED),
        ("runtime.turn.cancelled", TurnInputQueueStatus.CANCELLED),
    )
    for event_type, status in cases:
        queue = TurnInputQueue(id_factory=lambda: "q1")
        queued = queue.enqueue(session_id="sess-1", agent_id="agent-1", text="next")
        queue.reserve_next(session_id="sess-1", agent_id="agent-1")
        queue.mark_running(queue_id=queued.queue_id, trace_id="trace-next")
        events: list[dict] = []
        sessions = SimpleNamespace(
            append_event=lambda **kwargs: events.append(dict(kwargs))
        )

        project_terminal_runtime_event(
            queue=queue,
            sessions=sessions,
            event_type="runtime.turn.cancelled",
            payload={"trace_id": "trace-next", "terminal": False},
        )
        project_terminal_runtime_event(
            queue=queue,
            sessions=sessions,
            event_type=event_type,
            payload={"trace_id": "trace-next", "terminal": True},
        )
        project_terminal_runtime_event(
            queue=queue,
            sessions=sessions,
            event_type=event_type,
            payload={"trace_id": "trace-next", "terminal": True},
        )

        entry = queue.list_entries(session_id="sess-1")[0]
        assert entry.status == status
        assert len(events) == 1
        assert events[0]["payload"] == entry.event_payload()


def test_turn_input_audit_failure_does_not_stop_runtime_worker() -> None:
    queue = TurnInputQueue(id_factory=iter(("q1", "q2")).__next__)
    for trace_id in ("trace-1", "trace-2"):
        queued = queue.enqueue(
            session_id="sess-1",
            agent_id="agent-1",
            text=trace_id,
        )
        queue.reserve_next(session_id="sess-1", agent_id="agent-1")
        queue.mark_running(queue_id=queued.queue_id, trace_id=trace_id)

    def append_event(**_kwargs) -> None:
        raise OSError("session store unavailable")

    def on_runtime_event(event_type: str, payload: dict) -> None:
        project_terminal_runtime_event(
            queue=queue,
            sessions=SimpleNamespace(append_event=append_event),
            event_type=event_type,
            payload=payload,
        )

    manager = AgentRuntimeManager(
        turn_executor=lambda request, _emit, _cancel: TurnResponse(
            final_text=request.input_text
        ),
        on_runtime_event=on_runtime_event,
    )
    first = manager.submit_turn(TurnRequest("trace-1", "agent-1", "sess-1", "first"))
    second = manager.submit_turn(TurnRequest("trace-2", "agent-1", "sess-1", "second"))
    try:
        assert first.result(timeout_s=2).final_text == "first"
        assert second.result(timeout_s=2).final_text == "second"
    finally:
        manager.shutdown()

    assert [entry.status for entry in queue.list_entries(session_id="sess-1")] == [
        TurnInputQueueStatus.COMPLETED,
        TurnInputQueueStatus.COMPLETED,
    ]


def test_shutdown_terminalizes_queued_turn_input() -> None:
    queue = TurnInputQueue(id_factory=lambda: "q1")
    queued = queue.enqueue(session_id="sess-1", agent_id="agent-1", text="next")
    queue.reserve_next(session_id="sess-1", agent_id="agent-1")
    queue.mark_running(queue_id=queued.queue_id, trace_id="trace-next")
    events: list[dict] = []
    active_started = Event()

    def execute(request, _emit, cancel_event):  # noqa: ANN001
        if request.trace_id == "trace-active":
            active_started.set()
            cancel_event.wait(timeout=2)
        return TurnResponse(final_text=request.input_text)

    def on_runtime_event(event_type: str, payload: dict) -> None:
        project_terminal_runtime_event(
            queue=queue,
            sessions=SimpleNamespace(
                append_event=lambda **kwargs: events.append(dict(kwargs))
            ),
            event_type=event_type,
            payload=payload,
        )

    manager = AgentRuntimeManager(
        turn_executor=execute,
        on_runtime_event=on_runtime_event,
    )
    manager.submit_turn(TurnRequest("trace-active", "agent-1", "sess-1", "active"))
    assert active_started.wait(timeout=2)
    pending = manager.submit_turn(
        TurnRequest("trace-next", "agent-1", "sess-1", "next")
    )

    manager.shutdown()

    assert pending.result(timeout_s=2).errors[0].code == "cancelled"
    assert queue.list_entries(session_id="sess-1")[0].status == (
        TurnInputQueueStatus.CANCELLED
    )
    assert [event["event_type"] for event in events] == ["turn_input.cancelled"]


def test_forced_eviction_terminalizes_queued_turn_input() -> None:
    queue = TurnInputQueue(id_factory=lambda: "q1")
    queued = queue.enqueue(session_id="sess-1", agent_id="agent-1", text="next")
    queue.reserve_next(session_id="sess-1", agent_id="agent-1")
    queue.mark_running(queue_id=queued.queue_id, trace_id="trace-next")
    active_started = Event()
    release_active = Event()

    def execute(request, _emit, _cancel_event):  # noqa: ANN001
        if request.trace_id == "trace-active":
            active_started.set()
            release_active.wait(timeout=2)
        return TurnResponse(final_text=request.input_text)

    def on_runtime_event(event_type: str, payload: dict) -> None:
        project_terminal_runtime_event(
            queue=queue,
            sessions=SimpleNamespace(append_event=lambda **_kwargs: None),
            event_type=event_type,
            payload=payload,
        )

    manager = AgentRuntimeManager(
        turn_executor=execute,
        on_runtime_event=on_runtime_event,
    )
    manager.submit_turn(TurnRequest("trace-active", "agent-1", "sess-1", "active"))
    assert active_started.wait(timeout=2)
    pending = manager.submit_turn(
        TurnRequest("trace-next", "agent-1", "sess-1", "next")
    )
    evict_thread = Thread(target=lambda: manager.evict("agent-1", "test"))
    evict_thread.start()
    try:
        assert pending.result(timeout_s=2).errors[0].code == "evicted"
    finally:
        release_active.set()
        evict_thread.join(timeout=2)
        manager.shutdown()

    assert queue.list_entries(session_id="sess-1")[0].status == (
        TurnInputQueueStatus.FAILED
    )


def test_lifecycle_bridge_emits_single_info_owner_for_canonical_events(
    monkeypatch,
    tmp_path,
) -> None:
    recorded = []
    lifecycle_logs: list[str] = []
    runtime_logs: list[str] = []
    cron_logs: list[str] = []

    class FakeTelemetryService:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def record_event_sync(self, event) -> None:
            recorded.append(event)

        def close_sync(self) -> None:
            return None

    monkeypatch.setattr(runtime_daemon, "TelemetryService", FakeTelemetryService)
    monkeypatch.setattr(runtime_daemon._LIFECYCLE_LOGGER, "info", lifecycle_logs.append)
    monkeypatch.setattr(runtime_daemon._RUNTIMECTL_LOGGER, "info", runtime_logs.append)
    monkeypatch.setattr(runtime_daemon._CRONCTL_LOGGER, "info", cron_logs.append)
    runtime = SimpleNamespace(
        logger=logging.getLogger("tests.runtime.lifecycle.single_owner"),
        home_root=tmp_path,
        config=SimpleNamespace(runtime=SimpleNamespace(env={})),
        evict_agent_runtime=lambda agent_id, reason: None,
    )

    bridge = runtime_daemon._LifecycleTelemetryBridge(runtime)
    try:
        bridge.handle_runtime_event(
            "component.heartbeat",
            {
                "component": {
                    "component_kind": "runtime_manager",
                    "component_id": "primary",
                    "scope": "system",
                    "owner_module": "openminion-runtime",
                },
                "module_id": "openminion-runtime",
                "session_id": "lifecycle:runtime_manager:primary",
                "turn_id": "runtime_manager:primary:heartbeat:1",
                "status": "ok",
                "reason": "heartbeat",
                "source_classification": "native_canonical",
            },
        )
        bridge.handle_cron_event(
            "cron.scheduler.heartbeat",
            {
                "daemon_id": "daemon-1",
                "daemon_component_id": "primary",
                "active_runs": 0,
            },
        )
        bridge.handle_runtime_event(
            "runtime.turn.enqueued",
            {"trace_id": "trace-1", "agent_id": "agent-1"},
        )
    finally:
        bridge.close()

    assert len(lifecycle_logs) == 2
    assert any("event=component.heartbeat" in entry for entry in lifecycle_logs)
    assert any("source=cron.scheduler.heartbeat" in entry for entry in lifecycle_logs)
    assert runtime_logs == [
        'event=runtime.turn.enqueued payload={"agent_id": "agent-1", "trace_id": "trace-1"}'
    ]
    assert cron_logs == []
    assert len(recorded) == 2


def test_lifecycle_bridge_warns_instead_of_raising_on_sink_failure(
    caplog,
    tmp_path,
) -> None:
    def _fail(_event) -> None:
        raise RuntimeError("telemetry is closed")

    telemetry = SimpleNamespace(record_event_sync=_fail)
    runtime = SimpleNamespace(
        telemetry_service=telemetry,
        logger=logging.getLogger("tests.runtime.lifecycle.sink_failure"),
        home_root=tmp_path,
        config=SimpleNamespace(runtime=SimpleNamespace(env={})),
    )
    bridge = runtime_daemon._LifecycleTelemetryBridge(runtime)

    with caplog.at_level(logging.WARNING):
        bridge._record(  # noqa: SLF001
            SimpleNamespace(event_type="component.heartbeat", data={})
        )

    assert "lifecycle telemetry emit failed" in caplog.text
    assert "component.heartbeat" in caplog.text


def test_lifecycle_bridge_ignores_events_after_close(tmp_path) -> None:
    recorded = []
    telemetry = SimpleNamespace(record_event_sync=recorded.append)
    runtime = SimpleNamespace(
        telemetry_service=telemetry,
        home_root=tmp_path,
        config=SimpleNamespace(runtime=SimpleNamespace(env={})),
    )
    bridge = runtime_daemon._LifecycleTelemetryBridge(runtime)

    bridge.close()
    bridge._record(  # noqa: SLF001
        SimpleNamespace(event_type="component.heartbeat", data={})
    )

    assert recorded == []
