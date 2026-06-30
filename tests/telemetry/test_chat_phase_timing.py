from __future__ import annotations

import time

import pytest

from openminion.modules.telemetry.trace.phase_timing import (
    CHAT_PHASES,
    ChatPhaseTimer,
    ChatPhaseTimingPayload,
    active_chat_phase,
    mark_active_chat_first_text,
    use_chat_phase_timer,
)
from openminion.modules.telemetry.events.catalog import CHAT_PHASE_TIMING


# --- closed-set vocabulary regression ---


def test_chat_phases_closed_set_matches_contract():

    assert set(CHAT_PHASES) == {
        "runtime_bootstrap",
        "daemon_probe_start",
        "session_resume",
        "memory_retrieval",
        "context_pack_build",
        "gateway_routing",
        "gateway_session_context",
        "brain_state_load",
        "brain_pre_dispatch",
        "brain_budget_check",
        "brain_confirmation",
        "brain_dispatch",
        "tool_schema_serialization",
        "provider_request_build",
        "provider_round_trip",
        "approval_wait",
        "tool_calls",
        "response_normalization",
        "response_persistence",
        "memory_write",
        "cli_render_delivery",
    }


def test_event_type_registered_in_catalog():

    assert CHAT_PHASE_TIMING == "chat.phase_timing"


# --- ChatPhaseTimingPayload schema ---


def test_payload_is_frozen():
    payload = ChatPhaseTimingPayload(
        cold_start=True, total_turn_ms=100, time_to_first_text_ms=None
    )
    with pytest.raises(Exception):
        payload.total_turn_ms = 200  # type: ignore[misc]


def test_payload_rejects_negative_total():
    with pytest.raises(ValueError):
        ChatPhaseTimingPayload(
            cold_start=False, total_turn_ms=-1, time_to_first_text_ms=None
        )


def test_payload_rejects_negative_ttft():
    with pytest.raises(ValueError):
        ChatPhaseTimingPayload(
            cold_start=False, total_turn_ms=10, time_to_first_text_ms=-5
        )


def test_payload_rejects_negative_phase_ms():
    with pytest.raises(ValueError):
        ChatPhaseTimingPayload(
            cold_start=False,
            total_turn_ms=10,
            time_to_first_text_ms=None,
            provider_round_trip_ms=-1,
        )


def test_payload_as_dict_contains_all_phase_keys_and_load_bearing_metrics():

    payload = ChatPhaseTimingPayload(
        cold_start=True, total_turn_ms=100, time_to_first_text_ms=20
    )
    d = payload.as_dict()
    # separate in evidence.
    assert d["cold_start"] is True
    assert d["total_turn_ms"] == 100
    assert d["time_to_first_text_ms"] == 20
    for phase in CHAT_PHASES:
        assert f"{phase}_ms" in d
        assert d[f"{phase}_ms"] == 0
    # phases_instrumented honesty surface
    assert d["phases_instrumented"] == []


def test_payload_as_dict_renders_none_ttft_as_null():

    payload = ChatPhaseTimingPayload(
        cold_start=False, total_turn_ms=50, time_to_first_text_ms=None
    )
    assert payload.as_dict()["time_to_first_text_ms"] is None


# --- ChatPhaseTimer behavior ---


def test_timer_records_per_phase_elapsed():
    timer = ChatPhaseTimer(cold_start=False)
    with timer.phase("runtime_bootstrap"):
        time.sleep(0.005)
    with timer.phase("provider_round_trip"):
        time.sleep(0.010)
    payload = timer.build_payload(
        turn_id="t1", session_id="s1", agent_id="a1", process_mode="single-process"
    )
    # Should be at least the slept ms (with some slack for overhead).
    assert payload.runtime_bootstrap_ms >= 4
    assert payload.provider_round_trip_ms >= 8
    assert payload.total_turn_ms >= 14


def test_timer_uninstrumented_phases_report_zero_in_phases_instrumented_list():

    timer = ChatPhaseTimer(cold_start=True)
    with timer.phase("provider_round_trip"):
        pass
    payload = timer.build_payload()
    # provider_round_trip was entered → instrumented
    assert "provider_round_trip" in payload.phases_instrumented
    # runtime_bootstrap was not entered → not instrumented (but field is 0)
    assert "runtime_bootstrap" not in payload.phases_instrumented
    assert payload.runtime_bootstrap_ms == 0


def test_timer_rejects_unknown_phase_name():

    timer = ChatPhaseTimer()
    with pytest.raises(ValueError):
        with timer.phase("totally_made_up"):
            pass


def test_timer_reentrant_phase_accumulates():

    timer = ChatPhaseTimer()
    with timer.phase("memory_retrieval"):
        time.sleep(0.003)
    with timer.phase("memory_retrieval"):
        time.sleep(0.003)
    payload = timer.build_payload()
    assert payload.memory_retrieval_ms >= 5


def test_timer_mark_first_text_is_idempotent():

    timer = ChatPhaseTimer()
    time.sleep(0.005)
    timer.mark_first_text()
    first_ttft = timer._first_text_ns
    time.sleep(0.005)
    timer.mark_first_text()
    assert timer._first_text_ns == first_ttft


def test_timer_ttft_is_none_when_no_first_text_marked():

    timer = ChatPhaseTimer()
    with timer.phase("provider_round_trip"):
        time.sleep(0.001)
    payload = timer.build_payload()
    assert payload.time_to_first_text_ms is None


def test_timer_cold_start_propagates_to_payload():
    cold = ChatPhaseTimer(cold_start=True).build_payload()
    warm = ChatPhaseTimer(cold_start=False).build_payload()
    assert cold.cold_start is True
    assert warm.cold_start is False


def test_timer_does_not_crash_when_zero_phases_entered():

    timer = ChatPhaseTimer()
    payload = timer.build_payload()
    assert payload.phases_instrumented == ()
    for phase in CHAT_PHASES:
        assert getattr(payload, f"{phase}_ms") == 0


def test_timer_overhead_is_negligible():

    timer = ChatPhaseTimer()
    start = time.perf_counter()
    for _ in range(100):
        for phase in CHAT_PHASES:
            with timer.phase(phase):
                pass
    elapsed_ms = (time.perf_counter() - start) * 1000
    # 1000 phase-marks in well under 50ms on modern hardware
    assert elapsed_ms < 100


def test_active_chat_phase_is_noop_without_active_timer():
    with active_chat_phase("provider_round_trip"):
        pass


def test_active_chat_phase_records_on_active_timer():
    timer = ChatPhaseTimer()
    with use_chat_phase_timer(timer):
        with active_chat_phase("tool_calls"):
            time.sleep(0.001)
    payload = timer.build_payload()
    assert "tool_calls" in payload.phases_instrumented
    assert payload.tool_calls_ms >= 0


def test_active_chat_phase_rejects_unknown_name_without_timer():
    with pytest.raises(ValueError):
        with active_chat_phase("not_a_phase"):
            pass


def test_active_timer_context_resets_after_exit():
    timer = ChatPhaseTimer()
    with use_chat_phase_timer(timer):
        mark_active_chat_first_text()
    first_ttft = timer._first_text_ns
    mark_active_chat_first_text()
    assert timer._first_text_ns == first_ttft
