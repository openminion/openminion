"""Modules brain adapters context bridges session."""

from typing import Any

from openminion.modules.context.schemas import (
    SessionSlice,
    SessionToolEvent,
    SessionTurn,
)

from .shared import BRAIN_ADAPTER_INTERFACE_VERSION
from openminion.base.constants import STATE_KEY_ACTIVE


class BridgeSessionClient:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, backing_store: Any) -> None:
        self._store = backing_store

    def _sanitize_context_text(self, value: str, *, is_error: bool = False) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if is_error:
            return ""
        return text

    def update_summary(
        self,
        session_id: str,
        summary_short: str,
        *,
        summary_long: str | None = None,
        based_on_seq: int | None = None,
    ) -> None:
        if self._store is not None and hasattr(self._store, "update_summary"):
            try:
                self._store.update_summary(
                    session_id=session_id,
                    summary_short=summary_short,
                    summary_long=summary_long,
                    based_on_seq=int(based_on_seq or 0),
                )
            except Exception:
                pass

    def get_slice(
        self,
        *,
        session_id: str,
        purpose: str,
        limits: dict[str, int],
    ) -> SessionSlice:
        raw: dict[str, Any] = {}
        if self._store is not None and hasattr(self._store, "get_slice"):
            try:
                maybe_raw = self._store.get_slice(
                    session_id=session_id,
                    purpose=purpose,
                    limits=limits,
                )
                if isinstance(maybe_raw, dict):
                    raw = maybe_raw
            except Exception:
                raw = {}
        return self._to_session_slice(session_id=session_id, raw=raw)

    def _to_session_slice(
        self, *, session_id: str, raw: dict[str, Any]
    ) -> SessionSlice:
        summary_payload = raw.get("summary")
        summary_short = ""
        summary_long = None
        if isinstance(summary_payload, dict):
            summary_short = str(
                summary_payload.get("summary_short")
                or summary_payload.get("short")
                or ""
            )
            maybe_long = summary_payload.get("summary_long") or summary_payload.get(
                "long"
            )
            summary_long = str(maybe_long) if isinstance(maybe_long, str) else None
            summary_is_error = bool(summary_payload.get("is_error"))
        elif isinstance(summary_payload, str):
            summary_short = summary_payload
            summary_is_error = False
        else:
            summary_is_error = False
        summary_short = self._sanitize_context_text(
            summary_short,
            is_error=summary_is_error,
        )
        if summary_long is not None:
            summary_long = self._sanitize_context_text(
                summary_long,
                is_error=summary_is_error,
            )

        recent_turns_raw = raw.get("recent_turns")
        recent_turns: list[SessionTurn] = []
        if isinstance(recent_turns_raw, list):
            for idx, item in enumerate(recent_turns_raw):
                if not isinstance(item, dict):
                    continue
                role = (
                    str(item.get("role", item.get("turn_type", "user"))).strip().lower()
                    or "user"
                )
                content = str(item.get("text", item.get("content", "")) or "").strip()
                if not content:
                    continue
                if role == "assistant" and bool(item.get("is_error")):
                    continue
                turn_id = str(
                    item.get("turn_id", item.get("id", f"turn-{idx}")) or f"turn-{idx}"
                )
                ts_value = item.get("timestamp", item.get("ts"))
                ts = str(ts_value) if ts_value is not None else None
                recent_turns.append(
                    SessionTurn(
                        turn_id=turn_id,
                        role=role,
                        content=content,
                        ts=ts,
                        is_error=bool(item.get("is_error")),
                    )
                )

        tool_events_raw = raw.get("recent_tool_events")
        recent_tool_events: list[SessionToolEvent] = []
        if isinstance(tool_events_raw, list):
            for idx, item in enumerate(tool_events_raw):
                if not isinstance(item, dict):
                    continue
                event_id = (
                    str(item.get("event_id", f"tool-event-{idx}"))
                    or f"tool-event-{idx}"
                )
                tool_name = str(
                    item.get("tool_name")
                    or item.get("tool")
                    or item.get("event_type")
                    or "tool"
                )
                excerpt = str(item.get("excerpt", item.get("summary", "")) or "")
                refs_raw = item.get("artifact_refs")
                refs = (
                    [str(ref) for ref in refs_raw] if isinstance(refs_raw, list) else []
                )
                recent_tool_events.append(
                    SessionToolEvent(
                        event_id=event_id,
                        tool_name=tool_name,
                        excerpt=excerpt,
                        artifact_refs=refs,
                    )
                )

        open_tasks_raw = raw.get("open_tasks")
        open_tasks = (
            [str(task) for task in open_tasks_raw]
            if isinstance(open_tasks_raw, list)
            else []
        )

        last_event_id_value = raw.get("last_event_id")
        if last_event_id_value is None:
            last_event_seq = raw.get("last_event_seq")
            last_event_id = (
                f"seq:{last_event_seq}" if last_event_seq is not None else None
            )
        else:
            last_event_id = str(last_event_id_value)

        active_state_raw = raw.get(STATE_KEY_ACTIVE)
        active_state = active_state_raw if isinstance(active_state_raw, dict) else None

        return SessionSlice(
            session_id=str(raw.get("session_id") or session_id),
            slice_version=str(raw.get("slice_version") or "brain-bridge:v1"),
            last_event_id=last_event_id,
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
            recent_tool_events=recent_tool_events,
            prompt_context_id=(
                str(raw.get("prompt_context_id"))
                if raw.get("prompt_context_id") is not None
                else None
            ),
            checkpoint_id=(
                str(raw.get("checkpoint_id"))
                if raw.get("checkpoint_id") is not None
                else None
            ),
            seed_bundle_id=(
                str(raw.get("seed_bundle_id"))
                if raw.get("seed_bundle_id") is not None
                else None
            ),
            archive_refs=(
                [str(ref) for ref in raw.get("archive_refs", [])]
                if isinstance(raw.get("archive_refs"), list)
                else []
            ),
        )


__all__ = ["BridgeSessionClient"]
