from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

from openminion.modules.telemetry.events.catalog import CHAT_PHASE_TIMING
from openminion.modules.telemetry.trace.phase_timing import (
    CHAT_PHASES,
    ChatPhaseTimer,
    active_chat_phase,
)
from openminion.services.runtime import ingress
from openminion.services.runtime.ingress import _emit_chat_phase_timing


@dataclass
class _StubRequest:
    request_id: str = "turn-1"
    session_id: str = "sess-1"
    agent_id: str = "agent-1"


@dataclass
class _StubRuntimeConfig:
    process_mode: str = "single-process"


@dataclass
class _StubConfig:
    runtime: _StubRuntimeConfig


class _CapturingTelemetry:
    def __init__(self):
        self.events = []

    async def emit_canonical_event(
        self, session_id, turn_id, event_type, payload, **kwargs
    ):
        self.events.append((session_id, turn_id, event_type, payload))


class _SyncTelemetry:
    def __init__(self):
        self.events = []

    def emit_canonical_event(self, session_id, turn_id, event_type, payload, **kwargs):
        self.events.append((session_id, turn_id, event_type, payload))


class _RaisingTelemetry:
    def emit_canonical_event(self, *args, **kwargs):
        raise RuntimeError("telemetry boom")


@dataclass
class _StubRuntime:
    telemetry_service: object = None
    config: _StubConfig = None  # type: ignore[assignment]


def _make_runtime(telemetry, process_mode="single-process"):
    return _StubRuntime(
        telemetry_service=telemetry,
        config=_StubConfig(runtime=_StubRuntimeConfig(process_mode=process_mode)),
    )


def test_emit_helper_passes_positional_session_and_turn_ids_to_telemetry():
    telemetry = _SyncTelemetry()
    runtime = _make_runtime(telemetry)
    timer = ChatPhaseTimer(cold_start=True)
    request = _StubRequest(
        request_id="turn-xyz", session_id="sess-abc", agent_id="agent-1"
    )
    _emit_chat_phase_timing(runtime=runtime, timer=timer, request=request)
    assert len(telemetry.events) == 1
    session_id, turn_id, event_type, payload = telemetry.events[0]
    assert session_id == "sess-abc"
    assert turn_id == "turn-xyz"
    assert event_type == CHAT_PHASE_TIMING
    assert payload["cold_start"] is True
    assert payload["total_turn_ms"] >= 0
    assert payload["time_to_first_text_ms"] is None
    for phase in CHAT_PHASES:
        assert f"{phase}_ms" in payload


def test_emit_helper_is_noop_when_telemetry_service_is_none():
    runtime = _make_runtime(telemetry=None)
    timer = ChatPhaseTimer(cold_start=False)
    request = _StubRequest()
    _emit_chat_phase_timing(runtime=runtime, timer=timer, request=request)


def test_emit_helper_swallows_telemetry_exception():
    runtime = _make_runtime(telemetry=_RaisingTelemetry())
    timer = ChatPhaseTimer(cold_start=False)
    request = _StubRequest()
    _emit_chat_phase_timing(runtime=runtime, timer=timer, request=request)


def test_emit_helper_schedules_async_emit_when_loop_running():
    telemetry = _CapturingTelemetry()
    runtime = _make_runtime(telemetry)
    timer = ChatPhaseTimer(cold_start=False)
    request = _StubRequest()

    async def runner():
        _emit_chat_phase_timing(runtime=runtime, timer=timer, request=request)
        await asyncio.sleep(0)

    asyncio.run(runner())
    assert len(telemetry.events) == 1


def test_payload_carries_process_mode_from_runtime_config():
    telemetry = _SyncTelemetry()
    runtime = _make_runtime(telemetry, process_mode="daemon")
    _emit_chat_phase_timing(
        runtime=runtime,
        timer=ChatPhaseTimer(cold_start=False),
        request=_StubRequest(),
    )
    payload = telemetry.events[0][3]
    assert payload["process_mode"] == "daemon"


def test_emit_helper_does_not_raise_when_request_lacks_session_id():
    telemetry = _SyncTelemetry()
    runtime = _make_runtime(telemetry)

    @dataclass
    class _MinimalRequest:
        pass

    _emit_chat_phase_timing(
        runtime=runtime, timer=ChatPhaseTimer(), request=_MinimalRequest()
    )
    assert telemetry.events[0][0] == ""  # session_id
    assert telemetry.events[0][1] == ""  # turn_id


def test_cold_start_metadata_flag_propagates():
    telemetry = _SyncTelemetry()
    runtime = _make_runtime(telemetry)
    cold_timer = ChatPhaseTimer(cold_start=True)
    warm_timer = ChatPhaseTimer(cold_start=False)
    _emit_chat_phase_timing(runtime=runtime, timer=cold_timer, request=_StubRequest())
    _emit_chat_phase_timing(runtime=runtime, timer=warm_timer, request=_StubRequest())
    assert telemetry.events[0][3]["cold_start"] is True
    assert telemetry.events[1][3]["cold_start"] is False


def test_run_turn_payload_records_simple_turn_phases(monkeypatch):
    telemetry = _SyncTelemetry()
    runtime = _make_runtime(telemetry)

    def _stub_request_from_payload(**kwargs):
        return _StubRequest(request_id="turn-simple", session_id="sess-simple")

    def _stub_execute_runtime_turn(**kwargs):
        with active_chat_phase("gateway_routing"):
            pass
        with active_chat_phase("provider_round_trip"):
            pass
        return SimpleNamespace(as_payload=lambda: {"message": "hello"})

    monkeypatch.setattr(ingress, "runtime_turn_request_from_payload", _stub_request_from_payload)
    monkeypatch.setattr(ingress, "execute_runtime_turn", _stub_execute_runtime_turn)

    assert ingress.run_turn_payload(runtime=runtime, payload={"message": "hello"}) == {
        "message": "hello"
    }
    payload = telemetry.events[0][3]
    assert "provider_request_build" in payload["phases_instrumented"]
    assert "gateway_routing" in payload["phases_instrumented"]
    assert "provider_round_trip" in payload["phases_instrumented"]
    assert "response_normalization" in payload["phases_instrumented"]


def test_run_turn_payload_records_tool_turn_phases(monkeypatch):
    telemetry = _SyncTelemetry()
    runtime = _make_runtime(telemetry)

    def _stub_request_from_payload(**kwargs):
        return _StubRequest(request_id="turn-tool", session_id="sess-tool")

    def _stub_execute_runtime_turn(**kwargs):
        with active_chat_phase("tool_calls"):
            pass
        with active_chat_phase("approval_wait"):
            pass
        return SimpleNamespace(as_payload=lambda: {"message": "tool done"})

    monkeypatch.setattr(ingress, "runtime_turn_request_from_payload", _stub_request_from_payload)
    monkeypatch.setattr(ingress, "execute_runtime_turn", _stub_execute_runtime_turn)

    assert ingress.run_turn_payload(
        runtime=runtime,
        payload={"message": "check disk", "forced_tools": ["host.metrics"]},
    ) == {"message": "tool done"}
    payload = telemetry.events[0][3]
    assert "tool_calls" in payload["phases_instrumented"]
    assert "approval_wait" in payload["phases_instrumented"]
