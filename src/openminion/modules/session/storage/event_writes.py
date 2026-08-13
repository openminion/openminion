from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid4, uuid5

from openminion.base.redaction import redact_sensitive_text
from openminion.modules.storage.record_store import RecordStore

from .events import EventStore
from .json_utils import to_json

_TOOL_TRANSCRIPT_EVENTS = {
    "tool.call.requested",
    "tool.call.completed",
    "tool.call.blocked",
}
_UNSUPPORTED_TOOL_TERMINALS = {"tool.call.failed", "tool.call.error"}
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)


def _bounded_tool_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 6:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        bounded: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:50]:
            key = str(raw_key)
            if any(part in key.lower() for part in _SENSITIVE_KEY_PARTS):
                bounded[key] = "[REDACTED]"
            else:
                bounded[key] = _bounded_tool_value(item, depth=depth + 1)
        if len(value) > 50:
            bounded["_truncated_fields"] = len(value) - 50
        return bounded
    if isinstance(value, (list, tuple)):
        bounded_items = [
            _bounded_tool_value(item, depth=depth + 1) for item in list(value)[:50]
        ]
        if len(value) > 50:
            bounded_items.append(f"[TRUNCATED {len(value) - 50} ITEMS]")
        return bounded_items
    if isinstance(value, str):
        redacted, _ = redact_sensitive_text(value)
        if len(redacted) > 4096:
            return f"{redacted[:4096]}[TRUNCATED {len(redacted) - 4096} CHARS]"
        return redacted
    return value


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = str(payload.get(field, "") or "").strip()
    if not value:
        raise ValueError(f"canonical tool event requires {field}")
    return value


def _stable_tool_event_id(
    *, session_id: str, turn_scope_id: str, call_id: str, phase: str
) -> str:
    key = (
        f"openminion:tool-transcript:v1:{session_id}:{turn_scope_id}:{call_id}:{phase}"
    )
    return uuid5(NAMESPACE_URL, key).hex


def _canonical_tool_payload(
    *, event_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    schema_version = payload.get("schema_version")
    if schema_version != 1:
        raise ValueError("canonical tool event requires schema_version=1")
    turn_scope_id = _required_text(payload, "turn_scope_id")
    call_id = _required_text(payload, "call_id")

    if event_type == "tool.call.requested":
        canonical_name = _required_text(payload, "canonical_name")
        arguments = payload.get("sanitized_normalized_arguments")
        if not isinstance(arguments, dict):
            raise ValueError(
                "tool.call.requested requires sanitized_normalized_arguments object"
            )
        batch_index = payload.get("batch_index")
        if isinstance(batch_index, bool) or not isinstance(batch_index, int):
            raise ValueError("tool.call.requested requires integer batch_index")
        depends_on = payload.get("depends_on")
        if not isinstance(depends_on, list) or not all(
            isinstance(item, str) and item.strip() for item in depends_on
        ):
            raise ValueError("tool.call.requested requires depends_on list[str]")
        return {
            "schema_version": 1,
            "turn_scope_id": turn_scope_id,
            "call_id": call_id,
            "canonical_name": canonical_name,
            "sanitized_normalized_arguments": _bounded_tool_value(arguments),
            "batch_index": max(0, batch_index),
            "depends_on": list(depends_on),
        }

    duplicated = {
        "arguments",
        "canonical_name",
        "name",
        "sanitized_normalized_arguments",
        "tool_arguments",
        "tool_name",
    }.intersection(payload)
    if duplicated:
        raise ValueError(
            "canonical tool result must not contain call-owned fields: "
            + ", ".join(sorted(duplicated))
        )
    status = _required_text(payload, "status").lower()
    if event_type == "tool.call.completed":
        if status != "success":
            raise ValueError("tool.call.completed requires status=success")
        if "error" in payload:
            raise ValueError("tool.call.completed must not contain error")
        return {
            "schema_version": 1,
            "turn_scope_id": turn_scope_id,
            "call_id": call_id,
            "status": "success",
            "output": _bounded_tool_value(payload.get("output")),
        }
    if status not in {"error", "blocked", "timeout"}:
        raise ValueError("tool.call.blocked requires status=error, blocked, or timeout")
    error = payload.get("error")
    if not isinstance(error, dict):
        raise ValueError("tool.call.blocked requires typed error object")
    return {
        "schema_version": 1,
        "turn_scope_id": turn_scope_id,
        "call_id": call_id,
        "status": status,
        "error": _bounded_tool_value(error),
    }


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

    def _prepare_tool_event(
        self,
        *,
        session_id: str,
        event_name: str,
        payload: dict[str, Any],
        parent_event_id: str | None,
    ) -> tuple[dict[str, Any], str | None]:
        is_canonical = event_name == "tool.call.requested" or (
            event_name in _TOOL_TRANSCRIPT_EVENTS and payload.get("schema_version") == 1
        )
        if not is_canonical:
            return payload, None

        payload = _canonical_tool_payload(event_type=event_name, payload=payload)
        phase = "requested" if event_name == "tool.call.requested" else "terminal"
        event_id = _stable_tool_event_id(
            session_id=session_id,
            turn_scope_id=str(payload["turn_scope_id"]),
            call_id=str(payload["call_id"]),
            phase=phase,
        )
        if event_name != "tool.call.requested":
            self._validate_tool_result_parent(
                session_id=session_id,
                parent_event_id=parent_event_id,
                payload=payload,
            )
        return payload, event_id

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
        if event_name in _UNSUPPORTED_TOOL_TERMINALS:
            raise ValueError(f"unsupported tool terminal event: {event_name}")

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
            str(trace_obj["trace_id"]) if trace_obj.get("trace_id") else trace_id
        )
        span_id_value = (
            str(trace_obj["span_id"]) if trace_obj.get("span_id") else span_id
        )
        task_id_value = (
            str(trace_obj["task_id"]) if trace_obj.get("task_id") else task_id
        )

        actor_type_value = str(actor_type).strip() or "system"
        actor_id_value = actor_id or agent_id
        if actor_id_value and actor_type_value == "system":
            actor_type_value = "agent"

        resolved_parent_id = parent_event_id or parent_id
        payload_obj, event_id = self._prepare_tool_event(
            session_id=session_id,
            event_name=event_name,
            payload=payload_obj,
            parent_event_id=resolved_parent_id,
        )
        if event_id is not None:
            redaction = "bounded"

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
                parent_event_id=resolved_parent_id,
                payload=payload_obj,
                refs=refs_obj or None,
                importance=importance,
                redaction=str(redaction or "none"),
                verify_session_exists=True,
                event_id=event_id,
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

    def _validate_tool_result_parent(
        self,
        *,
        session_id: str,
        parent_event_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        if not parent_event_id:
            raise ValueError("canonical tool result requires parent_event_id")
        parent = self._event_store.get_event_by_id(parent_event_id)
        if parent is None or parent.get("event_type") != "tool.call.requested":
            raise ValueError("canonical tool result parent is not tool.call.requested")
        if parent.get("session_id") != session_id:
            raise ValueError("canonical tool result parent belongs to another session")
        parent_payload = parent.get("payload")
        if not isinstance(parent_payload, dict):
            raise ValueError("canonical tool result parent payload is invalid")
        if parent_payload.get("call_id") != payload.get("call_id"):
            raise ValueError("canonical tool result call_id does not match parent")
        if parent_payload.get("turn_scope_id") != payload.get("turn_scope_id"):
            raise ValueError(
                "canonical tool result turn_scope_id does not match parent"
            )

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
        event_id: str | None = None,
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
            importance=max(0, min(importance, 3)),
            redaction=redaction,
            event_id=event_id,
        )
