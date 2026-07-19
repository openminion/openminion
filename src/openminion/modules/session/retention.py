"""Session retention, expiry, holds, and physical purge."""

from __future__ import annotations

import hashlib
import sqlite3
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from openminion.base.constants import STATE_KEY_WORKING
from openminion.modules.session.interfaces import (
    SESSION_RETENTION_HOLD_VERSION,
    SESSION_RETENTION_PLAN_VERSION,
)


class SessionRetentionError(RuntimeError):
    code = "SESSION_RETENTION_ERROR"


class SessionRetentionSnapshotChangedError(SessionRetentionError):
    code = "SESSION_RETENTION_SNAPSHOT_CHANGED"


class SessionRetentionBlockedError(SessionRetentionError):
    code = "SESSION_RETENTION_BLOCKED"


@dataclass(frozen=True)
class SessionRetentionPolicy:
    inactivity_ttl_seconds: int = 30 * 24 * 60 * 60
    closed_retention_seconds: int = 7 * 24 * 60 * 60
    max_share_ttl_seconds: int = 24 * 60 * 60


@dataclass(frozen=True)
class SessionRetentionCandidate:
    session_id: str
    reason: str
    updated_at: str
    status: str
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "reason": self.reason,
            "updated_at": self.updated_at,
            "status": self.status,
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class SessionRetentionPlan:
    candidates: tuple[SessionRetentionCandidate, ...]
    policy: SessionRetentionPolicy
    snapshot_hash: str
    created_at: str
    schema_version: str = SESSION_RETENTION_PLAN_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "snapshot_hash": self.snapshot_hash,
            "policy": {
                "inactivity_ttl_seconds": self.policy.inactivity_ttl_seconds,
                "closed_retention_seconds": self.policy.closed_retention_seconds,
                "max_share_ttl_seconds": self.policy.max_share_ttl_seconds,
            },
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


class SessionRetentionService:
    def __init__(self, store: Any) -> None:
        self.store = store
        self._record_store = getattr(store, "_record_store", None)
        if self._record_store is None:
            raise TypeError("session retention requires a session store with a record store")
        self._ensure_schema()

    def add_hold(self, *, session_id: str, reason: str, actor_id: str = "operator") -> str:
        hold_id = f"hold-{hashlib.sha256(f'{session_id}:{reason}:{actor_id}'.encode()).hexdigest()[:16]}"
        now = _to_iso(_utc_now())
        self._record_store.insert(
            "session_retention_holds",
            {
                "hold_id": hold_id,
                "session_id": session_id,
                "reason": reason,
                "actor_id": actor_id,
                "created_at": now,
                "released_at": None,
                "schema_version": SESSION_RETENTION_HOLD_VERSION,
            },
        )
        self.store.append_event(
            session_id,
            event_type="session.retention.hold_added",
            payload={"hold_id": hold_id, "reason": reason},
            actor_type="system",
        )
        return hold_id

    def release_hold(self, hold_id: str) -> bool:
        rows = self._record_store.query_rows("session_retention_holds", where={"hold_id": hold_id}, limit=1)
        if not rows:
            return False
        now = _to_iso(_utc_now())
        changed = self._record_store.update_rows(
            "session_retention_holds", {"hold_id": hold_id}, {"released_at": now}
        )
        session_id = str(rows[0]["session_id"])
        self.store.append_event(
            session_id,
            event_type="session.retention.hold_released",
            payload={"hold_id": hold_id},
            actor_type="system",
        )
        return changed > 0

    def dry_run(
        self,
        *,
        policy: SessionRetentionPolicy | None = None,
        now: datetime | None = None,
    ) -> SessionRetentionPlan:
        resolved_policy = policy or SessionRetentionPolicy()
        current = now or _utc_now()
        candidates = tuple(self._candidate_rows(policy=resolved_policy, now=current))
        snapshot_hash = _snapshot_hash([candidate.to_dict() for candidate in candidates])
        return SessionRetentionPlan(
            candidates=candidates,
            policy=resolved_policy,
            snapshot_hash=snapshot_hash,
            created_at=_to_iso(current),
        )

    def purge(self, plan: SessionRetentionPlan, *, override_blockers: bool = False) -> dict[str, Any]:
        fresh = self.dry_run(policy=plan.policy)
        if fresh.snapshot_hash != plan.snapshot_hash:
            raise SessionRetentionSnapshotChangedError("retention candidate snapshot changed")
        blocked = [item for item in fresh.candidates if item.blockers]
        if blocked and not override_blockers:
            raise SessionRetentionBlockedError("retention purge has active blockers")
        deleted: dict[str, int] = {}
        for candidate in fresh.candidates:
            deleted[candidate.session_id] = self._purge_session(candidate.session_id)
        return {
            "schema_version": SESSION_RETENTION_PLAN_VERSION,
            "purged_session_count": len(fresh.candidates),
            "deleted_rows": deleted,
            "override_blockers": bool(override_blockers),
        }

    def _candidate_rows(
        self,
        *,
        policy: SessionRetentionPolicy,
        now: datetime,
    ) -> list[SessionRetentionCandidate]:
        rows = self._record_store.query_dicts(
            "SELECT session_id, updated_at, status FROM sessions ORDER BY updated_at ASC"
        )
        candidates: list[SessionRetentionCandidate] = []
        for row in rows:
            updated_at = str(row["updated_at"])
            status = str(row["status"])
            reason = _retention_reason(status=status, updated_at=updated_at, policy=policy, now=now)
            if reason is None:
                continue
            session_id = str(row["session_id"])
            candidates.append(
                SessionRetentionCandidate(
                    session_id=session_id,
                    reason=reason,
                    updated_at=updated_at,
                    status=status,
                    blockers=tuple(self._blockers(session_id, now=now)),
                )
            )
        return candidates

    def _blockers(self, session_id: str, *, now: datetime) -> list[str]:
        blockers: list[str] = []
        if self._has_rows(
            "SELECT 1 FROM session_retention_holds WHERE session_id = ? AND released_at IS NULL",
            (session_id,),
        ):
            blockers.append("retention_hold")
        if self._has_rows(
            "SELECT 1 FROM session_turn_leases WHERE session_id = ? AND released_at IS NULL AND expires_at > ?",
            (session_id, _to_iso(now)),
        ):
            blockers.append("active_turn_lease")
        if self._has_rows(
            "SELECT 1 FROM session_shares WHERE session_id = ? AND revoked_at IS NULL AND expires_at > ?",
            (session_id, _to_iso(now)),
        ):
            blockers.append("active_share")
        return blockers

    def _has_rows(self, sql: str, params: tuple[Any, ...]) -> bool:
        return bool(self._record_store.query_dicts(sql, params))

    def _purge_session(self, session_id: str) -> int:
        tables = (
            "session_retention_holds",
            "session_shares",
            "message_refs",
            "run_records",
            "seed_bundles",
            "compression_checkpoints",
            "prompt_contexts",
            "session_snapshots",
            "session_summaries",
            "summary_deltas",
            STATE_KEY_WORKING,
            "turns",
            "session_events",
            "events",
            "summaries",
            "session_turn_leases",
            "sessions",
        )
        total = 0
        with self._record_store.transaction():
            for table in tables:
                total += self._delete_from_table(table, session_id)
        return total

    def _delete_from_table(self, table: str, session_id: str) -> int:
        try:
            return self._record_store.delete_rows(table, {"session_id": session_id})
        except (sqlite3.DatabaseError, RuntimeError, ValueError):
            return 0

    def _ensure_schema(self) -> None:
        for statement in SESSION_RETENTION_SCHEMA:
            self._record_store.execute_count(statement)


SESSION_RETENTION_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS session_retention_holds (
      hold_id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      reason TEXT NOT NULL,
      actor_id TEXT NOT NULL,
      created_at TEXT NOT NULL,
      released_at TEXT,
      schema_version TEXT NOT NULL DEFAULT 'session_retention_hold.v1',
      FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_retention_holds_session
    ON session_retention_holds(session_id, released_at)
    """,
)


def _retention_reason(
    *,
    status: str,
    updated_at: str,
    policy: SessionRetentionPolicy,
    now: datetime,
) -> str | None:
    age = now - _parse_iso(updated_at)
    if status in {"closed", "archived"} and age >= timedelta(seconds=policy.closed_retention_seconds):
        return "closed_retention_elapsed"
    if age >= timedelta(seconds=policy.inactivity_ttl_seconds):
        return "inactivity_ttl_elapsed"
    return None


def _snapshot_hash(candidates: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(candidates, sort_keys=True).encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
