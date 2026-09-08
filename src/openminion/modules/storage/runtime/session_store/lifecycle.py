from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from collections.abc import Callable, Mapping

from openminion.modules.storage.constants import SESSION_RETENTION_HOLD_VERSION

from .backend import RuntimeSessionStoreBackend
from .constants import EVENT_PAGE_MAX
from .keys import normalize_session_status, utc_now_iso
from .models import (
    CaptureEventCommitRecord,
    EventRecord,
    MemoryCaptureRetentionHoldRecord,
    RuntimeSessionStoreIntegrityError,
    SessionRecord,
)
from .rows import (
    EVENT_COLUMNS,
    RETENTION_HOLD_COLUMNS,
    metadata_json,
    normalize_nullable_text,
    parse_iso_datetime,
    row_to_event,
    row_to_retention_hold,
)

LIFECYCLE_UNSET = object()
MEMORY_CAPTURE_HOLD_ACTOR = "memory_capture"
MEMORY_CAPTURE_HOLD_SCHEMA_VERSION = SESSION_RETENTION_HOLD_VERSION


def _memory_capture_hold_identity(
    *, session_id: str, capture_id: str
) -> tuple[str, str]:
    reason = f"memory_capture:{capture_id}"
    material = f"{session_id}:{reason}:{MEMORY_CAPTURE_HOLD_ACTOR}"
    digest = hashlib.sha256(material.encode()).hexdigest()[:16]
    return f"hold-{digest}", reason


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
        self._assert_session_turn_fence(session_id, session_turn_fence_token)

    def append_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        canonical_event_id: str | None = None,
        session_turn_fence_token: int | None = None,
    ) -> EventRecord:
        with self._backend.transaction():
            self._assert_fence_if_requested(
                session_id=session_id,
                session_turn_fence_token=session_turn_fence_token,
            )
            canonical_id = (canonical_event_id or "").strip()
            if canonical_id:
                event, _replayed = self._commit_canonical_event_locked(
                    session_id=session_id,
                    canonical_event_id=canonical_id,
                    event_type=event_type,
                    payload=payload,
                )
                return event
            return self._insert_event_locked(
                session_id=session_id,
                event_type=event_type,
                payload=payload,
            )

    def get_event_by_canonical_id(self, canonical_event_id: str) -> EventRecord | None:
        canonical_id = canonical_event_id.strip()
        if not canonical_id:
            raise ValueError("canonical_event_id is required")
        row = self._backend.query_one(
            f"SELECT {EVENT_COLUMNS} FROM events WHERE canonical_event_id = ?",
            (canonical_id,),
        )
        return None if row is None else row_to_event(row)

    def commit_terminal_turn_outcome(
        self,
        *,
        session_id: str,
        canonical_event_id: str,
        capture_id: str,
        payload: Mapping[str, Any],
        event_type: str = "turn.outcome",
        capture_state: str = "pending",
        session_turn_fence_token: int | None = None,
    ) -> CaptureEventCommitRecord:
        capture = capture_id.strip()
        if not capture:
            raise ValueError("capture_id is required")
        self._assert_capture_id_matches_payload(
            capture_id=capture,
            payload=payload,
            canonical_event_id=canonical_event_id,
        )
        with self._backend.transaction():
            self._assert_fence_if_requested(
                session_id=session_id,
                session_turn_fence_token=session_turn_fence_token,
            )
            event, replayed = self._commit_canonical_event_locked(
                session_id=session_id,
                canonical_event_id=canonical_event_id,
                event_type=event_type,
                payload=payload,
            )
            hold = None
            if capture_state.strip() == "pending":
                hold = self._ensure_capture_hold_locked(
                    session_id=session_id,
                    capture_id=capture,
                    canonical_event_id=canonical_event_id,
                )
            return CaptureEventCommitRecord(
                event=event,
                retention_hold=hold,
                replayed=replayed,
            )

    def commit_capture_result_and_release_hold(
        self,
        *,
        session_id: str,
        canonical_event_id: str,
        capture_id: str,
        payload: Mapping[str, Any],
        event_type: str = "memory.capture.result",
        session_turn_fence_token: int | None = None,
    ) -> CaptureEventCommitRecord:
        capture = capture_id.strip()
        if not capture:
            raise ValueError("capture_id is required")
        self._assert_capture_id_matches_payload(
            capture_id=capture,
            payload=payload,
            canonical_event_id=canonical_event_id,
        )
        with self._backend.transaction():
            self._assert_fence_if_requested(
                session_id=session_id,
                session_turn_fence_token=session_turn_fence_token,
            )
            event, replayed = self._commit_canonical_event_locked(
                session_id=session_id,
                canonical_event_id=canonical_event_id,
                event_type=event_type,
                payload=payload,
            )
            hold_id, reason = _memory_capture_hold_identity(
                session_id=session_id,
                capture_id=capture,
            )
            hold = self._get_retention_hold(hold_id)
            if hold is None:
                raise RuntimeSessionStoreIntegrityError(
                    f"capture {capture!r} has no retention hold",
                    canonical_event_id=canonical_event_id,
                )
            self._assert_capture_hold_matches(
                hold,
                session_id=session_id,
                reason=reason,
                canonical_event_id=canonical_event_id,
            )
            if hold.released_at is not None and not replayed:
                raise RuntimeSessionStoreIntegrityError(
                    f"capture {capture!r} already has a terminal result",
                    canonical_event_id=canonical_event_id,
                )
            if hold.released_at is None:
                self._backend.execute_count(
                    "UPDATE session_retention_holds SET released_at = ? WHERE hold_id = ?",
                    (utc_now_iso(), hold_id),
                )
                hold = self._get_retention_hold(hold_id)
                if hold is None:
                    raise RuntimeError(f"Failed to read released hold: {hold_id}")
            return CaptureEventCommitRecord(
                event=event,
                retention_hold=hold,
                replayed=replayed,
            )

    def _commit_canonical_event_locked(
        self,
        *,
        session_id: str,
        canonical_event_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None,
    ) -> tuple[EventRecord, bool]:
        canonical_id = canonical_event_id.strip()
        if not canonical_id:
            raise ValueError("canonical_event_id is required")
        payload_json = metadata_json(payload)
        now = utc_now_iso()
        inserted = self._backend.execute_count(
            """
            INSERT INTO events(
              session_id, event_type, payload_json, created_at, canonical_event_id
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(canonical_event_id) DO NOTHING
            """,
            (session_id, event_type, payload_json, now, canonical_id),
        )
        row = self._backend.query_one(
            f"SELECT {EVENT_COLUMNS} FROM events WHERE canonical_event_id = ?",
            (canonical_id,),
        )
        if row is None:
            raise RuntimeError(f"Failed to read canonical event: {canonical_id}")
        if (
            str(row["session_id"]) != session_id
            or str(row["event_type"]) != event_type
            or str(row["payload_json"]) != payload_json
        ):
            raise RuntimeSessionStoreIntegrityError(
                f"canonical event {canonical_id!r} conflicts with stored event",
                canonical_event_id=canonical_id,
            )
        if inserted > 0:
            self._backend.execute_count(
                "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE id = ?",
                (now, now, session_id),
            )
        return row_to_event(row), inserted == 0

    def _insert_event_locked(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None,
    ) -> EventRecord:
        now = utc_now_iso()
        event_id = self._backend.insert(
            "events",
            {
                "session_id": session_id,
                "event_type": event_type,
                "payload_json": metadata_json(payload),
                "created_at": now,
                "canonical_event_id": None,
            },
        )
        row = self._backend.query_one(
            f"SELECT {EVENT_COLUMNS} FROM events WHERE id = ?",
            (event_id,),
        )
        if row is None:
            raise RuntimeError(f"Failed to read inserted event: {event_id}")
        self._backend.execute_count(
            "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE id = ?",
            (now, now, session_id),
        )
        return row_to_event(row)

    def _ensure_capture_hold_locked(
        self,
        *,
        session_id: str,
        capture_id: str,
        canonical_event_id: str,
    ) -> MemoryCaptureRetentionHoldRecord:
        hold_id, reason = _memory_capture_hold_identity(
            session_id=session_id,
            capture_id=capture_id,
        )
        hold = self._get_retention_hold(hold_id)
        if hold is not None:
            self._assert_capture_hold_matches(
                hold,
                session_id=session_id,
                reason=reason,
                canonical_event_id=canonical_event_id,
            )
            return hold
        self._backend.execute_count(
            """
            INSERT INTO session_retention_holds(
              hold_id, session_id, reason, actor_id,
              created_at, released_at, schema_version
            )
            VALUES (?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                hold_id,
                session_id,
                reason,
                MEMORY_CAPTURE_HOLD_ACTOR,
                utc_now_iso(),
                MEMORY_CAPTURE_HOLD_SCHEMA_VERSION,
            ),
        )
        hold = self._get_retention_hold(hold_id)
        if hold is None:
            raise RuntimeError(f"Failed to read inserted hold: {hold_id}")
        return hold

    def _get_retention_hold(
        self, hold_id: str
    ) -> MemoryCaptureRetentionHoldRecord | None:
        row = self._backend.query_one(
            f"SELECT {RETENTION_HOLD_COLUMNS} FROM session_retention_holds WHERE hold_id = ?",
            (hold_id,),
        )
        return None if row is None else row_to_retention_hold(row)

    @staticmethod
    def _assert_capture_id_matches_payload(
        *,
        capture_id: str,
        payload: Mapping[str, Any],
        canonical_event_id: str,
    ) -> None:
        if str(payload.get("capture_id") or "").strip() != capture_id:
            raise RuntimeSessionStoreIntegrityError(
                "capture_id does not match the canonical event payload",
                canonical_event_id=canonical_event_id,
            )

    @staticmethod
    def _assert_capture_hold_matches(
        hold: MemoryCaptureRetentionHoldRecord,
        *,
        session_id: str,
        reason: str,
        canonical_event_id: str,
    ) -> None:
        if (
            hold.session_id != session_id
            or hold.reason != reason
            or hold.actor_id != MEMORY_CAPTURE_HOLD_ACTOR
        ):
            raise RuntimeSessionStoreIntegrityError(
                f"retention hold {hold.hold_id!r} conflicts with capture",
                canonical_event_id=canonical_event_id,
            )

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
        query = f"""
            SELECT {EVENT_COLUMNS}
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

    def count_events(
        self,
        *,
        session_id: str,
        event_type_prefix: str | None = None,
    ) -> int:
        from .rows import normalize_optional_text

        params: list[object] = [session_id]
        query = "SELECT COUNT(*) AS count FROM events WHERE session_id = ?"
        prefix = normalize_optional_text(event_type_prefix)
        if prefix:
            query += " AND event_type LIKE ?"
            params.append(f"{prefix}%")
        row = self._backend.query_one(query, params)
        return 0 if row is None else int(row["count"])

    def list_events_before_id(
        self,
        *,
        session_id: str,
        before_id: int,
        limit: int = 100,
    ) -> list[EventRecord]:
        safe_limit = max(0, min(int(limit), EVENT_PAGE_MAX))
        if safe_limit == 0:
            return []
        rows = self._backend.query_dicts(
            f"""
            SELECT {EVENT_COLUMNS}
            FROM events
            WHERE session_id = ? AND id < ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, max(1, int(before_id)), safe_limit),
        )
        return [row_to_event(row) for row in rows]

    def event_high_water(self, *, session_id: str) -> int:
        row = self._backend.query_one(
            "SELECT COALESCE(MAX(id), 0) AS high_water FROM events WHERE session_id = ?",
            (session_id,),
        )
        return 0 if row is None else int(row["high_water"])

    def list_events_after_id(
        self,
        *,
        session_id: str,
        after_id: int,
        high_water_id: int,
        limit: int = EVENT_PAGE_MAX,
    ) -> list[EventRecord]:
        safe_limit = max(0, min(int(limit), EVENT_PAGE_MAX))
        if safe_limit == 0:
            return []
        rows = self._backend.query_dicts(
            f"""
            SELECT {EVENT_COLUMNS}
            FROM events
            WHERE session_id = ? AND id > ? AND id <= ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (session_id, max(0, int(after_id)), max(0, int(high_water_id)), safe_limit),
        )
        return [row_to_event(row) for row in rows]

    def touch_session_activity(
        self,
        *,
        session_id: str,
        last_activity_at: str | None = None,
    ) -> SessionRecord:
        timestamp = (last_activity_at or utc_now_iso()).strip() or utc_now_iso()
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
        session_turn_fence_token: int | None = None,
    ) -> SessionRecord:
        with self._backend.transaction():
            self._assert_fence_if_requested(
                session_id=session_id,
                session_turn_fence_token=session_turn_fence_token,
            )
            current = self._get_session(session_id)
            if current is None:
                raise ValueError(f"Session not found: {session_id}")

            next_status = (status or current.status).strip() or current.status
            next_last_activity = (
                last_activity_at
                if last_activity_at is not None
                else current.last_activity_at
            ).strip() or current.updated_at
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
        session_turn_fence_token: int | None = None,
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
            session_turn_fence_token=session_turn_fence_token,
        )
        if current.status != updated.status:
            self.append_event(
                session_id=session_id,
                event_type="session.status.changed",
                payload={
                    "previous_status": current.status,
                    "status": updated.status,
                    "reason": (reason or "").strip(),
                },
                session_turn_fence_token=session_turn_fence_token,
            )
        if current.status != "closed" and updated.status == "closed":
            self.append_event(
                session_id=session_id,
                event_type="session.closed",
                payload={
                    "closed_at": updated.closed_at or now,
                    "reason": (reason or "").strip(),
                },
                session_turn_fence_token=session_turn_fence_token,
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

    def resume_session(
        self,
        *,
        session_id: str,
        reason: str | None = None,
        session_turn_fence_token: int | None = None,
    ) -> SessionRecord:
        current = self._get_session(session_id)
        if current is None:
            raise ValueError(f"Session not found: {session_id}")
        if current.expires_at:
            raise ValueError(f"Session has expired and cannot be resumed: {session_id}")
        if current.status == "active":
            return current

        updated = self.set_session_status(
            session_id=session_id,
            status="active",
            reason=reason or "explicit_resume",
            session_turn_fence_token=session_turn_fence_token,
        )
        self.append_event(
            session_id=session_id,
            event_type="session.resumed",
            payload={
                "previous_status": current.status,
                "reason": (reason or "explicit_resume").strip(),
            },
            session_turn_fence_token=session_turn_fence_token,
        )
        return updated

    def mark_stale_sessions(self, timeout_seconds: int = 24 * 60 * 60) -> int:
        stale_after = max(1, int(timeout_seconds))
        now = datetime.now(timezone.utc)
        candidates = self._list_sessions(
            limit=10_000, newest_first=False, status="active"
        )
        stale_sessions: list[tuple[str, str]] = []
        for session in candidates:
            last_seen = parse_iso_datetime(
                session.last_activity_at or session.updated_at
            )
            if last_seen is None:
                continue
            if (now - last_seen).total_seconds() <= stale_after:
                continue
            stale_sessions.append((session.id, last_seen.isoformat()))
        for session_id, last_activity_at in stale_sessions:
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
                    "last_activity_at": last_activity_at,
                    "timeout_seconds": stale_after,
                },
            )
        return len(stale_sessions)

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
        expiration = (expires_at or now).strip() or now
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
                "reason": (reason or "ttl_expired").strip(),
            },
        )
        return updated
