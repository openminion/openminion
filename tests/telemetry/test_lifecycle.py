from __future__ import annotations

import asyncio
from pathlib import Path

from openminion.modules.telemetry.lifecycle import (
    LIFECYCLE_CONTRACT,
    build_component_identity,
    build_lifecycle_telemetry_event,
    lifecycle_event_from_payload,
    map_cron_event_to_lifecycle_event,
    map_runtime_event_to_lifecycle_event,
)
from openminion.modules.telemetry.service import TelemetryService


def _run(coro):
    return asyncio.run(coro)


def test_build_lifecycle_event_records_contract_and_source_metadata() -> None:
    component = build_component_identity(
        component_kind="runtime_manager",
        component_id="primary",
        scope="system",
        owner_module="openminion-runtime",
        capabilities=["turn_dispatch"],
    )
    event = build_lifecycle_telemetry_event(
        event_type="component.started",
        component=component,
        module_id="openminion-runtime",
        session_id="lifecycle:runtime_manager:primary",
        turn_id="boot",
        status="ok",
        reason="startup",
        source_event_type="runtime.manager.started",
        source_classification="legacy_mapped",
    )

    assert event.event_type == "component.started"
    assert event.data["contract"] == LIFECYCLE_CONTRACT
    assert event.data["component"]["component_kind"] == "runtime_manager"
    assert event.data["source_event_type"] == "runtime.manager.started"
    assert event.data["source_classification"] == "legacy_mapped"


def test_map_runtime_agent_created_to_component_started() -> None:
    event = map_runtime_event_to_lifecycle_event(
        "runtime.agent.created",
        {"agent_id": "agent-a", "created_at": "2026-03-18T00:00:00+00:00"},
    )

    assert event is not None
    assert event.event_type == "component.started"
    assert event.data["component"]["component_kind"] == "agent_runtime"
    assert event.data["component"]["component_id"] == "agent-a"
    assert event.data["component"]["parent_component_id"] == "primary"
    assert event.data["component"]["host_component_id"] == "primary"
    assert event.data["source_event_type"] == "runtime.agent.created"
    assert event.data["source_classification"] == "legacy_mapped"


def test_map_runtime_agent_evicted_to_component_stopped_preserves_host_linkage() -> (
    None
):
    event = map_runtime_event_to_lifecycle_event(
        "runtime.agent.evicted",
        {"agent_id": "agent-a", "reason": "ttl_expired"},
    )

    assert event is not None
    assert event.event_type == "component.stopped"
    assert event.data["component"]["component_kind"] == "agent_runtime"
    assert event.data["component"]["component_id"] == "agent-a"
    assert event.data["component"]["parent_component_id"] == "primary"
    assert event.data["component"]["host_component_id"] == "primary"
    assert event.data["reason"] == "ttl_expired"
    assert event.data["source_event_type"] == "runtime.agent.evicted"
    assert event.data["source_classification"] == "legacy_mapped"


def test_lifecycle_event_from_payload_preserves_native_canonical_metadata() -> None:
    component = build_component_identity(
        component_kind="runtime_manager",
        component_id="primary",
        scope="system",
        owner_module="openminion-runtime",
    )

    event = lifecycle_event_from_payload(
        "component.heartbeat",
        {
            "component": component,
            "module_id": "openminion-runtime",
            "session_id": "lifecycle:runtime_manager:primary",
            "turn_id": "runtime_manager:primary:heartbeat:1",
            "status": "ok",
            "reason": "heartbeat",
            "metrics": {"active_agents": 0},
            "source_classification": "native_canonical",
        },
    )

    assert event is not None
    assert event.event_type == "component.heartbeat"
    assert event.data["component"]["component_kind"] == "runtime_manager"
    assert event.data["metrics"]["active_agents"] == 0
    assert event.data["source_classification"] == "native_canonical"


def test_map_runtime_event_skips_legacy_mapping_when_native_lifecycle_emitted() -> None:
    event = map_runtime_event_to_lifecycle_event(
        "runtime.agent.created",
        {
            "agent_id": "agent-a",
            "created_at": "2026-03-18T00:00:00+00:00",
            "native_lifecycle_emitted": True,
        },
    )

    assert event is None


def test_map_cron_scheduler_error_to_component_degraded() -> None:
    event = map_cron_event_to_lifecycle_event(
        "cron.scheduler.error",
        {
            "daemon_id": "daemon-123",
            "daemon_component_id": "primary",
            "daemon_pid": 123,
            "error": "db failed",
        },
    )

    assert event is not None
    assert event.event_type == "component.degraded"
    assert event.data["component"]["component_kind"] == "cron_scheduler"
    assert event.data["component"]["host_component_id"] == "primary"
    assert event.data["reason"] == "scheduler_error"
    assert event.data["source_event_type"] == "cron.scheduler.error"


def test_map_cron_scheduler_heartbeat_to_component_heartbeat() -> None:
    event = map_cron_event_to_lifecycle_event(
        "cron.scheduler.heartbeat",
        {
            "daemon_id": "daemon-123",
            "daemon_component_id": "primary",
            "daemon_pid": 123,
            "active_runs": 0,
            "lag_seconds": 0.02,
            "tick_duration_ms": 1.5,
            "tick_seconds": 0.5,
        },
    )

    assert event is not None
    assert event.event_type == "component.heartbeat"
    assert event.data["component"]["component_kind"] == "cron_scheduler"
    assert event.data["component"]["host_component_id"] == "primary"
    assert event.data["metrics"]["lag_seconds"] == 0.02
    assert event.data["metrics"]["daemon_pid"] == 123
    assert event.data["source_event_type"] == "cron.scheduler.heartbeat"


def test_map_runtime_manager_kill_to_component_crashed_with_stable_identity() -> None:
    event = map_runtime_event_to_lifecycle_event(
        "runtime.manager.kill",
        {
            "active_traces": 2,
        },
    )

    assert event is not None
    assert event.event_type == "component.crashed"
    assert event.session_id == "lifecycle:runtime_manager:primary"
    assert event.data["component"]["component_kind"] == "runtime_manager"
    assert event.data["component"]["component_id"] == "primary"
    assert event.data["reason"] == "kill_switch"
    assert event.data["metrics"]["active_traces"] == 2
    assert event.data["source_event_type"] == "runtime.manager.kill"
    assert event.data["source_classification"] == "legacy_mapped"


def test_record_event_sync_persists_lifecycle_event(tmp_path: Path) -> None:
    service = TelemetryService(str(tmp_path / ".openminion" / "telemetry.db"))
    component = build_component_identity(
        component_kind="runtime_manager",
        component_id="primary",
        scope="system",
        owner_module="openminion-runtime",
    )
    event = build_lifecycle_telemetry_event(
        event_type="component.stopped",
        component=component,
        module_id="openminion-runtime",
        session_id="lifecycle:runtime_manager:primary",
        turn_id="shutdown",
        status="ok",
        reason="manual_stop",
        source_event_type="runtime.manager.shutdown",
        source_classification="legacy_mapped",
    )

    service.record_event_sync(event)
    summary = _run(service.get_session_summary("lifecycle:runtime_manager:primary"))
    _run(service.close())

    assert summary.event_count == 1
    assert summary.events[0].event_type == "component.stopped"
    assert summary.events[0].data["source_event_type"] == "runtime.manager.shutdown"
    assert summary.module_stats["openminion-runtime"].event_count == 1
