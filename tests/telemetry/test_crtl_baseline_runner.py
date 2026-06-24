from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "smoke"
    / "crtl_baseline.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "crtl_baseline_test_load", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_script_importable():

    module = _load_module()
    assert hasattr(module, "main")
    assert hasattr(module, "_summarize")
    assert hasattr(module, "_BaselineSink")


def test_summarize_empty_events_returns_zero_runs():
    module = _load_module()
    summary = module._summarize([])
    assert summary["runs"] == 0
    assert "note" in summary


def test_summarize_single_event_carries_load_bearing_metrics():
    module = _load_module()
    event = {
        "total_turn_ms": 150,
        "time_to_first_text_ms": 40,
        "cold_start": True,
        "runtime_bootstrap_ms": 100,
        "provider_round_trip_ms": 50,
    }
    for phase in module.CHAT_PHASES:
        event.setdefault(f"{phase}_ms", 0)
    summary = module._summarize([event])
    assert summary["runs"] == 1
    assert summary["total_turn_ms_max"] == 150
    assert summary["ttft_observations"] == 1
    assert summary["cold_start_observed"] is True
    # Per-phase block present for every CHAT_PHASE
    for phase in module.CHAT_PHASES:
        assert phase in summary["per_phase_ms"]


def test_summarize_aggregates_multiple_events_p50_p95():
    module = _load_module()
    events = []
    for i in range(10):
        e = {
            "total_turn_ms": 100 + i * 10,
            "time_to_first_text_ms": 20 + i,
            "cold_start": False,
        }
        for phase in module.CHAT_PHASES:
            e.setdefault(f"{phase}_ms", 0)
        events.append(e)
    summary = module._summarize(events)
    assert summary["runs"] == 10
    # p95 should reflect the upper tail of the distribution
    assert summary["total_turn_ms_p95"] >= summary["total_turn_ms_p50"]
    assert summary["total_turn_ms_max"] == 190


def test_summarize_handles_ttft_none_observations():

    module = _load_module()
    events = [
        {
            "total_turn_ms": 100,
            "time_to_first_text_ms": None,
            "cold_start": True,
            **{f"{p}_ms": 0 for p in module.CHAT_PHASES},
        },
        {
            "total_turn_ms": 110,
            "time_to_first_text_ms": 30,
            "cold_start": False,
            **{f"{p}_ms": 0 for p in module.CHAT_PHASES},
        },
    ]
    summary = module._summarize(events)
    assert summary["ttft_observations"] == 1


def test_baseline_sink_captures_only_chat_phase_timing_events():

    import asyncio

    from openminion.modules.telemetry.events.catalog import CHAT_PHASE_TIMING

    module = _load_module()
    sink = module._BaselineSink()

    async def runner():
        await sink.emit_canonical_event(
            "s1", "t1", CHAT_PHASE_TIMING, {"total_turn_ms": 100}
        )
        await sink.emit_canonical_event(
            "s1", "t2", "some.other.event", {"unrelated": True}
        )
        await sink.emit_canonical_event(
            "s2", "t3", CHAT_PHASE_TIMING, {"total_turn_ms": 200}
        )

    asyncio.run(runner())
    assert len(sink.events) == 2
    assert sink.events[0]["session_id"] == "s1"
    assert sink.events[1]["session_id"] == "s2"
    assert all("total_turn_ms" in e for e in sink.events)
