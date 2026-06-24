from typing import Any, Callable
from uuid import uuid4

from openminion.modules.storage.record_store import RecordStore

from .events import EventStore
from .json_utils import to_json


def _clamp_importance(value: int) -> int:
    return max(0, min(int(value), 3))


class SessionEventWriter:
    def __init__(
        self,
        record_store: RecordStore,
        *,
        event_store: EventStore,
        get_session: Callable[[str], dict[str, Any] | None],
        touch_session_tx: Callable[..., None],
        invalidate_slice_cache: Callable[[str], None],
        add_artifact_refs: Callable[..., None],
        add_run_usage_delta: Callable[..., None] | None,
        utc_now_iso: Callable[[], str],
    ) -> None:
        self._record_store = record_store
        self._event_store = event_store
        self._get_session = get_session
        self._touch_session_tx = touch_session_tx
        self._invalidate_slice_cache = invalidate_slice_cache
        self._add_artifact_refs = add_artifact_refs
        self._add_run_usage_delta = add_run_usage_delta
        self._utc_now_iso = utc_now_iso

    def append_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        attachments: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        role_value = str(role).strip().lower()
        if role_value not in {"user", "assistant", "system", "tool"}:
            raise ValueError(f"unsupported role: {role}")

        turn_id = uuid4().hex
        now = self._utc_now_iso()
        attachment_items = list(attachments or [])
        meta_items = dict(meta or {})

        actor_type = {
            "user": "user",
            "assistant": "agent",
            "system": "system",
            "tool": "tool",
        }[role_value]
        actor_id: str | None = None
        if role_value == "assistant":
            session = self._get_session(session_id)
            actor_id = (
                str(session["active_agent_id"])
                if session and session.get("active_agent_id")
                else None
            )

        payload: dict[str, Any] = {
            "turn_id": turn_id,
            "text": content,
            "attachments": attachment_items,
        }
        is_error = bool(meta_items.get("is_error"))
        if role_value == "assistant":
            payload["ui_hints"] = meta_items.get("ui_hints", meta_items)
        else:
            payload["channel_meta"] = meta_items
        if is_error:
            payload["is_error"] = True
        refs: dict[str, Any] = {}
        if attachment_items:
            refs["artifact_refs"] = attachment_items

        with self._record_store.transaction():
            self._record_store.execute_count(
                """
                INSERT INTO turns(turn_id, session_id, ts, role, content, attachments_json, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    session_id,
                    now,
                    role_value,
                    content,
                    to_json(attachment_items),
                    to_json(meta_items),
                ),
            )
            self._write_session_event_tx(
                session_id=session_id,
                timestamp=now,
                event_type=f"turn.{role_value}",
                actor_type=actor_type,
                actor_id=actor_id,
                trace_id=None,
                span_id=None,
                task_id=None,
                parent_event_id=None,
                payload=payload,
                refs=refs or None,
                importance=1,
                redaction="none",
                verify_session_exists=False,
            )
            self._touch_session_tx(session_id=session_id, ts=now)
        self._add_artifact_refs(session_id=session_id, ref_values=attachment_items)
        self._invalidate_slice_cache(session_id)
        return turn_id

    def append_event(
        self,
        session_id: str,
        type: str | None = None,
        payload: dict[str, Any] | None = None,
        *,
        event_type: str | None = None,
        actor_type: str = "system",
        actor_id: str | None = None,
        trace: dict[str, Any] | None = None,
        refs: dict[str, Any] | None = None,
        parent_event_id: str | None = None,
        importance: int = 1,
        redaction: str | None = None,
        agent_id: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        task_id: str | None = None,
        parent_id: str | None = None,
        artifact_refs: list[str] | None = None,
        memory_refs: list[str] | None = None,
        status: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> str:
        event_name = (event_type or type or "").strip()
        if not event_name:
            raise ValueError("event_type is required")

        payload_obj = dict(payload or {})
        if status is not None and "status" not in payload_obj:
            payload_obj["status"] = status
        if error is not None and "error" not in payload_obj:
            payload_obj["error"] = error

        refs_obj = dict(refs or {})
        if artifact_refs is not None:
            refs_obj["artifact_refs"] = list(artifact_refs)
        if memory_refs is not None:
            refs_obj["memory_refs"] = list(memory_refs)

        trace_obj = dict(trace or {})
        trace_id_value = (
            str(trace_obj.get("trace_id"))
            if trace_obj.get("trace_id") is not None
            else trace_id
        )
        span_id_value = (
            str(trace_obj.get("span_id"))
            if trace_obj.get("span_id") is not None
            else span_id
        )
        task_id_value = (
            str(trace_obj.get("task_id"))
            if trace_obj.get("task_id") is not None
            else task_id
        )

        actor_type_value = str(actor_type).strip() or "system"
        actor_id_value = actor_id or agent_id
        if actor_id_value and actor_type_value == "system":
            actor_type_value = "agent"

        now = self._utc_now_iso()
        with self._record_store.transaction():
            event_id = self._write_session_event_tx(
                session_id=session_id,
                timestamp=now,
                event_type=event_name,
                actor_type=actor_type_value,
                actor_id=actor_id_value,
                trace_id=trace_id_value,
                span_id=span_id_value,
                task_id=task_id_value,
                parent_event_id=parent_event_id or parent_id,
                payload=payload_obj,
                refs=refs_obj or None,
                importance=importance,
                redaction=str(redaction or "none"),
                verify_session_exists=True,
            )
            self._maybe_backfill_run_usage(
                event_type=event_name,
                payload=payload_obj,
            )
            self._touch_session_tx(session_id=session_id, ts=now)
        self._add_artifact_refs(
            session_id=session_id,
            ref_values=refs_obj.get("artifact_refs"),
        )
        self._invalidate_slice_cache(session_id)
        return event_id

    def _maybe_backfill_run_usage(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if event_type != "llm.call.completed" or self._add_run_usage_delta is None:
            return
        run_id = str(payload.get("run_id", "") or "").strip()
        if not run_id:
            return
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return
        try:
            prompt_tokens = max(0, int(usage.get("prompt_tokens", 0) or 0))
            completion_tokens = max(0, int(usage.get("completion_tokens", 0) or 0))
        except (TypeError, ValueError):
            return
        self._add_run_usage_delta(
            run_id,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        )

    def _write_session_event_tx(
        self,
        *,
        session_id: str,
        timestamp: str,
        event_type: str,
        actor_type: str,
        actor_id: str | None,
        trace_id: str | None,
        span_id: str | None,
        task_id: str | None,
        parent_event_id: str | None,
        payload: dict[str, Any] | None,
        refs: dict[str, Any] | None,
        importance: int,
        redaction: str,
        verify_session_exists: bool,
    ) -> str:
        if verify_session_exists:
            exists = self._record_store.query_dicts(
                "SELECT 1 FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            if not exists:
                raise ValueError(f"session not found: {session_id}")
        return self._event_store.insert_session_event_tx(
            session_id=session_id,
            timestamp=timestamp,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            trace_id=trace_id,
            span_id=span_id,
            task_id=task_id,
            parent_event_id=parent_event_id,
            payload=payload,
            refs=refs,
            importance=_clamp_importance(importance),
            redaction=redaction,
        )
