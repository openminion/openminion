from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .backend import RuntimeSessionStoreBackend
from .keys import normalize_session_status, utc_now_iso
from .models import EventRecord, SessionRecord
from .rows import (
    normalize_nullable_text,
    parse_iso_datetime,
    row_to_event,
)

LIFECYCLE_UNSET = object()


class RuntimeSessionStoreLifecycle:
    def __init__(
        self,
        backend: RuntimeSessionStoreBackend,
        *,
        get_session: Callable[[str], SessionRecord | None],
        list_sessions: Callable[..., list[SessionRecord]],
        assert_session_turn_fence: Callable[[str, int], None] | None = None,
    ) -> None:
        self._backend = backend
        self._get_session = get_session
        self._list_sessions = list_sessions
        self._assert_session_turn_fence = assert_session_turn_fence

    def _assert_fence_if_requested(
        self,
        *,
        session_id: str,
        session_turn_fence_token: int | None,
    ) -> None:
        if session_turn_fence_token is None or self._assert_session_turn_fence is None:
            return
        self._assert_session_turn_fence(session_id, int(session_turn_fence_token))

    def append_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        session_turn_fence_token: int | None = None,
    ) -> EventRecord:
        from .rows import metadata_json

        now = utc_now_iso()
        with self._backend.transaction():
            self._assert_fence_if_requested(
                session_id=session_id,
                session_turn_fence_token=session_turn_fence_token,
            )
            self._backend.execute_count(
                """
                INSERT INTO events(session_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, event_type, metadata_json(payload), now),
            )
            self._backend.execute_count(
                "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE id = ?",
                (now, now, session_id),
            )
        row = self._backend.query_one(
            """
            SELECT id, session_id, event_type, payload_json, created_at
            FROM events
            WHERE session_id = ? AND event_type = ? AND created_at = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (session_id, event_type, now),
        )
        if row is None:
            raise RuntimeError("Failed to read event after insert")
        return row_to_event(row)

    def list_events(
        self,
        *,
        session_id: str,
        limit: int = 100,
        newest_first: bool = False,
        event_type_prefix: str | None = None,
    ) -> list[EventRecord]:
        from .rows import normalize_optional_text

        safe_limit = max(0, int(limit))
        if safe_limit == 0:
            return []
        direction = "DESC" if newest_first else "ASC"
        params: list[object] = [session_id]
        query = """
            SELECT id, session_id, event_type, payload_json, created_at
            FROM events
            WHERE session_id = ?
        """
        prefix = normalize_optional_text(event_type_prefix)
        if prefix:
            query += "\nAND event_type LIKE ?"
            params.append(f"{prefix}%")
        query += f"\nORDER BY created_at {direction}, id {direction}\nLIMIT ?"
        params.append(safe_limit)
        rows = self._backend.query_dicts(query, params)
        return [row_to_event(row) for row in rows]

    def touch_session_activity(
        self,
        *,
        session_id: str,
        last_activity_at: str | None = None,
    ) -> SessionRecord:
        timestamp = str(last_activity_at or utc_now_iso()).strip() or utc_now_iso()
        updated = self._backend.execute_count(
            "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE id = ?",
            (timestamp, timestamp, session_id),
        )
        if updated == 0:
            raise ValueError(f"Session not found: {session_id}")
        session = self._get_session(session_id)
        if session is None:
            raise RuntimeError(
                f"Failed to read session after activity update: {session_id}"
            )
        return session

    def update_session_lifecycle(
        self,
        *,
        session_id: str,
        status: str | None = None,
        last_activity_at: str | None = None,
        closed_at: str | None | object = LIFECYCLE_UNSET,
        expires_at: str | None | object = LIFECYCLE_UNSET,
    ) -> SessionRecord:
        current = self._get_session(session_id)
        if current is None:
            raise ValueError(f"Session not found: {session_id}")

        next_status = str(status or current.status).strip() or current.status
        next_last_activity = (
            str(
                last_activity_at
                if last_activity_at is not None
                else current.last_activity_at
            ).strip()
            or current.updated_at
        )
        next_closed_at = (
            current.closed_at
            if closed_at is LIFECYCLE_UNSET
            else normalize_nullable_text(closed_at)
        )
        next_expires_at = (
            current.expires_at
            if expires_at is LIFECYCLE_UNSET
            else normalize_nullable_text(expires_at)
        )
        now = utc_now_iso()
        self._backend.execute_count(
            """
            UPDATE sessions
            SET status = ?, last_activity_at = ?, closed_at = ?, expires_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_status,
                next_last_activity,
                next_closed_at,
                next_expires_at,
                now,
                session_id,
            ),
        )
        updated = self._get_session(session_id)
        if updated is None:
            raise RuntimeError(
                f"Failed to update lifecycle for session_id={session_id}"
            )
        return updated

    def set_session_status(
        self,
        *,
        session_id: str,
        status: str,
        reason: str | None = None,
    ) -> SessionRecord:
        current = self._get_session(session_id)
        if current is None:
            raise ValueError(f"Session not found: {session_id}")
        next_status = normalize_session_status(status)
        now = utc_now_iso()
        next_closed_at = current.closed_at
        if next_status == "closed":
            next_closed_at = current.closed_at or now
        elif current.closed_at is not None:
            next_closed_at = None

        updated = self.update_session_lifecycle(
            session_id=session_id,
            status=next_status,
            last_activity_at=now,
            closed_at=next_closed_at,
        )
        if current.status != updated.status:
            self.append_event(
                session_id=session_id,
                event_type="session.status.changed",
                payload={
                    "previous_status": current.status,
                    "status": updated.status,
                    "reason": str(reason or "").strip(),
                },
            )
        if current.status != "closed" and updated.status == "closed":
            self.append_event(
                session_id=session_id,
                event_type="session.closed",
                payload={
                    "closed_at": updated.closed_at or now,
                    "reason": str(reason or "").strip(),
                },
            )
        return updated

    def close_session(
        self,
        *,
        session_id: str,
        reason: str | None = None,
    ) -> SessionRecord:
        return self.set_session_status(
            session_id=session_id,
            status="closed",
            reason=reason or "manual_close",
        )

    def mark_stale_sessions(self, timeout_seconds: int = 24 * 60 * 60) -> int:
        stale_after = max(1, int(timeout_seconds))
        now = datetime.now(timezone.utc)
        candidates = self._list_sessions(
            limit=10_000, newest_first=False, status="active"
        )
        stale_ids: list[str] = []
        stale_timestamps: dict[str, str] = {}
        for session in candidates:
            last_seen = parse_iso_datetime(
                session.last_activity_at or session.updated_at
            )
            if last_seen is None:
                continue
            if (now - last_seen).total_seconds() <= stale_after:
                continue
            stale_ids.append(session.id)
            stale_timestamps[session.id] = last_seen.isoformat()
        for session_id in stale_ids:
            self.set_session_status(
                session_id=session_id,
                status="stale",
                reason="stale_timeout",
            )
            self.append_event(
                session_id=session_id,
                event_type="session.stale",
                payload={
                    "reason": "stale_timeout",
                    "last_activity_at": stale_timestamps.get(session_id, ""),
                    "timeout_seconds": stale_after,
                },
            )
        return len(stale_ids)

    def expire_session(
        self,
        *,
        session_id: str,
        expires_at: str | None = None,
        reason: str | None = None,
    ) -> SessionRecord:
        current = self._get_session(session_id)
        if current is None:
            raise ValueError(f"Session not found: {session_id}")
        now = utc_now_iso()
        expiration = str(expires_at or now).strip() or now
        updated = self.update_session_lifecycle(
            session_id=session_id,
            status="closed",
            last_activity_at=now,
            closed_at=current.closed_at or now,
            expires_at=expiration,
        )
        if current.status != updated.status:
            self.append_event(
                session_id=session_id,
                event_type="session.status.changed",
                payload={
                    "previous_status": current.status,
                    "status": updated.status,
                    "reason": "expired",
                },
            )
        self.append_event(
            session_id=session_id,
            event_type="session.expired",
            payload={
                "previous_status": current.status,
                "status": updated.status,
                "expires_at": expiration,
                "closed_at": updated.closed_at or now,
                "reason": str(reason or "ttl_expired").strip(),
            },
        )
        return updated
