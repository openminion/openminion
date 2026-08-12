from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Error as SQLiteError
from typing import Any, Callable


@dataclass(frozen=True)
class DirectorySessionRecord:
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
    name: str = ""
    label: str = ""
    message_count: int = 0
    preview_line: str = ""


def build_directory_session_record(
    session: Any,
    *,
    store: Any,
    role_to_sender: Callable[[str, dict[str, object]], str],
) -> DirectorySessionRecord:
    session_id = str(getattr(session, "id", "") or "")
    metadata = getattr(session, "metadata", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    name = str(metadata.get("name", "") or "").strip()
    return DirectorySessionRecord(
        id=session_id,
        session_key=str(getattr(session, "session_key", "") or ""),
        channel=str(getattr(session, "channel", "") or ""),
        target=str(getattr(session, "target", "") or ""),
        metadata=dict(metadata),
        created_at=str(getattr(session, "created_at", "") or ""),
        updated_at=str(getattr(session, "updated_at", "") or ""),
        status=str(getattr(session, "status", "") or ""),
        last_activity_at=str(getattr(session, "last_activity_at", "") or ""),
        closed_at=getattr(session, "closed_at", None),
        expires_at=getattr(session, "expires_at", None),
        active_agent_id=getattr(session, "active_agent_id", None),
        name=name,
        label=name or session_id[:12],
        message_count=_session_message_count(store, session_id),
        preview_line=_session_preview_line(store, session_id, role_to_sender),
    )


def _session_message_count(store: Any, session_id: str) -> int:
    counter = getattr(store, "count_messages", None)
    if not callable(counter) or not session_id:
        return 0
    try:
        return int(counter(session_id=session_id) or 0)
    except (TypeError, ValueError, SQLiteError):
        return 0


def _session_preview_line(
    store: Any,
    session_id: str,
    role_to_sender: Callable[[str, dict[str, object]], str],
) -> str:
    if not session_id:
        return ""
    recent = getattr(store, "list_recent_messages", None)
    lister = recent if callable(recent) else getattr(store, "list_messages", None)
    if not callable(lister):
        return ""
    try:
        records = list(lister(session_id=session_id, limit=3) or [])
    except (TypeError, ValueError, SQLiteError):
        return ""
    for record in records:
        body = str(getattr(record, "body", "") or "").strip()
        if body:
            role = str(getattr(record, "role", "") or "").strip().lower()
            metadata = getattr(record, "metadata", {}) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            sender = role_to_sender(role, metadata)
            return f"{sender}: {body[:80]}"
    return ""
