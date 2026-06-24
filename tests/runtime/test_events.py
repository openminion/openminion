from __future__ import annotations

from time import sleep
from typing import Any

from openminion.services.runtime import AgentRuntimeManager, TurnRequest, TurnResponse


def _collect_events() -> tuple[list[tuple[str, dict]], Any]:
    events: list[tuple[str, dict]] = []

    def hook(event_type: str, payload: dict) -> None:
        events.append((event_type, dict(payload)))

    return events, hook


def _simple_executor(req, emit_chunk, cancel_event):  # noqa: ANN001
    del emit_chunk, cancel_event
    sleep(0.01)
    return TurnResponse(final_text=f"ok:{req.trace_id}")


def test_turn_enqueued_event() -> None:
    events, hook = _collect_events()
    manager = AgentRuntimeManager(
        turn_executor=_simple_executor,
        on_runtime_event=hook,
    )
    manager.start()
    try:
        h = manager.submit_turn(
            TurnRequest(
                trace_id="ev-1",
                agent_id="agent-ev",
                session_id="sess",
                input_text="hi",
            )
        )
        h.result(timeout_s=3)
    finally:
        manager.shutdown()

    types = [e[0] for e in events]
    assert "runtime.turn.enqueued" in types, f"Missing enqueued event, got: {types}"


def test_turn_completed_event() -> None:
    events, hook = _collect_events()
    manager = AgentRuntimeManager(
        turn_executor=_simple_executor,
        on_runtime_event=hook,
    )
    manager.start()
    try:
        h = manager.submit_turn(
            TurnRequest(
                trace_id="ev-2",
                agent_id="agent-ev",
                session_id="sess",
                input_text="hi",
            )
        )
        h.result(timeout_s=3)
        sleep(0.05)  # let events flush
    finally:
        manager.shutdown()

    types = [e[0] for e in events]
    assert "runtime.turn.completed" in types, f"Missing completed event, got: {types}"


def test_turn_cancelled_event() -> None:
    events, hook = _collect_events()

    def _slow_executor(req, emit_chunk, cancel_event):  # noqa: ANN001
        del emit_chunk
        for _ in range(20):
            if cancel_event.is_set():
                break
            sleep(0.05)
        return TurnResponse(final_text=f"done:{req.trace_id}")

    manager = AgentRuntimeManager(
        turn_executor=_slow_executor,
        max_global_concurrency=1,
        on_runtime_event=hook,
    )
    manager.start()
    try:
        first = manager.submit_turn(
            TurnRequest(
                trace_id="ev-slow",
                agent_id="ev-agent",
                session_id="sess",
                input_text="a",
            )
        )
        second = manager.submit_turn(
            TurnRequest(
                trace_id="ev-cancel",
                agent_id="ev-agent",
                session_id="sess",
                input_text="b",
            )
        )
        manager.cancel_turn("ev-cancel")
        first.result(timeout_s=3)
        second.result(timeout_s=3)
        sleep(0.05)
    finally:
        manager.shutdown()

    types = [e[0] for e in events]
    assert "runtime.turn.cancelled" in types, f"Missing cancelled event, got: {types}"


def test_agent_lifecycle_events() -> None:
    events, hook = _collect_events()
    created: list[str] = []
    evicted: list[str] = []

    manager = AgentRuntimeManager(
        turn_executor=_simple_executor,
        on_runtime_event=hook,
        on_agent_create=lambda aid: created.append(aid),
        on_agent_evict=lambda aid, reason: evicted.append(aid),
    )
    manager.start()
    try:
        h = manager.submit_turn(
            TurnRequest(
                trace_id="lc-1",
                agent_id="lc-agent",
                session_id="sess",
                input_text="x",
            )
        )
        h.result(timeout_s=3)
        manager.evict("lc-agent", "test")
        sleep(0.05)
    finally:
        manager.shutdown()

    types = [e[0] for e in events]
    assert "runtime.agent.created" in types, f"Missing agent.created, got: {types}"
    assert "runtime.agent.evicted" in types, f"Missing agent.evicted, got: {types}"
    assert "lc-agent" in created
    assert "lc-agent" in evicted


def test_events_include_ts_field() -> None:
    events, hook = _collect_events()
    manager = AgentRuntimeManager(
        turn_executor=_simple_executor,
        on_runtime_event=hook,
    )
    manager.start()
    try:
        h = manager.submit_turn(
            TurnRequest(
                trace_id="ts-1", agent_id="ts-agent", session_id="sess", input_text="x"
            )
        )
        h.result(timeout_s=3)
    finally:
        manager.shutdown()

    for event_type, payload in events:
        assert "ts" in payload, f"Event {event_type!r} missing 'ts' field"


def test_shutdown_event_emitted() -> None:
    events, hook = _collect_events()
    manager = AgentRuntimeManager(
        turn_executor=_simple_executor,
        on_runtime_event=hook,
    )
    manager.start()
    manager.shutdown()

    types = [e[0] for e in events]
    assert "runtime.manager.shutdown" in types, f"Missing shutdown event, got: {types}"


def test_native_canonical_runtime_lifecycle_events_use_stable_component_ids() -> None:
    events, hook = _collect_events()
    manager = AgentRuntimeManager(
        turn_executor=_simple_executor,
        on_runtime_event=hook,
        sweep_interval_seconds=1,
    )
    manager.start()
    try:
        manager._sweep_once()
        handle = manager.submit_turn(
            TurnRequest(
                trace_id="canon-1",
                agent_id="canon-agent",
                session_id="sess",
                input_text="hello",
            )
        )
        handle.result(timeout_s=3)
        manager.evict("canon-agent", "test")
    finally:
        manager.shutdown()

    runtime_manager_started = next(
        payload
        for event_type, payload in events
        if event_type == "component.started"
        and payload.get("component", {}).get("component_kind") == "runtime_manager"
    )
    runtime_manager_heartbeat = next(
        payload
        for event_type, payload in events
        if event_type == "component.heartbeat"
        and payload.get("component", {}).get("component_kind") == "runtime_manager"
    )
    agent_started = next(
        payload
        for event_type, payload in events
        if event_type == "component.started"
        and payload.get("component", {}).get("component_kind") == "agent_runtime"
    )
    agent_stopped = next(
        payload
        for event_type, payload in events
        if event_type == "component.stopped"
        and payload.get("component", {}).get("component_kind") == "agent_runtime"
    )

    assert runtime_manager_started["component"]["component_id"] == "primary"
    assert runtime_manager_started["source_classification"] == "native_canonical"
    assert runtime_manager_heartbeat["component"]["component_id"] == "primary"
    assert runtime_manager_heartbeat["metrics"]["active_agents"] >= 0
    assert agent_started["component"]["component_id"] == "canon-agent"
    assert agent_started["component"]["host_component_id"] == "primary"
    assert agent_stopped["component"]["component_id"] == "canon-agent"
    assert agent_stopped["reason"] == "test"
