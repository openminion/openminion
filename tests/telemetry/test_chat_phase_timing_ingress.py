from __future__ import annotations

import asyncio
from dataclasses import dataclass

from openminion.modules.telemetry.events.catalog import CHAT_PHASE_TIMING
from openminion.modules.telemetry.trace.phase_timing import ChatPhaseTimer
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
    for phase in (
        "runtime_bootstrap",
        "daemon_probe_start",
        "session_resume",
        "memory_retrieval",
        "context_pack_build",
        "tool_schema_serialization",
        "provider_request_build",
        "provider_round_trip",
        "response_normalization",
        "cli_render_delivery",
    ):
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
