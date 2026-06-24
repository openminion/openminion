from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4

from .backend import RuntimeSessionStoreBackend
from .keys import utc_now_iso
from .models import MessageRecord
from .rows import (
    build_message_filters,
    metadata_json,
    normalize_optional_text,
    row_to_message,
)


class RuntimeSessionStoreMessages:
    def __init__(self, backend: RuntimeSessionStoreBackend) -> None:
        self._backend = backend

    def append_message(
        self,
        *,
        session_id: str,
        conversation_id: str | None = None,
        thread_id: str | None = None,
        attach_id: str | None = None,
        role: str,
        body: str,
        metadata: Mapping[str, Any] | None = None,
        participant_id: str | None = None,
        participant_type: str | None = None,
        display_name: str | None = None,
    ) -> MessageRecord:
        now = utc_now_iso()
        message_id = uuid4().hex
        conversation_value = normalize_optional_text(conversation_id)
        thread_value = normalize_optional_text(thread_id)
        attach_value = normalize_optional_text(attach_id)
        payload_metadata = dict(metadata or {})
        participant_id_value = normalize_optional_text(participant_id)
        participant_type_value = normalize_optional_text(participant_type).lower()
        display_name_value = normalize_optional_text(display_name)
        if participant_id_value:
            payload_metadata["participant_id"] = participant_id_value
        if participant_type_value:
            payload_metadata["participant_type"] = participant_type_value
        if display_name_value:
            payload_metadata["display_name"] = display_name_value
        if conversation_value:
            payload_metadata.setdefault("conversation_id", conversation_value)
        if thread_value:
            payload_metadata.setdefault("thread_id", thread_value)
        if attach_value:
            payload_metadata.setdefault("attach_id", attach_value)

        with self._backend.transaction():
            self._backend.execute_count(
                """
                INSERT INTO messages(id, session_id, conversation_id, thread_id, attach_id, role, body, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    session_id,
                    conversation_value,
                    thread_value,
                    attach_value,
                    role,
                    body,
                    metadata_json(payload_metadata),
                    now,
                ),
            )
            self._backend.execute_count(
                "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE id = ?",
                (now, now, session_id),
            )
        return self._backend.message_by_id(message_id)

    def list_messages(
        self,
        *,
        session_id: str,
        limit: int = 100,
        conversation_id: str | None = None,
        thread_id: str | None = None,
    ) -> list[MessageRecord]:
        safe_limit = max(0, int(limit))
        if safe_limit == 0:
            return []
        where_clause, params = build_message_filters(
            session_id=session_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        rows = self._backend.message_query(
            where_clause=where_clause,
            params=params,
            newest_first=False,
            limit=safe_limit,
        )
        return [row_to_message(row) for row in rows]

    def list_recent_messages(
        self,
        *,
        session_id: str,
        limit: int = 100,
        conversation_id: str | None = None,
        thread_id: str | None = None,
    ) -> list[MessageRecord]:
        safe_limit = max(0, int(limit))
        if safe_limit == 0:
            return []
        where_clause, params = build_message_filters(
            session_id=session_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        rows = self._backend.message_query(
            where_clause=where_clause,
            params=params,
            newest_first=True,
            limit=safe_limit,
        )
        records = [row_to_message(row) for row in rows]
        records.reverse()
        return records

    def latest_conversation_id(self, *, session_id: str) -> str | None:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return None
        row = self._backend.query_one(
            """
            SELECT conversation_id
            FROM messages
            WHERE session_id = ?
              AND TRIM(COALESCE(conversation_id, '')) <> ''
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (normalized_session_id,),
        )
        if row is None:
            return None
        value = str(row["conversation_id"] or "").strip()
        return value or None

    def count_messages(
        self,
        *,
        session_id: str,
        conversation_id: str | None = None,
        thread_id: str | None = None,
    ) -> int:
        where_clause, params = build_message_filters(
            session_id=session_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        row = self._backend.query_one(
            f"SELECT COUNT(*) AS count FROM messages {where_clause}",
            params,
        )
        if row is None:
            return 0
        return int(row["count"])

    def list_messages_after_rowid(
        self,
        *,
        session_id: str,
        after_rowid: int = 0,
        limit: int = 100,
        conversation_id: str | None = None,
        thread_id: str | None = None,
    ) -> list[MessageRecord]:
        safe_limit = max(0, int(limit))
        if safe_limit == 0:
            return []
        where_clause, params = build_message_filters(
            session_id=session_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        rows = self._backend.message_query(
            where_clause=where_clause,
            params=params,
            newest_first=False,
            limit=safe_limit,
            after_rowid=after_rowid,
        )
        return [row_to_message(row) for row in rows]
