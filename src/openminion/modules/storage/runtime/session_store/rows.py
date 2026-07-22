import json
from datetime import datetime, timezone
from typing import Any
from collections.abc import Mapping

from .models import (
    EventRecord,
    MessageRecord,
    RoomParticipant,
    SessionContextRecord,
    SessionRecord,
)

SESSION_COLUMNS = (
    "id, session_key, channel, target, metadata_json, created_at, updated_at, "
    "status, last_activity_at, closed_at, expires_at, active_agent_id"
)
MESSAGE_COLUMNS = (
    "id, session_id, conversation_id, thread_id, attach_id, role, body, "
    "metadata_json, created_at"
)
PARTICIPANT_COLUMNS = (
    "id, session_id, participant_type, participant_id, channel, role, "
    "display_name, joined_at, left_at"
)


def metadata_json(metadata: Mapping[str, Any] | None) -> str:
    payload = dict(metadata or {})
    return json.dumps(payload, sort_keys=True)


def parse_json_object(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        return parsed
    return {}


def normalize_nullable_text(raw: object) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    return value


def normalize_optional_text(raw: object) -> str:
    return str(raw or "").strip()


def parse_iso_datetime(raw: object) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_message_filters(
    *,
    session_id: str,
    conversation_id: str | None = None,
    thread_id: str | None = None,
) -> tuple[str, list[object]]:
    params: list[object] = [session_id]
    query = "WHERE session_id = ?"
    conversation_value = normalize_optional_text(conversation_id)
    thread_value = normalize_optional_text(thread_id)
    if conversation_value:
        query += "\nAND conversation_id = ?"
        params.append(conversation_value)
    if thread_value:
        query += "\nAND thread_id = ?"
        params.append(thread_value)
    return query, params


def build_session_context_update_values(
    *,
    current: SessionContextRecord,
    pinned_context: str | None = None,
    summary_short: str | None = None,
    rolling_summary: str | None = None,
    compacted_until_rowid: int | None = None,
    compacted_until_created_at: str | None = None,
    compacted_until_message_id: str | None = None,
    compacted_message_count: int | None = None,
    version: int | None = None,
) -> tuple[object, ...]:
    return (
        current.pinned_context if pinned_context is None else str(pinned_context),
        current.summary_short if summary_short is None else str(summary_short),
        current.rolling_summary if rolling_summary is None else str(rolling_summary),
        (
            current.compacted_until_rowid
            if compacted_until_rowid is None
            else max(0, int(compacted_until_rowid))
        ),
        (
            current.compacted_until_created_at
            if compacted_until_created_at is None
            else str(compacted_until_created_at)
        ),
        (
            current.compacted_until_message_id
            if compacted_until_message_id is None
            else str(compacted_until_message_id)
        ),
        (
            current.compacted_message_count
            if compacted_message_count is None
            else max(0, int(compacted_message_count))
        ),
        current.version if version is None else max(1, int(version)),
    )


def row_to_session(row: Mapping[str, Any]) -> SessionRecord:
    updated_at = str(row["updated_at"])
    last_activity_at = str(row["last_activity_at"] or "").strip() or updated_at
    status = str(row["status"] or "").strip() or "active"
    return SessionRecord(
        id=str(row["id"]),
        session_key=str(row["session_key"]),
        channel=str(row["channel"]),
        target=str(row["target"]),
        metadata=parse_json_object(str(row["metadata_json"])),
        created_at=str(row["created_at"]),
        updated_at=updated_at,
        status=status,
        last_activity_at=last_activity_at,
        closed_at=normalize_nullable_text(row["closed_at"]),
        expires_at=normalize_nullable_text(row["expires_at"]),
        active_agent_id=normalize_nullable_text(row.get("active_agent_id")),
    )


def row_to_message(row: Mapping[str, Any]) -> MessageRecord:
    return MessageRecord(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        conversation_id=str(row["conversation_id"]),
        thread_id=str(row["thread_id"]),
        attach_id=str(row["attach_id"]),
        role=str(row["role"]),
        body=str(row["body"]),
        metadata=parse_json_object(str(row["metadata_json"])),
        created_at=str(row["created_at"]),
        rowid=int(row["rowid"]),
    )


def row_to_event(row: Mapping[str, Any]) -> EventRecord:
    return EventRecord(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        event_type=str(row["event_type"]),
        payload=parse_json_object(str(row["payload_json"])),
        created_at=str(row["created_at"]),
    )


def row_to_session_context(row: Mapping[str, Any]) -> SessionContextRecord:
    return SessionContextRecord(
        session_id=str(row["session_id"]),
        pinned_context=str(row["pinned_context"]),
        summary_short=str(row.get("summary_short") or ""),
        rolling_summary=str(row["rolling_summary"]),
        compacted_until_rowid=int(row["compacted_until_rowid"]),
        compacted_until_created_at=str(row["compacted_until_created_at"]),
        compacted_until_message_id=str(row["compacted_until_message_id"]),
        compacted_message_count=int(row["compacted_message_count"]),
        version=int(row["version"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def row_to_participant(row: Mapping[str, Any]) -> RoomParticipant:
    return RoomParticipant(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        participant_type=str(row["participant_type"]),
        participant_id=str(row["participant_id"]),
        channel=str(row["channel"] or ""),
        role=str(row["role"] or "participant"),
        display_name=str(row["display_name"] or ""),
        joined_at=str(row["joined_at"]),
        left_at=normalize_nullable_text(row["left_at"]),
    )
