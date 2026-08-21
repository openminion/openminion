"""Shared constants for telemetry usage projections."""

RUNTIME_EVENT_READ_LIMIT = 10_000
LLM_USAGE_EVENT_TYPES = frozenset({"llm.call.completed", "llm.call.failed"})

__all__ = ["LLM_USAGE_EVENT_TYPES", "RUNTIME_EVENT_READ_LIMIT"]
