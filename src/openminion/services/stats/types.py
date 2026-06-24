"""Shared run and session stats types."""

from collections.abc import Mapping
import json
from dataclasses import dataclass
from typing import Any


def _coerce_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _first_int(payload: Mapping[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return None


def _tool_error_count_from_payload(payload: Mapping[str, Any]) -> int:
    explicit = _first_int(payload, ("tool_errors", "tool_errors_count"))
    if explicit is not None:
        return explicit
    raw_tool_results = payload.get("tool_results")
    if isinstance(raw_tool_results, str):
        try:
            raw_tool_results = json.loads(raw_tool_results)
        except ValueError:
            raw_tool_results = None
    if not isinstance(raw_tool_results, list):
        return 0
    count = 0
    for item in raw_tool_results:
        if not isinstance(item, dict):
            continue
        if bool(item.get("ok")):
            continue
        count += 1
    return max(0, count)


@dataclass(frozen=True)
class RunStats:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    duration_ms: int = 0

    @property
    def total_tokens(self) -> int:
        return max(0, self.input_tokens + self.output_tokens)

    @property
    def has_any_data(self) -> bool:
        return any(
            (
                self.input_tokens,
                self.output_tokens,
                self.cache_read_tokens,
                self.llm_calls,
                self.tool_calls,
                self.tool_errors,
                self.duration_ms,
            )
        )

    def add(self, other: "RunStats") -> "RunStats":
        return RunStats(
            input_tokens=max(0, self.input_tokens + other.input_tokens),
            output_tokens=max(0, self.output_tokens + other.output_tokens),
            cache_read_tokens=max(
                0,
                self.cache_read_tokens + other.cache_read_tokens,
            ),
            llm_calls=max(0, self.llm_calls + other.llm_calls),
            tool_calls=max(0, self.tool_calls + other.tool_calls),
            tool_errors=max(0, self.tool_errors + other.tool_errors),
            duration_ms=max(0, self.duration_ms + other.duration_ms),
        )

    def as_payload(self) -> dict[str, int]:
        return {
            "input_tokens": int(self.input_tokens),
            "output_tokens": int(self.output_tokens),
            "cache_read_tokens": int(self.cache_read_tokens),
            "llm_calls": int(self.llm_calls),
            "tool_calls": int(self.tool_calls),
            "tool_errors": int(self.tool_errors),
            "duration_ms": int(self.duration_ms),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "RunStats | None":
        if not isinstance(payload, Mapping):
            return None
        input_tokens = _first_int(
            payload,
            ("input_tokens", "prompt_tokens", "total_input_tokens_used"),
        )
        output_tokens = _first_int(
            payload,
            ("output_tokens", "completion_tokens", "total_output_tokens_used"),
        )
        cache_read_tokens = _first_int(
            payload,
            (
                "cache_read_tokens",
                "cached_tokens",
                "cached_input_tokens",
                "usage_cached_tokens",
            ),
        )
        llm_calls = _first_int(payload, ("llm_calls", "llm_calls_count"))
        tool_calls = _first_int(
            payload,
            ("tool_calls", "tool_calls_count", "tool_request_count"),
        )
        tool_errors = _first_int(payload, ("tool_errors", "tool_errors_count"))
        duration_ms = _first_int(
            payload, ("duration_ms", "elapsed_ms", "turn_duration_ms")
        )
        stats = cls(
            input_tokens=_coerce_non_negative_int(input_tokens),
            output_tokens=_coerce_non_negative_int(output_tokens),
            cache_read_tokens=_coerce_non_negative_int(cache_read_tokens),
            llm_calls=_coerce_non_negative_int(llm_calls),
            tool_calls=_coerce_non_negative_int(tool_calls),
            tool_errors=_coerce_non_negative_int(
                tool_errors
                if tool_errors is not None
                else _tool_error_count_from_payload(payload)
            ),
            duration_ms=_coerce_non_negative_int(duration_ms),
        )
        return stats if stats.has_any_data else None


@dataclass(frozen=True)
class ToolCallCount:
    name: str
    calls: int


@dataclass(frozen=True)
class RunStatsSummary:
    session_id: str
    run_id: str
    stats: RunStats


@dataclass(frozen=True)
class SessionStatsSummary:
    session_id: str
    turn_count: int
    stats: RunStats
    top_tools: tuple[ToolCallCount, ...] = ()
