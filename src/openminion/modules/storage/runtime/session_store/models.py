from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SessionRecord:
    id: str
    session_key: str
    channel: str
    target: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    status: str
    last_activity_at: str
    closed_at: str | None
    expires_at: str | None
    active_agent_id: str | None = None


@dataclass(frozen=True)
class RoomParticipant:
    id: str
    session_id: str
    participant_type: str
    participant_id: str
    channel: str
    role: str
    display_name: str
    joined_at: str
    left_at: str | None


@dataclass(frozen=True)
class MessageRecord:
    id: str
    session_id: str
    conversation_id: str
    thread_id: str
    attach_id: str
    role: str
    body: str
    metadata: dict[str, Any]
    created_at: str
    rowid: int = 0


@dataclass(frozen=True)
class EventRecord:
    id: int
    session_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class SessionContextRecord:
    session_id: str
    pinned_context: str
    summary_short: str
    rolling_summary: str
    compacted_until_rowid: int
    compacted_until_created_at: str
    compacted_until_message_id: str
    compacted_message_count: int
    version: int
    created_at: str
    updated_at: str
