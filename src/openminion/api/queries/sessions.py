"""Session query helpers for the developer API."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import math
import re
import sqlite3
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, unquote
from uuid import uuid4

from openminion.api.config import close_api_runtime_if_owned, resolve_api_runtime
from openminion.api.runtime import APIRuntime
from openminion.base.config.core import resolve_default_agent_id
from openminion.base.time import utc_now_iso
from openminion.modules.context.trace_inspection import (
    ContextTraceLookupError,
    list_context_traces,
)
from openminion.modules.telemetry.events.catalog import DESKTOP_APPROVAL_REQUESTED


@dataclass
class SessionQueryError(RuntimeError):
    message: str
    code: str = "invalid_request"

    def __str__(self) -> str:
        return self.message


_CLIENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
_CLIENT_EVENT_FACTS = frozenset(
    {
        "step",
        "status",
        "previous_status",
        "reason",
        "reason_code",
        "closed_at",
        "agent_id",
        "response_id",
        "response_chars",
        "provider",
        "model",
        "delivery_mode",
        "channel",
        "target",
        "approval_id",
        "call_id",
        "command_id",
        "tool_name",
        "decision",
        "outcome",
        "requested_at",
        "expires_at",
        "resolved_at",
        "argument_keys_count",
        "duration_ms",
        "ok",
        "summary_status",
    }
)
_SESSION_LIST_LIMIT = 100
_MESSAGE_LIMIT = 200
_EVENT_LIMIT = 200
_SESSION_LIST_BYTES = 256 * 1024
_SESSION_LOAD_BYTES = 512 * 1024
_EVENT_BYTES = 256 * 1024
_EVENT_PAGE_BYTES = 1024 * 1024
CursorSigner = Callable[[bytes], bytes]
CursorVerifier = Callable[[bytes, bytes], bool]


def is_client_session(record: Any) -> bool:
    session_id = str(getattr(record, "id", "") or "")
    expires_at = str(getattr(record, "expires_at", "") or "")
    return (
        _CLIENT_ID.fullmatch(session_id) is not None
        and getattr(record, "channel", None) == "console"
        and getattr(record, "target", None) == "api-user"
        and getattr(record, "status", None)
        in {"active", "idle", "paused", "stale", "closed"}
        and (not expires_at or expires_at > utc_now_iso())
    )


def client_session_payload(record: Any, *, event_cursor: str) -> dict[str, Any]:
    if not is_client_session(record):
        raise SessionQueryError(
            "Session is not available to this client.",
            code="session_not_found",
        )
    metadata = getattr(record, "metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    active_agent = str(getattr(record, "active_agent_id", "") or "").strip()
    if not active_agent:
        active_agent = _agent_id_from_session_key(str(record.session_key or ""))
    return {
        "schema_version": 1,
        "session_id": record.id,
        "title": _bounded_metadata_text(metadata, "desktop_title"),
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "active_agent_id": active_agent or None,
        "model": {
            "provider": _bounded_metadata_text(metadata, "desktop_model_provider"),
            "model_id": _bounded_metadata_text(metadata, "desktop_model_id"),
        },
        "project_id": _bounded_metadata_text(metadata, "desktop_project_id"),
        "event_cursor": event_cursor,
    }


def client_message_payload(record: Any) -> dict[str, Any] | None:
    roles = {"inbound": "user", "outbound": "assistant"}
    role = roles.get(str(getattr(record, "role", "") or ""))
    if role is None:
        return None
    return {
        "schema_version": 1,
        "id": str(record.id),
        "session_id": str(record.session_id),
        "role": role,
        "body": str(record.body),
        "created_at": str(record.created_at),
    }


def client_event_payload(
    record: Any,
    *,
    cursor: str,
    approval_recovery: Callable[[str, str, str], str] | None = None,
) -> dict[str, Any]:
    payload = getattr(record, "payload", {})
    payload = payload if isinstance(payload, dict) else {}
    facts: dict[str, Any] = {
        key: value
        for key in _CLIENT_EVENT_FACTS
        if (value := _client_fact(payload.get(key))) is not None
    }
    if "argument_keys" in payload:
        facts["argument_keys"] = _client_argument_keys(
            payload["argument_keys"],
            expected_count=facts.get("argument_keys_count"),
        )
    trace_id = _optional_identifier(payload.get("request_id")) or _optional_identifier(
        getattr(record, "trace_id", None)
    )
    if str(record.event_type) == DESKTOP_APPROVAL_REQUESTED and approval_recovery:
        approval_id = _optional_identifier(payload.get("approval_id"))
        expires_at = _optional_identifier(payload.get("expires_at"))
        if approval_id is None or trace_id is None or expires_at is None:
            raise SessionQueryError(
                "Approval recovery facts are invalid.",
                code="event_invalid",
            )
        facts["outcome"] = approval_recovery(trace_id, approval_id, expires_at)
    return {
        "schema_version": 1,
        "event_type": str(record.event_type),
        "cursor": cursor,
        "created_at": str(record.created_at),
        "run_id": _optional_identifier(payload.get("run_id")),
        "trace_id": trace_id,
        "state": _optional_identifier(payload.get("state")),
        "facts": facts,
    }


def _bounded_metadata_text(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text and len(text.encode("utf-8")) <= 256 else None


def _optional_identifier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _client_fact(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value if len(value.encode("utf-8")) <= 4 * 1024 else None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _client_argument_keys(value: Any, *, expected_count: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 64:
        raise SessionQueryError(
            "Approval argument keys are invalid.", code="event_invalid"
        )
    if any(
        not isinstance(item, str) or len(item.encode("utf-8")) > 256 for item in value
    ):
        raise SessionQueryError(
            "Approval argument keys are invalid.", code="event_invalid"
        )
    if (
        type(expected_count) is not int
        or expected_count != len(value)
        or value != sorted(set(value))
        or _compact_size(value) > 8 * 1024
    ):
        raise SessionQueryError(
            "Approval argument keys are invalid.", code="event_invalid"
        )
    return list(value)


def list_client_session_page(
    runtime: Any,
    *,
    query: str | None,
    sign_cursor: CursorSigner,
    verify_cursor: CursorVerifier,
    request_id: str,
    path: str,
) -> dict[str, Any]:
    fields = _query_fields(query, {"cursor", "limit"})
    limit = _bounded_int(fields.get("limit"), default=50, maximum=_SESSION_LIST_LIMIT)
    cursor = _decode_cursor(
        fields.get("cursor"),
        kind="sessions",
        verify_cursor=verify_cursor,
    )
    records = runtime.sessions.list_client_sessions(
        limit=limit + 1,
        before_updated_at=str(cursor.get("updated_at", "")) or None,
        before_session_id=str(cursor.get("session_id", "")) or None,
    )
    items: list[dict[str, Any]] = []
    next_cursor = fields.get("cursor")
    consumed = 0
    for record in records[:limit]:
        item = client_session_payload(
            record,
            event_cursor=_encode_cursor(
                kind="events",
                session_id=record.id,
                position=0,
                sign_cursor=sign_cursor,
            ),
        )
        candidate_cursor = _encode_cursor(
            kind="sessions",
            session_id=str(record.id),
            updated_at=str(record.updated_at),
            sign_cursor=sign_cursor,
        )
        candidate = {
            "ok": True,
            "sessions": [*items, item],
            "next_cursor": candidate_cursor,
            "has_more": True,
        }
        if (
            _response_size(candidate, request_id=request_id, path=path)
            > _SESSION_LIST_BYTES
        ):
            if not items:
                raise SessionQueryError(
                    "A session summary exceeds the client response limit.",
                    code="session_too_large",
                )
            break
        items.append(item)
        next_cursor = candidate_cursor
        consumed += 1
    return {
        "ok": True,
        "sessions": items,
        "next_cursor": next_cursor,
        "has_more": consumed < len(records),
    }


def create_client_session(
    runtime: Any,
    *,
    body: dict[str, Any] | None,
    query: str | None,
    sign_cursor: CursorSigner,
) -> tuple[dict[str, Any], str]:
    if query:
        raise SessionQueryError("Session creation does not accept query fields.")
    payload = body or {}
    if set(payload) - {"agent_id", "title", "project_id"}:
        raise SessionQueryError("Session creation contains unknown fields.")
    agent_id = _optional_scalar(payload.get("agent_id"), "agent_id")
    selected_agent = agent_id or resolve_default_agent_id(runtime.config)
    if selected_agent not in runtime.config.agents:
        raise SessionQueryError("Unknown agent profile.")
    record = runtime.sessions.resolve_session(
        agent_id=selected_agent,
        channel="console",
        target="api-user",
        session_id=uuid4().hex,
        metadata={
            key: value
            for key, value in {
                "desktop_title": _optional_scalar(payload.get("title"), "title"),
                "desktop_project_id": _optional_scalar(
                    payload.get("project_id"), "project_id"
                ),
            }.items()
            if value is not None
        },
    )
    return (
        {
            "ok": True,
            "session": client_session_payload(
                record,
                event_cursor=_encode_cursor(
                    kind="events",
                    session_id=record.id,
                    position=0,
                    sign_cursor=sign_cursor,
                ),
            ),
        },
        record.id,
    )


def load_client_session(
    runtime: Any,
    *,
    session_id: str,
    query: str | None,
    sign_cursor: CursorSigner,
    verify_cursor: CursorVerifier,
    request_id: str,
    path: str,
) -> dict[str, Any]:
    fields = _query_fields(query, {"message_after", "message_limit"})
    limit = _bounded_int(
        fields.get("message_limit"), default=100, maximum=_MESSAGE_LIMIT
    )
    cursor = _decode_cursor(
        fields.get("message_after"),
        kind="messages",
        session_id=session_id,
        verify_cursor=verify_cursor,
    )
    position = int(cursor.get("position", 0))
    record = _client_session(runtime, session_id)
    _validate_message_position(runtime, session_id, position)
    rows = runtime.sessions.list_messages_after_rowid(
        session_id=session_id,
        after_rowid=position,
        limit=limit + 1,
    )
    session = client_session_payload(
        record,
        event_cursor=_encode_cursor(
            kind="events",
            session_id=session_id,
            position=0,
            sign_cursor=sign_cursor,
        ),
    )
    messages: list[dict[str, Any]] = []
    next_position = position
    consumed = 0
    for row in rows[:limit]:
        message = client_message_payload(row)
        if message is not None and len(message["body"].encode("utf-8")) > 256 * 1024:
            raise SessionQueryError(
                "A session message exceeds the client message limit.",
                code="message_too_large",
            )
        candidate_messages = messages if message is None else [*messages, message]
        candidate_cursor = _encode_cursor(
            kind="messages",
            session_id=session_id,
            position=row.rowid,
            sign_cursor=sign_cursor,
        )
        candidate = {
            "ok": True,
            "session": session,
            "messages": candidate_messages,
            "next_message_cursor": candidate_cursor,
            "messages_has_more": True,
        }
        if (
            _response_size(
                candidate,
                request_id=request_id,
                path=path,
                session_id=session_id,
            )
            > _SESSION_LOAD_BYTES
        ):
            if not messages:
                raise SessionQueryError(
                    "A session message exceeds the client response limit.",
                    code="message_too_large",
                )
            break
        messages = candidate_messages
        next_position = row.rowid
        consumed += 1
    return {
        "ok": True,
        "session": session,
        "messages": messages,
        "next_message_cursor": _encode_cursor(
            kind="messages",
            session_id=session_id,
            position=next_position,
            sign_cursor=sign_cursor,
        ),
        "messages_has_more": consumed < len(rows),
    }


def close_client_session(
    runtime: Any,
    *,
    session_id: str,
    body: dict[str, Any] | None,
    query: str | None,
    sign_cursor: CursorSigner,
    cancel_pending: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if query:
        raise SessionQueryError("Session close does not accept query fields.")
    payload = body or {}
    if set(payload) - {"reason"}:
        raise SessionQueryError("Session close contains unknown fields.")
    _client_session(runtime, session_id)
    if cancel_pending is not None:
        cancel_pending(session_id)
    record = runtime.sessions.close_session(
        session_id=session_id,
        reason=_optional_scalar(payload.get("reason"), "reason") or "desktop_close",
    )
    return {
        "ok": True,
        "session": client_session_payload(
            record,
            event_cursor=_encode_cursor(
                kind="events",
                session_id=session_id,
                position=0,
                sign_cursor=sign_cursor,
            ),
        ),
    }


def list_client_event_page(
    runtime: Any,
    *,
    session_id: str,
    query: str | None,
    sign_cursor: CursorSigner,
    verify_cursor: CursorVerifier,
    request_id: str,
    path: str,
    approval_recovery: Callable[[str, str, str], str] | None = None,
) -> dict[str, Any]:
    fields = _query_fields(query, {"after", "limit"})
    limit = _bounded_int(fields.get("limit"), default=100, maximum=_EVENT_LIMIT)
    cursor = _decode_cursor(
        fields.get("after"),
        kind="events",
        session_id=session_id,
        verify_cursor=verify_cursor,
    )
    position = int(cursor.get("position", 0))
    _client_session(runtime, session_id)
    high_water = runtime.sessions.event_high_water(session_id=session_id)
    _validate_event_position(runtime, session_id, position, high_water)
    rows = runtime.sessions.list_events_after_id(
        session_id=session_id,
        after_id=position,
        high_water_id=high_water,
        limit=limit + 1,
    )
    events: list[dict[str, Any]] = []
    next_position = position
    consumed = 0
    for row in rows[:limit]:
        candidate_cursor = _encode_cursor(
            kind="events",
            session_id=session_id,
            position=row.id,
            sign_cursor=sign_cursor,
        )
        event = client_event_payload(
            row,
            cursor=candidate_cursor,
            approval_recovery=approval_recovery,
        )
        if _compact_size(event) > _EVENT_BYTES:
            raise SessionQueryError(
                "A canonical event exceeds the client event limit.",
                code="event_too_large",
            )
        candidate = {
            "ok": True,
            "events": [*events, event],
            "next_cursor": candidate_cursor,
            "has_more": True,
        }
        if (
            _response_size(
                candidate,
                request_id=request_id,
                path=path,
                session_id=session_id,
            )
            > _EVENT_PAGE_BYTES
        ):
            if not events:
                raise SessionQueryError(
                    "A canonical event exceeds the client event-page limit.",
                    code="event_too_large",
                )
            break
        events.append(event)
        next_position = row.id
        consumed += 1
    return {
        "ok": True,
        "events": events,
        "next_cursor": _encode_cursor(
            kind="events",
            session_id=session_id,
            position=next_position,
            sign_cursor=sign_cursor,
        ),
        "has_more": consumed < len(rows),
    }


def cancel_client_turn(
    manager: Any,
    runtime: Any,
    *,
    session_id: str,
    trace_id: str,
) -> dict[str, Any]:
    _client_session(runtime, session_id)
    correlation = _run_correlation(runtime, session_id, trace_id)
    if correlation is not None and correlation[1] in {
        "completed",
        "failed",
        "cancelled",
    }:
        return _cancellation(
            session_id,
            trace_id,
            run_id=correlation[0],
            state=correlation[1],
            terminal=True,
        )
    state = str(manager.cancel_session_turn(trace_id, session_id))
    if state == "session_mismatch":
        return _cancellation_error("trace_session_mismatch")
    if state == "not_active":
        return _inactive_cancellation(runtime, session_id, trace_id)
    run_id = correlation[0] if correlation is not None else None
    try:
        cancellation_event, inserted = runtime.sessions.append_cancel_request_once(
            session_id=session_id,
            request_id=trace_id,
            run_id=run_id,
        )
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        return _cancellation_error(
            "cancellation_event_failed",
            run_id=run_id,
            retryable=True,
        )
    if cancellation_event.event_type in {
        "run.completed",
        "run.failed",
        "run.cancelled",
    }:
        return _terminal_cancellation(session_id, trace_id, cancellation_event)
    if not inserted:
        state = "already_requested"
    return _cancellation(
        session_id,
        trace_id,
        run_id=run_id,
        state=state,
        terminal=False,
    )


def _inactive_cancellation(
    runtime: Any,
    session_id: str,
    trace_id: str,
) -> dict[str, Any]:
    correlation = _run_correlation(runtime, session_id, trace_id)
    if correlation is None:
        return _cancellation_error("trace_not_found")
    run_id, state = correlation
    if state in {"completed", "failed", "cancelled"}:
        return _cancellation(
            session_id,
            trace_id,
            run_id=run_id,
            state=state,
            terminal=True,
        )
    return _cancellation_error("stale_trace", run_id=run_id)


def _terminal_cancellation(
    session_id: str,
    trace_id: str,
    event: Any,
) -> dict[str, Any]:
    run_id = str(event.payload.get("run_id", "") or "").strip() or None
    state = str(event.payload.get("state", "") or "").strip()
    state = state or str(event.event_type).removeprefix("run.")
    return _cancellation(
        session_id,
        trace_id,
        run_id=run_id,
        state=state,
        terminal=True,
    )


def _run_correlation(
    runtime: Any,
    session_id: str,
    trace_id: str,
) -> tuple[str, str] | None:
    event = runtime.sessions.latest_run_event_for_request(
        session_id=session_id,
        request_id=trace_id,
    )
    if event is None:
        return None
    run_id = str(event.payload.get("run_id", "") or "").strip()
    state = str(event.payload.get("state", "") or "").strip()
    if not state and event.event_type.startswith("run."):
        state = event.event_type.removeprefix("run.")
    return (run_id, state) if run_id and state else None


def _cancellation(
    session_id: str,
    trace_id: str,
    *,
    run_id: str | None,
    state: str,
    terminal: bool,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "trace_id": trace_id,
        "run_id": run_id,
        "state": state,
        "terminal": terminal,
    }


def _cancellation_error(
    code: str,
    *,
    run_id: str | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    return {"error": code, "run_id": run_id, "retryable": retryable}


def _client_session(runtime: Any, session_id: str) -> Any:
    if not _valid_identifier(session_id):
        raise SessionQueryError("Session was not found.", code="session_not_found")
    record = runtime.sessions.get_session(session_id)
    if record is None or not is_client_session(record):
        raise SessionQueryError("Session was not found.", code="session_not_found")
    return record


def _validate_message_position(runtime: Any, session_id: str, position: int) -> None:
    high_water = runtime.sessions.message_high_water(session_id=session_id)
    if position < 0 or position > high_water:
        raise SessionQueryError("Message cursor is invalid.", code="invalid_cursor")
    if not runtime.sessions.message_cursor_exists(
        session_id=session_id, rowid=position
    ):
        raise SessionQueryError("Message cursor has expired.", code="cursor_expired")


def _validate_event_position(
    runtime: Any,
    session_id: str,
    position: int,
    high_water: int,
) -> None:
    if position < 0 or position > high_water:
        raise SessionQueryError("Event cursor is invalid.", code="invalid_cursor")
    if not runtime.sessions.event_cursor_exists(
        session_id=session_id, event_id=position
    ):
        raise SessionQueryError("Event cursor has expired.", code="cursor_expired")


def _query_fields(query: str | None, admitted: set[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key, value in parse_qsl(str(query or ""), keep_blank_values=True):
        if key not in admitted or key in fields or not value.strip():
            raise SessionQueryError("Query fields are invalid.")
        fields[key] = value.strip()
    return fields


def _bounded_int(raw: str | None, *, default: int, maximum: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SessionQueryError("Page limit is invalid.") from exc
    if value < 1 or value > maximum:
        raise SessionQueryError("Page limit is out of range.")
    return value


def _optional_scalar(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SessionQueryError(f"{name} must be a string.")
    text = value.strip()
    if not text or len(text.encode("utf-8")) > 256:
        raise SessionQueryError(f"{name} is invalid.")
    return text


def _valid_identifier(value: str) -> bool:
    return _CLIENT_ID.fullmatch(value) is not None


def _encode_cursor(
    *,
    kind: str,
    sign_cursor: CursorSigner,
    session_id: str = "",
    position: int | None = None,
    updated_at: str = "",
) -> str:
    payload: dict[str, Any] = {"v": 1, "kind": kind, "session_id": session_id}
    payload["updated_at" if kind == "sessions" else "position"] = (
        updated_at if kind == "sessions" else int(position or 0)
    )
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"{_b64(raw)}.{_b64(sign_cursor(raw))}"


def _decode_cursor(
    raw_cursor: str | None,
    *,
    kind: str,
    verify_cursor: CursorVerifier,
    session_id: str = "",
) -> dict[str, Any]:
    if raw_cursor is None:
        return (
            {"v": 1, "kind": kind, "session_id": "", "updated_at": ""}
            if kind == "sessions"
            else {"v": 1, "kind": kind, "session_id": session_id, "position": 0}
        )
    try:
        encoded, encoded_signature = raw_cursor.split(".", 1)
        raw = _unb64(encoded)
        signature = _unb64(encoded_signature)
        payload = json.loads(raw.decode("utf-8"))
    except (
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
    ) as exc:
        raise SessionQueryError("Cursor is invalid.", code="invalid_cursor") from exc
    expected = (
        {"v", "kind", "session_id", "updated_at"}
        if kind == "sessions"
        else {"v", "kind", "session_id", "position"}
    )
    if (
        not verify_cursor(raw, signature)
        or not isinstance(payload, dict)
        or set(payload) != expected
        or payload.get("v") != 1
        or payload.get("kind") != kind
        or (kind != "sessions" and payload.get("session_id") != session_id)
    ):
        raise SessionQueryError("Cursor is invalid.", code="invalid_cursor")
    if kind == "sessions":
        values = (payload.get("session_id"), payload.get("updated_at"))
        if not all(isinstance(value, str) for value in values):
            raise SessionQueryError("Cursor is invalid.", code="invalid_cursor")
        if not _valid_identifier(str(values[0])) or not values[1]:
            raise SessionQueryError("Cursor is invalid.", code="invalid_cursor")
    elif type(payload.get("position")) is not int:
        raise SessionQueryError("Cursor is invalid.", code="invalid_cursor")
    return payload


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    decoded = base64.b64decode(
        value + "=" * (-len(value) % 4),
        altchars=b"-_",
        validate=True,
    )
    if not value or _b64(decoded) != value:
        raise binascii.Error("non-canonical base64")
    return decoded


def _response_size(
    payload: dict[str, Any],
    *,
    request_id: str,
    path: str,
    session_id: str | None = None,
) -> int:
    response = dict(payload)
    meta = {"request_id": request_id, "method": "GET", "path": path}
    if session_id:
        meta["session_id"] = session_id
    response["meta"] = meta
    return _compact_size(response)


def _compact_size(payload: object) -> int:
    return len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _agent_id_from_session_key(session_key: str) -> str:
    if session_key.strip().lower().startswith("room:"):
        return ""
    for part in session_key.split("|"):
        if part.startswith("agent:"):
            return unquote(part.removeprefix("agent:"))
    return ""


def list_session_messages(
    config_path: str | None,
    *,
    session_id: str,
    limit: int = 100,
    runtime: APIRuntime | None = None,
) -> dict[str, Any]:
    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        raise SessionQueryError("`session_id` is required.", code="invalid_request")

    safe_limit = max(1, min(limit, 500))

    active_runtime, own_runtime = resolve_api_runtime(
        config_path=config_path,
        runtime=runtime,
    )
    try:
        session = active_runtime.sessions.get_session(normalized_session_id)
        if session is None:
            raise SessionQueryError(
                f"Session '{normalized_session_id}' was not found.",
                code="session_not_found",
            )

        records = active_runtime.sessions.list_messages(
            session_id=normalized_session_id, limit=safe_limit
        )
        messages: list[dict[str, Any]] = [
            {
                "id": record.id,
                "session_id": record.session_id,
                "role": record.role,
                "body": record.body,
                "metadata": record.metadata,
                "created_at": record.created_at,
            }
            for record in records
        ]
        return {
            "session": {
                "id": session.id,
                "channel": session.channel,
                "target": session.target,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
            },
            "messages": messages,
            "limit": safe_limit,
        }
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)


def append_session_event(
    config_path: str | None,
    *,
    session_id: str,
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    runtime: APIRuntime | None = None,
) -> dict[str, Any]:
    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        raise SessionQueryError("`session_id` is required.", code="invalid_request")
    normalized_event = str(event_type or "").strip()
    if not normalized_event:
        raise SessionQueryError("`event_type` is required.", code="invalid_request")

    active_runtime, own_runtime = resolve_api_runtime(
        config_path=config_path,
        runtime=runtime,
    )
    try:
        session = active_runtime.sessions.get_session(normalized_session_id)
        if session is None:
            raise SessionQueryError(
                f"Session '{normalized_session_id}' was not found.",
                code="session_not_found",
            )
        event = active_runtime.sessions.append_event(
            session_id=normalized_session_id,
            event_type=normalized_event,
            payload=dict(payload or {}),
        )
        return {
            "session_id": normalized_session_id,
            "event": {
                "id": event.id,
                "session_id": event.session_id,
                "event_type": event.event_type,
                "payload": event.payload,
                "created_at": event.created_at,
            },
        }
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)


def list_session_context_traces(
    config_path: str | None,
    *,
    session_id: str,
    turn_id: str | None = None,
    limit: int = 50,
    runtime: APIRuntime | None = None,
) -> dict[str, Any]:
    active_runtime, own_runtime = resolve_api_runtime(
        config_path=config_path,
        runtime=runtime,
    )
    try:
        return list_context_traces(
            active_runtime.sessions,
            session_id=session_id,
            turn_id=turn_id,
            limit=limit,
        )
    except ContextTraceLookupError as exc:
        raise SessionQueryError(str(exc), code=exc.code) from exc
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)
