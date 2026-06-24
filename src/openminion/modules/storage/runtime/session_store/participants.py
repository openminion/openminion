from __future__ import annotations

from typing import Callable
from uuid import uuid4

from .backend import RuntimeSessionStoreBackend
from .keys import (
    agent_id_from_session_key,
    is_room_session_key,
    normalize_identity,
    normalize_participant_role,
    normalize_participant_type,
    utc_now_iso,
)
from .models import RoomParticipant, SessionRecord
from .rows import PARTICIPANT_COLUMNS, row_to_participant


class RuntimeSessionStoreParticipants:
    def __init__(
        self,
        backend: RuntimeSessionStoreBackend,
        *,
        get_session: Callable[[str], SessionRecord | None],
    ) -> None:
        self._backend = backend
        self._get_session = get_session

    def add_participant(
        self,
        *,
        session_id: str,
        participant_type: str,
        participant_id: str,
        channel: str = "",
        role: str = "participant",
        display_name: str = "",
    ) -> RoomParticipant:
        session = self._get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        normalized_type = normalize_participant_type(participant_type)
        normalized_id = normalize_identity(participant_id)
        if not normalized_id:
            raise ValueError("participant_id is required")
        normalized_channel = normalize_identity(channel) if channel else ""
        normalized_role = normalize_participant_role(role)
        if not is_room_session_key(session.session_key):
            legacy_agent = agent_id_from_session_key(session.session_key)
            if legacy_agent and not (
                normalized_type == "agent" and normalized_id == legacy_agent
            ):
                existing_row = self._backend.query_one(
                    """
                    SELECT id
                    FROM room_participants
                    WHERE session_id = ?
                    LIMIT 1
                    """,
                    (session_id,),
                )
                if existing_row is None:
                    self.upsert_participant_record(
                        session_id=session_id,
                        participant_type="agent",
                        participant_id=legacy_agent,
                        channel=session.channel,
                        role="owner",
                        display_name=legacy_agent,
                    )

        self.upsert_participant_record(
            session_id=session_id,
            participant_type=normalized_type,
            participant_id=normalized_id,
            channel=normalized_channel,
            role=normalized_role,
            display_name=str(display_name or "").strip(),
        )
        refreshed = self._get_session(session_id)
        if (
            normalized_type == "agent"
            and refreshed is not None
            and not (refreshed.active_agent_id or "").strip()
        ):
            now = utc_now_iso()
            self._backend.execute_count(
                "UPDATE sessions SET active_agent_id = ?, updated_at = ? WHERE id = ?",
                (normalized_id, now, session_id),
            )
        participant = self.get_participant(
            session_id=session_id,
            participant_type=normalized_type,
            participant_id=normalized_id,
        )
        if participant is None:
            raise RuntimeError(
                f"Failed to upsert participant {normalized_type}:{normalized_id}"
            )
        return participant

    def get_participant(
        self,
        session_id: str,
        participant_type: str,
        participant_id: str,
    ) -> RoomParticipant | None:
        session = self._get_session(session_id)
        if session is None:
            return None
        self.ensure_legacy_participant_record(session)
        row = self._backend.query_one(
            f"""
            SELECT {PARTICIPANT_COLUMNS}
            FROM room_participants
            WHERE session_id = ? AND participant_type = ? AND participant_id = ?
              AND left_at IS NULL
            LIMIT 1
            """,
            (
                session_id,
                normalize_participant_type(participant_type),
                normalize_identity(participant_id),
            ),
        )
        if row is None:
            return None
        return row_to_participant(row)

    def list_participants(self, session_id: str) -> list[RoomParticipant]:
        session = self._get_session(session_id)
        if session is None:
            return []
        self.ensure_legacy_participant_record(session)
        rows = self._backend.query_dicts(
            f"""
            SELECT {PARTICIPANT_COLUMNS}
            FROM room_participants
            WHERE session_id = ? AND left_at IS NULL
            ORDER BY joined_at ASC, id ASC
            """,
            (session_id,),
        )
        return [row_to_participant(row) for row in rows]

    def remove_participant(
        self,
        *,
        session_id: str,
        participant_type: str,
        participant_id: str,
    ) -> bool:
        session = self._get_session(session_id)
        if session is None:
            return False
        normalized_type = normalize_participant_type(participant_type)
        normalized_id = normalize_identity(participant_id)
        now = utc_now_iso()
        with self._backend.transaction():
            updated = self._backend.execute_count(
                """
                UPDATE room_participants
                SET left_at = ?
                WHERE session_id = ? AND participant_type = ? AND participant_id = ?
                  AND left_at IS NULL
                """,
                (now, session_id, normalized_type, normalized_id),
            )
            if updated > 0 and normalized_type == "agent":
                current_active = self.get_active_agent(session_id)
                if current_active == normalized_id:
                    remaining_agents = [
                        item.participant_id
                        for item in self.list_participants(session_id)
                        if item.participant_type == "agent"
                        and item.participant_id != normalized_id
                    ]
                    next_active = remaining_agents[0] if remaining_agents else None
                    self._backend.execute_count(
                        "UPDATE sessions SET active_agent_id = ?, updated_at = ? WHERE id = ?",
                        (next_active, now, session_id),
                    )
        return updated > 0

    def get_active_agent(self, session_id: str) -> str | None:
        session = self._get_session(session_id)
        if session is None:
            return None
        self.ensure_legacy_participant_record(session)
        if session.active_agent_id:
            return session.active_agent_id
        if not is_room_session_key(session.session_key):
            legacy_agent = agent_id_from_session_key(session.session_key)
            return legacy_agent or None
        participants = self.list_participants(session_id)
        for participant in participants:
            if participant.participant_type == "agent":
                return participant.participant_id
        return None

    def set_active_agent(self, *, session_id: str, agent_id: str) -> SessionRecord:
        session = self._get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        normalized_agent = normalize_identity(agent_id)
        if not normalized_agent:
            raise ValueError("agent_id is required")
        participant = self.get_participant(
            session_id,
            "agent",
            normalized_agent,
        )
        if participant is None:
            legacy_agent = agent_id_from_session_key(session.session_key)
            if legacy_agent and legacy_agent != normalized_agent:
                raise ValueError(
                    f"Legacy session {session_id!r} is bound to agent {legacy_agent!r}"
                )
            if not legacy_agent:
                raise ValueError(
                    f"Agent {normalized_agent!r} is not an active participant"
                )
        now = utc_now_iso()
        self._backend.execute_count(
            "UPDATE sessions SET active_agent_id = ?, updated_at = ? WHERE id = ?",
            (normalized_agent, now, session_id),
        )
        updated = self._get_session(session_id)
        if updated is None:
            raise RuntimeError(f"Failed to update active agent for {session_id}")
        return updated

    def ensure_legacy_participant_record(self, session: SessionRecord) -> None:
        if is_room_session_key(session.session_key):
            return
        legacy_agent = agent_id_from_session_key(session.session_key)
        if not legacy_agent:
            return
        row = self._backend.query_one(
            """
            SELECT id
            FROM room_participants
            WHERE session_id = ? AND left_at IS NULL
            LIMIT 1
            """,
            (session.id,),
        )
        if row is not None:
            return
        self.upsert_participant_record(
            session_id=session.id,
            participant_type="agent",
            participant_id=legacy_agent,
            channel=session.channel,
            role="owner",
            display_name=legacy_agent,
        )

    def upsert_participant_record(
        self,
        *,
        session_id: str,
        participant_type: str,
        participant_id: str,
        channel: str,
        role: str,
        display_name: str,
    ) -> None:
        now = utc_now_iso()
        participant_row_id = uuid4().hex
        with self._backend.transaction():
            self._backend.execute_count(
                """
                INSERT INTO room_participants(
                    id,
                    session_id,
                    participant_type,
                    participant_id,
                    channel,
                    role,
                    display_name,
                    joined_at,
                    left_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(session_id, participant_type, participant_id)
                DO UPDATE SET
                    channel = excluded.channel,
                    role = excluded.role,
                    display_name = excluded.display_name,
                    joined_at = CASE
                        WHEN room_participants.left_at IS NULL THEN room_participants.joined_at
                        ELSE excluded.joined_at
                    END,
                    left_at = NULL
                """,
                (
                    participant_row_id,
                    session_id,
                    participant_type,
                    participant_id,
                    channel,
                    role,
                    display_name,
                    now,
                ),
            )
