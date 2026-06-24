from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class IterationToolCallRecord:
    tool_name: str
    duration_ms: int
    status: str
    cache_hit: bool
    parallel: bool


@dataclass(frozen=True, slots=True)
class AdaptiveLoopIterationEvent:
    iteration_index: int
    llm_call_duration_ms: int
    tool_calls: tuple[IterationToolCallRecord, ...]
    tokens_used_this_iteration: int
    budget_remaining: dict[str, Any]
    reflection_triggered: bool
    termination_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": "adaptive_loop_iteration",
            "iteration_index": self.iteration_index,
            "llm_call_duration_ms": self.llm_call_duration_ms,
            "tool_calls": [
                {
                    "tool_name": tc.tool_name,
                    "duration_ms": tc.duration_ms,
                    "status": tc.status,
                    "cache_hit": tc.cache_hit,
                    "parallel": tc.parallel,
                }
                for tc in self.tool_calls
            ],
            "tokens_used_this_iteration": self.tokens_used_this_iteration,
            "budget_remaining": self.budget_remaining,
            "reflection_triggered": self.reflection_triggered,
            "termination_reason": self.termination_reason,
        }
