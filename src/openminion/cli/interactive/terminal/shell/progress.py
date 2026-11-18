import logging
import asyncio
from collections.abc import Callable
from typing import Any

from openminion.cli.status.tool_calls import format_public_tool_activity

from ..status_line import TerminalStatusLine
from ..transcript import TerminalTranscript
from .permissions import (
    runtime_permission_mode as _runtime_permission_mode,
    runtime_action_policy_mode as _runtime_action_policy_mode,
)
from openminion.cli.presentation.visible_parity import statusline_label

_LOGGER = logging.getLogger(__name__)

_DEFAULT_TURN_STATUS = "Working on it..."

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


def tool_progress_status_label(payload: dict[str, Any], *, pending: bool) -> str:
    name = (
        str(
            payload.get("model_tool_name")
            or payload.get("tool_name")
            or payload.get("name")
            or payload.get("tool")
            or ""
        ).strip()
        or "tool"
    )
    return str(format_public_tool_activity(name, pending=pending))


def apply_turn_progress_status(
    *,
    handle: Any | None,
    status_line: TerminalStatusLine | None,
    invalidate_prompt: Callable[[], None] | None,
    label: str,
    status_key: str = "working",
) -> None:
    if handle is not None:
        setter = getattr(handle, "set_status_label", None)
        if callable(setter):
            setter(label)
    if status_line is not None:
        status_line.set_state(
            state="responding", turn_status=label, status_key=status_key
        )
        if callable(invalidate_prompt):
            invalidate_prompt()


async def tick_turn_status_line(
    *,
    status_line: TerminalStatusLine,
    invalidate_prompt: Callable[[], None] | None = None,
) -> None:
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    while True:
        updates: dict[str, Any] = {"elapsed_seconds": loop.time() - started_at}
        state = str(getattr(status_line, "state", "") or "")
        turn_status = str(getattr(status_line, "turn_status_label", "") or "").strip()
        if not state or state == "idle":
            updates["state"] = "responding"
        if not turn_status:
            updates["turn_status"] = _DEFAULT_TURN_STATUS
        status_line.set_state(**updates)
        if callable(invalidate_prompt):
            invalidate_prompt()
        await asyncio.sleep(1.0)


async def stop_turn_status_line_tick(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _route_durable_activity_event(
    transcript: TerminalTranscript, payload: dict[str, Any]
) -> bool:
    try:
        from openminion.cli.status.activity_ledger import (
            KIND_APPROVAL,
            KIND_BACKGROUND,
            KIND_BUDGET,
            KIND_ERROR,
            KIND_PLAN,
            activity_from_progress_payload,
        )

        event = activity_from_progress_payload(payload)
        if event is not None and event.kind in {
            KIND_PLAN,
            KIND_APPROVAL,
            KIND_BACKGROUND,
            KIND_BUDGET,
            KIND_ERROR,
        }:
            transcript.push_activity_event(event)
            return True
    except Exception:
        _LOGGER.debug("activity event routing failed", exc_info=True)
    return False


def _finalize_turn_status_line(runtime: Any, status_line: TerminalStatusLine) -> None:
    status_line.set_state(
        state="idle",
        permission_mode=_runtime_permission_mode(runtime),
        action_policy_mode=_runtime_action_policy_mode(runtime),
        custom=statusline_label(runtime),
        turn_status="",
    )
