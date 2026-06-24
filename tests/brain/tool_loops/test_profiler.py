from __future__ import annotations

import pytest

from openminion.modules.brain.loop.tools.profiler import LoopProfiler


def test_profiler_aggregates_multiple_tool_calls() -> None:
    profiler = LoopProfiler()
    profiler.record_tool_call("file.read", 100)
    profiler.record_tool_call("file.read", 200)
    profiler.record_tool_call("exec.run", 50)

    summary = profiler.summary()

    assert summary["slowest_tool"] is not None
    assert summary["slowest_tool"]["tool_name"] == "file.read"
    assert summary["slowest_tool"]["duration_ms"] == 200

    assert summary["fastest_tool"] is not None
    assert summary["fastest_tool"]["tool_name"] == "exec.run"
    assert summary["fastest_tool"]["duration_ms"] == 50

    assert summary["most_called_tool"] == "file.read"


def test_profiler_zero_tool_calls() -> None:
    profiler = LoopProfiler()
    summary = profiler.summary()

    assert summary["slowest_tool"] is None
    assert summary["fastest_tool"] is None
    assert summary["most_called_tool"] is None
    assert summary["avg_iteration_latency_by_profile"] == {}
    assert summary["cache_hit_rate"] == 0.0


def test_profiler_summary_structure() -> None:
    profiler = LoopProfiler()
    profiler.record_tool_call("tool.a", 75)
    profiler.record_iteration("my_profile", 300)
    profiler.record_cache(hits=3, misses=7)

    summary = profiler.summary()

    assert "slowest_tool" in summary
    assert "fastest_tool" in summary
    assert "most_called_tool" in summary
    assert "avg_iteration_latency_by_profile" in summary
    assert "cache_hit_rate" in summary

    assert isinstance(summary["avg_iteration_latency_by_profile"], dict)
    assert summary["avg_iteration_latency_by_profile"]["my_profile"] == 300
    assert isinstance(summary["cache_hit_rate"], float)


@pytest.mark.parametrize(
    ("hits", "misses", "expected"),
    [(3, 7, 0.3), (5, 0, 1.0), (0, 0, 0.0)],
)
def test_profiler_cache_hit_rate(hits: int, misses: int, expected: float) -> None:
    profiler = LoopProfiler()
    profiler.record_cache(hits=hits, misses=misses)
    assert profiler.summary()["cache_hit_rate"] == expected


def test_profiler_avg_iteration_latency() -> None:
    profiler = LoopProfiler()
    profiler.record_iteration("profile_a", 100)
    profiler.record_iteration("profile_a", 200)
    profiler.record_iteration("profile_b", 400)

    summary = profiler.summary()
    avg = summary["avg_iteration_latency_by_profile"]
    assert avg["profile_a"] == 150
    assert avg["profile_b"] == 400


def test_profiler_cumulative_cache_recording() -> None:
    profiler = LoopProfiler()
    profiler.record_cache(hits=2, misses=3)
    profiler.record_cache(hits=1, misses=1)

    assert profiler.total_cache_hits == 3
    assert profiler.total_cache_misses == 4
    summary = profiler.summary()
    assert round(summary["cache_hit_rate"], 4) == round(3 / 7, 4)
