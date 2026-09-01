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

    @property
    def owner_agent_id(self) -> str:
        if self.active_agent_id:
            return self.active_agent_id
        from .keys import agent_id_from_session_key

        return agent_id_from_session_key(self.session_key)


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
    canonical_event_id: str | None = None


@dataclass(frozen=True)
class MemoryCaptureRetentionHoldRecord:
    hold_id: str
    session_id: str
    reason: str
    actor_id: str
    created_at: str
    released_at: str | None
    schema_version: str


@dataclass(frozen=True)
class CaptureEventCommitRecord:
    event: EventRecord
    retention_hold: MemoryCaptureRetentionHoldRecord | None
    replayed: bool


class RuntimeSessionStoreIntegrityError(RuntimeError):
    code = "SESSION_STORE_INTEGRITY_CONFLICT"

    def __init__(self, message: str, *, canonical_event_id: str) -> None:
        self.canonical_event_id = canonical_event_id
        super().__init__(message)


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
