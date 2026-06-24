from __future__ import annotations

import logging
from types import SimpleNamespace

from openminion.services.runtime import TurnRequest, TurnResponse
from openminion.services.runtime import daemon as runtime_daemon


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
