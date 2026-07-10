from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..status_line import TerminalStatusLine

_PROGRESS_KIND_ALIASES = {
    "tool_start": "tool_started",
    "tool_started": "tool_started",
    "tool_call_start": "tool_started",
    "tool_call_started": "tool_started",
    "tool_complete": "tool_completed",
    "tool_completed": "tool_completed",
    "tool_finish": "tool_completed",
    "tool_finished": "tool_completed",
    "tool_call_complete": "tool_completed",
    "tool_call_completed": "tool_completed",
}


def normalize_progress_kind(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    for key in ("kind", "source_event", "source_event_type", "event_type"):
        raw = payload.get(key)
        normalized = str(raw or "").strip().lower().replace(".", "_").replace("-", "_")
        if normalized in _PROGRESS_KIND_ALIASES:
            return _PROGRESS_KIND_ALIASES[normalized]
    return ""


async def tick_turn_status_line(
    *,
    status_line: TerminalStatusLine,
    invalidate_prompt: Callable[[], None] | None = None,
) -> None:
    """Keep the active turn footer clock fresh while prompt input is available."""

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    while True:
        status_line.set_state(elapsed_seconds=loop.time() - started_at)
        if callable(invalidate_prompt):
            invalidate_prompt()
        await asyncio.sleep(1.0)
