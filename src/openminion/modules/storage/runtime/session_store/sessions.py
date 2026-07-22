from __future__ import annotations

from typing import Any, Callable, Mapping
from urllib.parse import quote
from uuid import uuid4

from .backend import RuntimeSessionStoreBackend
from .keys import (
    agent_id_from_session_key,
    build_explicit_session_key,
    build_room_session_key,
    build_session_key,
    normalize_identity,
    normalize_session_status,
    utc_now_iso,
)
from .models import RoomParticipant, SessionRecord
from .rows import (
    SESSION_COLUMNS,
    metadata_json,
    normalize_nullable_text,
    normalize_optional_text,
    row_to_session,
)


class RuntimeSessionStoreSessions:
    def __init__(
        self,
        backend: RuntimeSessionStoreBackend,
        *,
        list_participants: Callable[[str], list[RoomParticipant]],
    ) -> None:
        self._backend = backend
        self._list_participants = list_participants

    def _resolve_existing_explicit_session(
        self, explicit_id: str, *, agent_id: str
    ) -> SessionRecord | None:
        existing = self.get_session(explicit_id)
        if existing is None:
            return None

        normalized_agent = normalize_identity(agent_id) if agent_id else ""
        if normalized_agent:
            participants = self._list_participants(existing.id)
            agent_participants = {
                item.participant_id: item
                for item in participants
                if item.participant_type == "agent"
            }
            if agent_participants:
                if normalized_agent not in agent_participants:
                    raise ValueError(
                        f"Session {explicit_id!r} does not include agent "
                        f"{normalized_agent!r}"
                    )
                return existing

        session_agent = agent_id_from_session_key(existing.session_key)
        if normalized_agent and session_agent and session_agent != normalized_agent:
            raise ValueError(
                f"Session {explicit_id!r} belongs to agent "
                f"{session_agent!r}, not {normalized_agent!r}"
            )
        return existing

    def _create_explicit_session(
        self,
        *,
        explicit_id: str,
        agent_id: str,
        channel: str,
        target: str,
        metadata: Mapping[str, Any] | None,
    ) -> SessionRecord:
        now = utc_now_iso()
        self.insert_session(
            session_id=explicit_id,
            session_key=build_explicit_session_key(
                agent_id=agent_id,
                channel=channel,
                target=target,
                session_id=explicit_id,
            ),
            channel=channel,
            target=target,
            session_metadata_json=metadata_json(metadata),
            created_at=now,
            updated_at=now,
        )
        created = self.get_session(explicit_id)
        if created is None:
            raise RuntimeError(f"Failed to create explicit session record id={explicit_id}")
        return created

    def resolve_session(
        self,
        *,
        agent_id: str,
        channel: str,
        target: str,
        session_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SessionRecord:
        normalized_channel = normalize_identity(channel)
        normalized_target = normalize_identity(target)

        explicit_id = (session_id or "").strip()
        if explicit_id:
            existing = self._resolve_existing_explicit_session(
                explicit_id, agent_id=agent_id
            )
            if existing is not None:
                return existing

            return self._create_explicit_session(
                explicit_id=explicit_id,
                agent_id=agent_id,
                channel=normalized_channel,
                target=normalized_target,
                metadata=metadata,
            )

        session_key = build_session_key(
            agent_id=agent_id, channel=channel, target=target
        )
        row = self._backend.query_one(
            f"SELECT {SESSION_COLUMNS} FROM sessions WHERE session_key = ?",
            (session_key,),
        )
        if row is not None:
            if metadata:
                now = utc_now_iso()
                self._backend.execute_count(
                    "UPDATE sessions SET metadata_json = ?, updated_at = ? WHERE id = ?",
                    (metadata_json(metadata), now, str(row["id"])),
                )
                refreshed = self._backend.query_one(
                    f"SELECT {SESSION_COLUMNS} FROM sessions WHERE id = ?",
                    (str(row["id"]),),
                )
                if refreshed is not None:
                    return row_to_session(refreshed)
            return row_to_session(row)

        now = utc_now_iso()
        session_id_value = uuid4().hex
        self.insert_session(
            session_id=session_id_value,
            session_key=session_key,
            channel=normalized_channel,
            target=normalized_target,
            session_metadata_json=metadata_json(metadata),
            created_at=now,
            updated_at=now,
        )
        created = self.get_session(session_id_value)
        if created is None:
            raise RuntimeError(f"Failed to create session record for key={session_key}")
        return created

    def create_room(
        self,
        *,
        channel: str,
        target: str,
        session_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        active_agent_id: str | None = None,
    ) -> SessionRecord:
        normalized_channel = normalize_identity(channel or "cli")
        normalized_target = normalize_identity(target or "room")
        room_session_id = str(session_id or f"room-{uuid4().hex}").strip()
        if not room_session_id:
            room_session_id = f"room-{uuid4().hex}"
        if self.get_session(room_session_id) is not None:
            raise ValueError(f"Session already exists: {room_session_id}")
        now = utc_now_iso()
        self.insert_session(
            session_id=room_session_id,
            session_key=build_room_session_key(session_id=room_session_id),
            channel=normalized_channel,
            target=normalized_target,
            session_metadata_json=metadata_json(metadata),
            created_at=now,
            updated_at=now,
            active_agent_id=active_agent_id,
        )
        created = self.get_session(room_session_id)
        if created is None:
            raise RuntimeError(f"Failed to create room session {room_session_id}")
        return created

    def get_session(self, session_id: str) -> SessionRecord | None:
        row = self._backend.query_one(
            f"SELECT {SESSION_COLUMNS} FROM sessions WHERE id = ?",
            (session_id,),
        )
        if row is None:
            return None
        return row_to_session(row)

    def count_sessions(self) -> int:
        row = self._backend.query_one("SELECT COUNT(*) AS count FROM sessions")
        if row is None:
            return 0
        return int(row["count"])

    def list_sessions(
        self,
        *,
        limit: int = 100,
        newest_first: bool = True,
        agent_id: str | None = None,
        status: str | None = None,
        channel: str | None = None,
        target: str | None = None,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[SessionRecord]:
        safe_limit = max(0, int(limit))
        if safe_limit == 0:
            return []
        direction = "DESC" if newest_first else "ASC"
        clauses: list[str] = []
        params: list[object] = []
        normalized_agent = normalize_optional_text(agent_id).lower()
        normalized_status = normalize_optional_text(status).lower()
        normalized_channel = normalize_optional_text(channel).lower()
        normalized_target = normalize_optional_text(target).lower()
        if normalized_agent:
            encoded_agent = quote(normalized_agent, safe="")
            clauses.append(
                "(session_key LIKE ? OR LOWER(COALESCE(active_agent_id, '')) LIKE ?)"
            )
            params.extend([f"agent:{encoded_agent}%|%", f"{normalized_agent}%"])
        if normalized_status:
            clauses.append("status = ?")
            params.append(normalize_session_status(normalized_status))
        if normalized_channel:
            clauses.append("channel = ?")
            params.append(normalized_channel)
        if normalized_target:
            clauses.append("target = ?")
            params.append(normalized_target)
        if metadata_filter:
            for key, value in metadata_filter.items():
                normalized_key = str(key or "").strip()
                if not normalized_key:
                    continue
                clauses.append(f"json_extract(metadata_json, '$.{normalized_key}') = ?")
                params.append(str(value or ""))
        where_clause = ""
        if clauses:
            where_clause = "WHERE " + " AND ".join(clauses)
        rows = self._backend.query_dicts(
            f"""
            SELECT {SESSION_COLUMNS}
            FROM sessions
            {where_clause}
            ORDER BY updated_at {direction}, id {direction}
            LIMIT ?
            """,
            [*params, safe_limit],
        )
        return [row_to_session(row) for row in rows]

    def delete_session(self, session_id: str) -> bool:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return False
        with self._backend.transaction():
            self._backend.execute_count(
                "DELETE FROM messages WHERE session_id = ?",
                (normalized_session_id,),
            )
            self._backend.execute_count(
                "DELETE FROM events WHERE session_id = ?",
                (normalized_session_id,),
            )
            self._backend.execute_count(
                "DELETE FROM session_contexts WHERE session_id = ?",
                (normalized_session_id,),
            )
            deleted = self._backend.execute_count(
                "DELETE FROM sessions WHERE id = ?",
                (normalized_session_id,),
            )
        return deleted > 0

    def update_session_metadata(
        self,
        *,
        session_id: str,
        patch: Mapping[str, Any],
    ) -> SessionRecord:
        current = self.get_session(session_id)
        if current is None:
            raise ValueError(f"Session not found: {session_id}")
        merged = dict(current.metadata)
        merged.update(patch)
        now = utc_now_iso()
        self._backend.execute_count(
            "UPDATE sessions SET metadata_json = ?, updated_at = ? WHERE id = ?",
            (metadata_json(merged), now, session_id),
        )
        updated = self.get_session(session_id)
        if updated is None:
            raise RuntimeError(f"Failed to update metadata for session_id={session_id}")
        return updated

    def insert_session(
        self,
        *,
        session_id: str,
        session_key: str,
        channel: str,
        target: str,
        session_metadata_json: str,
        created_at: str,
        updated_at: str,
        active_agent_id: str | None = None,
    ) -> None:
        self._backend.execute_count(
            """
            INSERT INTO sessions(
                id,
                session_key,
                channel,
                target,
                metadata_json,
                created_at,
                updated_at,
                status,
                last_activity_at,
                active_agent_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                session_key,
                channel,
                target,
                session_metadata_json,
                created_at,
                updated_at,
                "active",
                updated_at,
                normalize_nullable_text(active_agent_id),
            ),
        )
