from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class LoopProfiler:
    _tool_durations: dict[str, list[int]] = field(default_factory=dict)
    _iteration_latencies: dict[str, list[int]] = field(default_factory=dict)
    _tool_call_counts: dict[str, int] = field(default_factory=dict)
    total_cache_hits: int = 0
    total_cache_misses: int = 0

    def record_tool_call(self, tool_name: str, duration_ms: int) -> None:
        self._tool_durations.setdefault(tool_name, []).append(duration_ms)
        self._tool_call_counts[tool_name] = self._tool_call_counts.get(tool_name, 0) + 1

    def record_iteration(self, profile_name: str, duration_ms: int) -> None:
        self._iteration_latencies.setdefault(profile_name, []).append(duration_ms)

    def record_cache(self, hits: int, misses: int) -> None:
        self.total_cache_hits += hits
        self.total_cache_misses += misses

    def summary(self) -> dict[str, Any]:
        all_durations = [
            (name, d)
            for name, durations in self._tool_durations.items()
            for d in durations
        ]
        slowest = max(all_durations, key=lambda x: x[1]) if all_durations else None
        fastest = min(all_durations, key=lambda x: x[1]) if all_durations else None
        most_called = (
            max(self._tool_call_counts.items(), key=lambda x: x[1])
            if self._tool_call_counts
            else None
        )

        avg_latencies: dict[str, int] = {}
        for profile, latencies in self._iteration_latencies.items():
            avg_latencies[profile] = sum(latencies) // max(len(latencies), 1)

        total_requests = self.total_cache_hits + self.total_cache_misses
        cache_hit_rate = (
            self.total_cache_hits / total_requests if total_requests > 0 else 0.0
        )

        return {
            "slowest_tool": (
                {"tool_name": slowest[0], "duration_ms": slowest[1]}
                if slowest
                else None
            ),
            "fastest_tool": (
                {"tool_name": fastest[0], "duration_ms": fastest[1]}
                if fastest
                else None
            ),
            "most_called_tool": most_called[0] if most_called else None,
            "avg_iteration_latency_by_profile": avg_latencies,
            "cache_hit_rate": round(cache_hit_rate, 4),
        }
