"""Session-continuation summary rendering helpers."""

from __future__ import annotations

import json
from typing import Any

from ..schemas import SessionSlice


def render_continuation_payload(payload: dict[str, Any]) -> str:
    lines = [
        f"source_session_id: {payload.get('source_session_id', '')}",
        f"source_agent_id: {payload.get('source_agent_id', '')}",
        f"phase: {payload.get('phase') or 'unknown'}",
        _cursor_line(payload),
    ]
    summary = str(payload.get("session_work_summary") or "").strip()
    if summary:
        lines.extend(("work_summary:", summary))
    for label, key in (
        ("plan_steps", "plan_steps"),
        ("intents", "intents"),
        ("pending_inputs", "pending_input_refs"),
        ("memory_refs", "memory_refs"),
        ("artifact_refs", "artifact_refs"),
        ("checkpoint_refs", "checkpoint_refs"),
        ("permission_refs_revalidate", "permission_refs"),
    ):
        values = payload.get(key)
        if values:
            lines.append(
                f"{label}: {json.dumps(values, sort_keys=True, ensure_ascii=True)}"
            )
    return "\n".join(lines)


def _cursor_line(payload: dict[str, Any]) -> str:
    cursor = payload.get("cursor")
    return f"cursor: {cursor if cursor is not None else 'unknown'}"


def continuation_payload(
    session_slice: SessionSlice,
) -> tuple[dict[str, Any], dict[str, Any]]:
    continuation_event = (
        session_slice.continuation
        if isinstance(session_slice.continuation, dict)
        else {}
    )
    continuation_payload_value = (
        continuation_event.get("continuation")
        if isinstance(continuation_event.get("continuation"), dict)
        else {}
    )
    return continuation_event, continuation_payload_value
