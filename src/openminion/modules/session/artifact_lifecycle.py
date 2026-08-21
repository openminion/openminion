from __future__ import annotations

from typing import Any
from uuid import uuid4

from openminion.base.time import utc_now_iso
from openminion.modules.telemetry.events.catalog import (
    DESKTOP_ARTIFACT_DETACHED,
    DESKTOP_ARTIFACT_RESTORED,
)
from openminion.modules.session.storage.turn_leases import SessionTurnBusyError


class ArtifactLifecycleError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def get_detached_artifact_refs(
    store: Any,
    session_id: str,
    *,
    limit: int = 256,
) -> list[str]:
    safe_limit = max(1, min(int(limit), 256))
    rows = store._record_store.query_dicts(
        """
        SELECT artifact_ref
        FROM session_detached_artifacts
        WHERE session_id = ?
        ORDER BY event_seq ASC
        LIMIT ?
        """,
        (session_id, safe_limit + 1),
    )
    if len(rows) > safe_limit:
        raise ArtifactLifecycleError(
            "artifact_state_too_large",
            "Artifact state exceeds the supported session limit.",
        )
    return [str(row["artifact_ref"]) for row in rows]


def apply_artifact_decision(
    store: Any,
    session_id: str,
    *,
    artifact_ref: str,
    detached: bool,
    reason_code: str,
    request_id: str,
) -> str:
    try:
        lease = store.acquire_session_turn_lease(
            session_id,
            owner="desktop-artifact-decision",
            request_id=request_id,
            ttl_s=60,
        )
    except SessionTurnBusyError as exc:
        raise ArtifactLifecycleError(
            "session_turn_active", "Session turn is active."
        ) from exc
    try:
        fence_token = int(lease.fence_token)
        store.assert_session_turn_fence(session_id, fence_token=fence_token)
        with store._lock, store._record_store.transaction():
            existing = store._record_store.query_dicts(
                """
                SELECT artifact_ref FROM session_detached_artifacts
                WHERE session_id = ? AND artifact_ref = ?
                """,
                (session_id, artifact_ref),
            )
            is_detached = bool(existing)
            if is_detached == detached:
                return "already_applied"
            if detached:
                _require_projection_capacity(store, session_id)
            now = utc_now_iso()
            event_id = uuid4().hex
            event_type = (
                DESKTOP_ARTIFACT_DETACHED if detached else DESKTOP_ARTIFACT_RESTORED
            )
            store._event_store.insert_session_event_tx(
                session_id=session_id,
                event_type=event_type,
                actor_type="user",
                actor_id=None,
                trace_id=request_id,
                span_id=None,
                task_id=None,
                parent_event_id=None,
                payload={"schema_version": 1, "reason_code": reason_code},
                refs={"artifact_refs": [artifact_ref]},
                importance=1,
                redaction="bounded",
                event_id=event_id,
                timestamp=now,
            )
            seq_rows = store._record_store.query_dicts(
                "SELECT seq FROM session_events WHERE event_id = ?",
                (event_id,),
            )
            event_seq = int(seq_rows[0]["seq"])
            if detached:
                _upsert_detached_ref(
                    store,
                    session_id=session_id,
                    artifact_ref=artifact_ref,
                    event_id=event_id,
                    event_seq=event_seq,
                    timestamp=now,
                )
            else:
                store._record_store.execute_count(
                    "DELETE FROM session_detached_artifacts "
                    "WHERE session_id = ? AND artifact_ref = ?",
                    (session_id, artifact_ref),
                )
            store._touch_session_tx(session_id=session_id, ts=now)
        store._invalidate_slice_cache(session_id)
        return "applied"
    finally:
        store.release_session_turn_lease(
            session_id,
            owner="desktop-artifact-decision",
            fence_token=int(lease.fence_token),
        )


def _require_projection_capacity(store: Any, session_id: str) -> None:
    rows = store._record_store.query_dicts(
        "SELECT COUNT(*) AS count FROM session_detached_artifacts WHERE session_id = ?",
        (session_id,),
    )
    if rows and int(rows[0]["count"]) >= 256:
        raise ArtifactLifecycleError(
            "artifact_state_backpressure",
            "Artifact state is at capacity.",
        )


def _upsert_detached_ref(
    store: Any,
    *,
    session_id: str,
    artifact_ref: str,
    event_id: str,
    event_seq: int,
    timestamp: str,
) -> None:
    store._record_store.execute_count(
        """
        INSERT INTO session_detached_artifacts(
          session_id, artifact_ref, event_id, event_seq, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(session_id, artifact_ref) DO UPDATE SET
          event_id = excluded.event_id,
          event_seq = excluded.event_seq,
          updated_at = excluded.updated_at
        """,
        (session_id, artifact_ref, event_id, event_seq, timestamp),
    )


__all__ = [
    "ArtifactLifecycleError",
    "apply_artifact_decision",
    "get_detached_artifact_refs",
]
