from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from openminion.modules.storage.record_store import RecordStore

from ..pinned_context import (
    DEFAULT_PINNED_CONTEXT_POLICY,
    PinnedContextEntry,
    PinnedContextPolicy,
)
from .backend import RuntimeSessionStoreBackend
from .context import RuntimeSessionStoreContext
from .keys import (
    agent_id_from_session_key,
    build_explicit_session_key,
    build_room_session_key,
    build_session_key,
    is_room_session_key,
)
from .lifecycle import LIFECYCLE_UNSET, RuntimeSessionStoreLifecycle
from .messages import RuntimeSessionStoreMessages
from .models import (
    EventRecord,
    MessageRecord,
    RoomParticipant,
    SessionContextRecord,
    SessionRecord,
)
from .participants import RuntimeSessionStoreParticipants
from .sessions import RuntimeSessionStoreSessions
from .turn_leases import RuntimeSessionTurnLeases


class SessionStore:
    def __init__(self, store_or_connection: RecordStore | sqlite3.Connection) -> None:
        self._backend = RuntimeSessionStoreBackend(store_or_connection)
        self._turn_leases = RuntimeSessionTurnLeases(self._backend)
        self._sessions = RuntimeSessionStoreSessions(
            self._backend,
            list_participants=lambda session_id: self._participants.list_participants(
                session_id
            ),
        )
        self._messages = RuntimeSessionStoreMessages(
            self._backend,
            assert_session_turn_fence=self._assert_session_turn_fence_for_child,
        )
        self._participants = RuntimeSessionStoreParticipants(
            self._backend,
            get_session=self._sessions.get_session,
        )
        self._lifecycle = RuntimeSessionStoreLifecycle(
            self._backend,
            get_session=self._sessions.get_session,
            list_sessions=self._sessions.list_sessions,
            assert_session_turn_fence=self._assert_session_turn_fence_for_child,
        )
        self._context = RuntimeSessionStoreContext(
            self._backend,
            assert_session_turn_fence=self._assert_session_turn_fence_for_child,
        )

    def _assert_session_turn_fence_for_child(
        self,
        session_id: str,
        fence_token: int,
    ) -> None:
        self._turn_leases.assert_fence(session_id, fence_token=fence_token)

    def resolve_session(
        self,
        *,
        agent_id: str,
        channel: str,
        target: str,
        session_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SessionRecord:
        return self._sessions.resolve_session(
            agent_id=agent_id,
            channel=channel,
            target=target,
            session_id=session_id,
            metadata=metadata,
        )

    def create_room(
        self,
        *,
        channel: str,
        target: str,
        session_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        active_agent_id: str | None = None,
    ) -> SessionRecord:
        return self._sessions.create_room(
            channel=channel,
            target=target,
            session_id=session_id,
            metadata=metadata,
            active_agent_id=active_agent_id,
        )

    def get_session(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get_session(session_id)

    def count_sessions(self) -> int:
        return self._sessions.count_sessions()

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
        return self._sessions.list_sessions(
            limit=limit,
            newest_first=newest_first,
            agent_id=agent_id,
            status=status,
            channel=channel,
            target=target,
            metadata_filter=metadata_filter,
        )

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
        return self._participants.add_participant(
            session_id=session_id,
            participant_type=participant_type,
            participant_id=participant_id,
            channel=channel,
            role=role,
            display_name=display_name,
        )

    def get_participant(
        self,
        session_id: str,
        participant_type: str,
        participant_id: str,
    ) -> RoomParticipant | None:
        return self._participants.get_participant(
            session_id,
            participant_type,
            participant_id,
        )

    def list_participants(self, session_id: str) -> list[RoomParticipant]:
        return self._participants.list_participants(session_id)

    def remove_participant(
        self,
        *,
        session_id: str,
        participant_type: str,
        participant_id: str,
    ) -> bool:
        return self._participants.remove_participant(
            session_id=session_id,
            participant_type=participant_type,
            participant_id=participant_id,
        )

    def get_active_agent(self, session_id: str) -> str | None:
        return self._participants.get_active_agent(session_id)

    def set_active_agent(self, *, session_id: str, agent_id: str) -> SessionRecord:
        return self._participants.set_active_agent(
            session_id=session_id,
            agent_id=agent_id,
        )

    def delete_session(self, session_id: str) -> bool:
        return self._sessions.delete_session(session_id)

    def acquire_session_turn_lease(
        self,
        session_id: str,
        *,
        owner: str,
        request_id: str,
        ttl_s: int = 60,
        now_iso: str | None = None,
    ) -> Any:
        return self._turn_leases.acquire(
            session_id,
            owner=owner,
            request_id=request_id,
            ttl_s=ttl_s,
            now_iso=now_iso,
        )

    def renew_session_turn_lease(
        self,
        session_id: str,
        *,
        owner: str,
        fence_token: int,
        ttl_s: int = 60,
        now_iso: str | None = None,
    ) -> bool:
        return self._turn_leases.renew(
            session_id,
            owner=owner,
            fence_token=fence_token,
            ttl_s=ttl_s,
            now_iso=now_iso,
        )

    def release_session_turn_lease(
        self,
        session_id: str,
        *,
        owner: str,
        fence_token: int,
        now_iso: str | None = None,
    ) -> bool:
        return self._turn_leases.release(
            session_id,
            owner=owner,
            fence_token=fence_token,
            now_iso=now_iso,
        )

    def assert_session_turn_fence(
        self,
        session_id: str,
        *,
        fence_token: int,
    ) -> None:
        self._turn_leases.assert_fence(session_id, fence_token=fence_token)

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
        session_turn_fence_token: int | None = None,
    ) -> MessageRecord:
        return self._messages.append_message(
            session_id=session_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            attach_id=attach_id,
            role=role,
            body=body,
            metadata=metadata,
            participant_id=participant_id,
            participant_type=participant_type,
            display_name=display_name,
            session_turn_fence_token=session_turn_fence_token,
        )

    def list_messages(
        self,
        *,
        session_id: str,
        limit: int = 100,
        conversation_id: str | None = None,
        thread_id: str | None = None,
    ) -> list[MessageRecord]:
        return self._messages.list_messages(
            session_id=session_id,
            limit=limit,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )

    def list_recent_messages(
        self,
        *,
        session_id: str,
        limit: int = 100,
        conversation_id: str | None = None,
        thread_id: str | None = None,
    ) -> list[MessageRecord]:
        return self._messages.list_recent_messages(
            session_id=session_id,
            limit=limit,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )

    def latest_conversation_id(self, *, session_id: str) -> str | None:
        return self._messages.latest_conversation_id(session_id=session_id)

    def count_messages(
        self,
        *,
        session_id: str,
        conversation_id: str | None = None,
        thread_id: str | None = None,
    ) -> int:
        return self._messages.count_messages(
            session_id=session_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )

    def list_messages_after_rowid(
        self,
        *,
        session_id: str,
        after_rowid: int = 0,
        limit: int = 100,
        conversation_id: str | None = None,
        thread_id: str | None = None,
    ) -> list[MessageRecord]:
        return self._messages.list_messages_after_rowid(
            session_id=session_id,
            after_rowid=after_rowid,
            limit=limit,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )

    def append_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        session_turn_fence_token: int | None = None,
    ) -> EventRecord:
        return self._lifecycle.append_event(
            session_id=session_id,
            event_type=event_type,
            payload=payload,
            session_turn_fence_token=session_turn_fence_token,
        )

    def list_events(
        self,
        *,
        session_id: str,
        limit: int = 100,
        newest_first: bool = False,
        event_type_prefix: str | None = None,
    ) -> list[EventRecord]:
        return self._lifecycle.list_events(
            session_id=session_id,
            limit=limit,
            newest_first=newest_first,
            event_type_prefix=event_type_prefix,
        )

    def touch_session_activity(
        self,
        *,
        session_id: str,
        last_activity_at: str | None = None,
    ) -> SessionRecord:
        return self._lifecycle.touch_session_activity(
            session_id=session_id,
            last_activity_at=last_activity_at,
        )

    def update_session_lifecycle(
        self,
        *,
        session_id: str,
        status: str | None = None,
        last_activity_at: str | None = None,
        closed_at: str | None | object = LIFECYCLE_UNSET,
        expires_at: str | None | object = LIFECYCLE_UNSET,
    ) -> SessionRecord:
        return self._lifecycle.update_session_lifecycle(
            session_id=session_id,
            status=status,
            last_activity_at=last_activity_at,
            closed_at=closed_at,
            expires_at=expires_at,
        )

    def set_session_status(
        self,
        *,
        session_id: str,
        status: str,
        reason: str | None = None,
    ) -> SessionRecord:
        return self._lifecycle.set_session_status(
            session_id=session_id,
            status=status,
            reason=reason,
        )

    def update_session_metadata(
        self,
        *,
        session_id: str,
        patch: Mapping[str, Any],
    ) -> SessionRecord:
        return self._sessions.update_session_metadata(
            session_id=session_id,
            patch=patch,
        )

    def close_session(
        self,
        *,
        session_id: str,
        reason: str | None = None,
    ) -> SessionRecord:
        return self._lifecycle.close_session(
            session_id=session_id,
            reason=reason,
        )

    def mark_stale_sessions(self, timeout_seconds: int = 24 * 60 * 60) -> int:
        return self._lifecycle.mark_stale_sessions(timeout_seconds=timeout_seconds)

    def expire_session(
        self,
        *,
        session_id: str,
        expires_at: str | None = None,
        reason: str | None = None,
    ) -> SessionRecord:
        return self._lifecycle.expire_session(
            session_id=session_id,
            expires_at=expires_at,
            reason=reason,
        )

    def get_session_context(self, *, session_id: str) -> SessionContextRecord | None:
        return self._context.get_session_context(session_id=session_id)

    def list_pins(self, *, session_id: str) -> list[PinnedContextEntry]:
        return self._context.list_pins(session_id=session_id)

    def replace_pins(
        self,
        *,
        session_id: str,
        pins: list[PinnedContextEntry],
        policy: PinnedContextPolicy = DEFAULT_PINNED_CONTEXT_POLICY,
    ) -> SessionContextRecord:
        return self._context.replace_pins(
            session_id=session_id,
            pins=pins,
            policy=policy,
        )

    def add_pin(
        self,
        *,
        session_id: str,
        source: str,
        text: str,
        pin_id: str | None = None,
        created_at: str | None = None,
        policy: PinnedContextPolicy = DEFAULT_PINNED_CONTEXT_POLICY,
    ) -> list[PinnedContextEntry]:
        return self._context.add_pin(
            session_id=session_id,
            source=source,
            text=text,
            pin_id=pin_id,
            created_at=created_at,
            policy=policy,
        )

    def remove_pin(
        self,
        *,
        session_id: str,
        pin_id: str | None = None,
        text: str | None = None,
        source: str | None = None,
        policy: PinnedContextPolicy = DEFAULT_PINNED_CONTEXT_POLICY,
    ) -> list[PinnedContextEntry]:
        return self._context.remove_pin(
            session_id=session_id,
            pin_id=pin_id,
            text=text,
            source=source,
            policy=policy,
        )

    def ensure_session_context(self, *, session_id: str) -> SessionContextRecord:
        return self._context.ensure_session_context(session_id=session_id)

    def update_session_context(
        self,
        *,
        session_id: str,
        pinned_context: str | None = None,
        summary_short: str | None = None,
        rolling_summary: str | None = None,
        compacted_until_rowid: int | None = None,
        compacted_until_created_at: str | None = None,
        compacted_until_message_id: str | None = None,
        compacted_message_count: int | None = None,
        version: int | None = None,
        expected_version: int | None = None,
        session_turn_fence_token: int | None = None,
    ) -> SessionContextRecord:
        return self._context.update_session_context(
            session_id=session_id,
            pinned_context=pinned_context,
            summary_short=summary_short,
            rolling_summary=rolling_summary,
            compacted_until_rowid=compacted_until_rowid,
            compacted_until_created_at=compacted_until_created_at,
            compacted_until_message_id=compacted_until_message_id,
            compacted_message_count=compacted_message_count,
            version=version,
            expected_version=expected_version,
            session_turn_fence_token=session_turn_fence_token,
        )


__all__ = [
    "EventRecord",
    "MessageRecord",
    "RoomParticipant",
    "SessionContextRecord",
    "SessionRecord",
    "SessionStore",
    "agent_id_from_session_key",
    "build_explicit_session_key",
    "build_room_session_key",
    "build_session_key",
    "is_room_session_key",
]
