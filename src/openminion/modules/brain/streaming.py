"""Structural provider-stream mapping for the brain progress owner."""

from __future__ import annotations

from typing import Any

from openminion.modules.llm.schemas import LLMStreamEvent


def turn_progress_from_llm_stream_event(
    event: LLMStreamEvent,
    *,
    trace_id: str,
    timestamp: str | None = None,
) -> dict[str, Any] | None:
    """Map a provider text delta into the existing turn-progress envelope."""

    if event.type != "delta" or not event.delta_text:
        return None
    payload: dict[str, Any] = {
        "trace_id": str(trace_id or "").strip() or "brain-stream",
        "kind": "delta",
        "data": {"delta_text": event.delta_text},
    }
    if timestamp:
        payload["ts"] = str(timestamp)
    return payload


__all__ = ["turn_progress_from_llm_stream_event"]
