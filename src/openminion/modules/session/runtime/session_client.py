"""Session runtime context adapter."""

import logging
from typing import Any

from ..interfaces import SESSION_INTERFACE_VERSION
from openminion.base.constants import STATE_KEY_ACTIVE

_log = logging.getLogger(__name__)


class SessctlSessionClient:
    """Adapt session-store slices into context `SessionSlice` models."""

    def __init__(self, store: Any, *, logger: logging.Logger | None = None) -> None:
        self._store = store
        self._log = logger or _log

    def get_slice(
        self,
        *,
        session_id: str,
        purpose: str,
        limits: dict[str, int],
    ) -> Any:
        from openminion.modules.context.schemas import (
            SessionSlice,
            SessionTurn,
            SessionToolEvent,
        )  # type: ignore[import]

        raw: dict[str, Any] = self._store.get_slice(session_id, purpose, limits)
        slice_version = str(raw.get("slice_version", ""))
        summary_raw = raw.get("summary") or {}
        summary_short = ""
        summary_long: str | None = None
        if isinstance(summary_raw, dict):
            summary_short = str(
                summary_raw.get("summary_short") or summary_raw.get("short") or ""
            )
            long_val = summary_raw.get("summary_long") or summary_raw.get("long")
            summary_long = str(long_val) if long_val else None
        elif isinstance(summary_raw, str):
            summary_short = summary_raw

        recent_turns = [
            SessionTurn(
                turn_id=str(turn.get("turn_id") or turn.get("event_id") or ""),
                role=str(turn.get("role", "user")),
                content=str(turn.get("text") or turn.get("content", "")),
                ts=str(turn.get("timestamp") or turn.get("ts", "")) or None,
                is_error=bool(turn.get("is_error")),
            )
            for turn in raw.get("recent_turns", [])
            if isinstance(turn, dict)
        ]

        open_tasks: list[str] = []
        for task in raw.get("open_tasks", []):
            if isinstance(task, str):
                open_tasks.append(task)
                continue
            if isinstance(task, dict):
                tid = task.get("task_id") or task.get("job_id") or task.get("id", "")
                if tid:
                    open_tasks.append(str(tid))

        active_state: dict[str, Any] | None = None
        active_state_raw = raw.get(STATE_KEY_ACTIVE)
        if isinstance(active_state_raw, dict) and active_state_raw:
            active_state = active_state_raw

        tool_events = [
            SessionToolEvent(
                event_id=str(event.get("event_id", "")),
                tool_name=str(event.get("tool_name") or event.get("name", "")),
                excerpt=str(
                    event.get("excerpt")
                    or event.get("summary")
                    or event.get("text", "")
                ),
                artifact_refs=list(event.get("artifact_refs", [])),
            )
            for event in raw.get("recent_tool_events", [])
            if isinstance(event, dict)
        ]

        self._log.debug(
            "sessctl.get_slice: session_id=%s purpose=%s slice_version=%s "
            "turns=%d tool_events=%d open_tasks=%d",
            session_id,
            purpose,
            slice_version,
            len(recent_turns),
            len(tool_events),
            len(open_tasks),
        )

        return SessionSlice(
            session_id=session_id,
            slice_version=slice_version,
            summary_short=summary_short,
            summary_long=summary_long,
            conversation_summary=str(raw.get("conversation_summary") or ""),
            active_task_plan=raw.get("active_task_plan")
            if isinstance(raw.get("active_task_plan"), dict)
            else None,
            pending_trailer_feedback=raw.get("pending_trailer_feedback")
            if isinstance(raw.get("pending_trailer_feedback"), dict)
            else None,
            total_turn_count=int(raw.get("total_turn_count") or len(recent_turns)),
            recent_turns=recent_turns,
            open_tasks=open_tasks,
            active_state=active_state,
            recent_tool_events=tool_events,
            archive_refs=[str(ref) for ref in raw.get("archive_refs", [])],
        )

    contract_version = SESSION_INTERFACE_VERSION
