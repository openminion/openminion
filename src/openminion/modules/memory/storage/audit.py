import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Protocol, runtime_checkable

from sophiagraph.audit.events import MemoryAuditEvent


@runtime_checkable
class MemoryAuditSink(Protocol):
    def append_event(self, event: MemoryAuditEvent) -> None: ...


class InMemoryMemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[MemoryAuditEvent] = []

    def append_event(self, event: MemoryAuditEvent) -> None:
        self.events.append(event)


class SQLiteMemoryAuditSink:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        with self._lock:
            if self._conn is None:
                conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memory_audit_events (
                        event_id TEXT PRIMARY KEY,
                        timestamp TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        target_kind TEXT NOT NULL,
                        target_id TEXT,
                        scope TEXT,
                        record_type TEXT,
                        record_key TEXT,
                        session_id TEXT,
                        details_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_memory_audit_events_ts
                    ON memory_audit_events(timestamp)
                    """
                )
                conn.commit()
                self._conn = conn
        return self._conn

    def append_event(self, event: MemoryAuditEvent) -> None:
        payload = event.to_dict()
        conn = self._connect()
        with self._lock:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_audit_events(
                    event_id, timestamp, event_type, target_kind, target_id,
                    scope, record_type, record_key, session_id, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["event_id"],
                    payload["timestamp"],
                    payload["event_type"],
                    payload["target_kind"],
                    payload["target_id"],
                    payload["scope"],
                    payload["record_type"],
                    payload["record_key"],
                    payload["session_id"],
                    json.dumps(payload["details"], ensure_ascii=True, sort_keys=True),
                ),
            )
            conn.commit()

    def list_events(self) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT event_id, timestamp, event_type, target_kind, target_id,
                   scope, record_type, record_key, session_id, details_json
            FROM memory_audit_events
            ORDER BY timestamp ASC, event_id ASC
            """
        ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            events.append(
                {
                    "event_id": row["event_id"],
                    "timestamp": row["timestamp"],
                    "event_type": row["event_type"],
                    "target_kind": row["target_kind"],
                    "target_id": row["target_id"],
                    "scope": row["scope"],
                    "record_type": row["record_type"],
                    "record_key": row["record_key"],
                    "session_id": row["session_id"],
                    "details": json.loads(row["details_json"] or "{}"),
                }
            )
        return events


def default_memory_audit_db_path(db_path: str | Path) -> Path:
    base = Path(db_path)
    if base.suffix:
        return base.with_name(f"{base.stem}.audit.db")
    return base / "memory.audit.db"


class AuditedMemoryStore:
    """MemoryStore wrapper that emits append-only audit events on mutation."""

    def __init__(self, store: Any, sink: MemoryAuditSink | None = None) -> None:
        self._store = store
        self._sink = sink

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def _append(self, event: MemoryAuditEvent) -> None:
        if self._sink is None:
            return
        try:
            self._sink.append_event(event)
        except Exception:
            pass

    def put(self, record: Any) -> str:
        record_id = self._store.put(record)
        self._append(
            MemoryAuditEvent(
                event_type="memory.record.put",
                target_kind="record",
                target_id=str(record_id or getattr(record, "id", "") or ""),
                scope=str(getattr(record, "scope", "") or "") or None,
                record_type=str(getattr(record, "type", "") or "") or None,
                record_key=str(getattr(record, "key", "") or "") or None,
                details={"title": str(getattr(record, "title", "") or "")},
            )
        )
        return record_id

    def upsert(
        self, scope: str, type: str, key: str, record_patch: dict[str, Any]
    ) -> Any:
        record = self._store.upsert(scope, type, key, record_patch)
        self._append(
            MemoryAuditEvent(
                event_type="memory.record.upsert",
                target_kind="record",
                target_id=str(getattr(record, "id", "") or ""),
                scope=str(getattr(record, "scope", "") or scope) or None,
                record_type=str(getattr(record, "type", "") or type) or None,
                record_key=str(getattr(record, "key", "") or key) or None,
                details={"patched_fields": sorted(str(k) for k in record_patch)},
            )
        )
        return record

    def delete(
        self,
        record_id: str,
        *,
        reason: str | None = None,
        deleted_at: str | None = None,
    ) -> None:
        """Forward delete audit metadata when the wrapped store accepts it."""

        current = None
        if hasattr(self._store, "get"):
            try:
                current = self._store.get(record_id)
            except Exception:
                current = None
        try:
            self._store.delete(record_id, reason=reason, deleted_at=deleted_at)
        except TypeError:
            self._store.delete(record_id)
        details: dict[str, Any] = {}
        if reason is not None:
            details["reason"] = reason
        if deleted_at is not None:
            details["deleted_at"] = deleted_at
        self._append(
            MemoryAuditEvent(
                event_type="memory.record.delete",
                target_kind="record",
                target_id=str(record_id or ""),
                scope=str(getattr(current, "scope", "") or "") or None,
                record_type=str(getattr(current, "type", "") or "") or None,
                record_key=str(getattr(current, "key", "") or "") or None,
                details=details,
            )
        )

    def tombstone(self, scope: str, type: str, key: str) -> None:
        self._store.tombstone(scope, type, key)
        self._append(
            MemoryAuditEvent(
                event_type="memory.record.tombstone",
                target_kind="record_key",
                scope=str(scope or "") or None,
                record_type=str(type or "") or None,
                record_key=str(key or "") or None,
            )
        )

    def apply_outcome_feedback(
        self,
        record_ids: list[str],
        *,
        outcome: str,
        command_id: str,
        observed_at: str,
        feedback_delta: float,
    ) -> int:
        updated = self._store.apply_outcome_feedback(
            record_ids,
            outcome=outcome,
            command_id=command_id,
            observed_at=observed_at,
            feedback_delta=feedback_delta,
        )
        if updated:
            self._append(
                MemoryAuditEvent(
                    event_type="memory.record.feedback",
                    target_kind="record_batch",
                    details={
                        "record_ids": list(record_ids),
                        "outcome": str(outcome or ""),
                        "command_id": str(command_id or ""),
                        "observed_at": str(observed_at or ""),
                        "feedback_delta": float(feedback_delta),
                        "updated_count": int(updated),
                    },
                )
            )
        return updated

    def candidate_put(self, candidate: Any) -> str:
        candidate_id = self._store.candidate_put(candidate)
        self._append(
            MemoryAuditEvent(
                event_type="memory.candidate.put",
                target_kind="candidate",
                target_id=str(
                    candidate_id or getattr(candidate, "candidate_id", "") or ""
                ),
                scope=str(getattr(candidate, "proposed_scope", "") or "") or None,
                record_type=str(getattr(candidate, "type", "") or "") or None,
                record_key=str(getattr(candidate, "key", "") or "") or None,
                session_id=str(getattr(candidate, "session_id", "") or "") or None,
            )
        )
        return candidate_id

    def candidate_update(self, candidate_id: str, patch: dict[str, Any]) -> Any:
        candidate = self._store.candidate_update(candidate_id, patch)
        self._append(
            MemoryAuditEvent(
                event_type="memory.candidate.update",
                target_kind="candidate",
                target_id=str(candidate_id or ""),
                scope=str(getattr(candidate, "proposed_scope", "") or "") or None,
                record_type=str(getattr(candidate, "type", "") or "") or None,
                record_key=str(getattr(candidate, "key", "") or "") or None,
                session_id=str(getattr(candidate, "session_id", "") or "") or None,
                details={"patched_fields": sorted(str(k) for k in patch)},
            )
        )
        return candidate

    def candidate_delete(self, candidate_id: str) -> Any:
        delete_fn = getattr(self._store, "candidate_delete", None)
        if not callable(delete_fn):
            raise AttributeError("candidate_delete is unsupported by the wrapped store")
        result = delete_fn(candidate_id)
        self._append(
            MemoryAuditEvent(
                event_type="memory.candidate.delete",
                target_kind="candidate",
                target_id=str(candidate_id or ""),
            )
        )
        return result

    def put_relation(self, relation: Any) -> str:
        relation_id = self._store.put_relation(relation)
        self._append(
            MemoryAuditEvent(
                event_type="memory.relation.put",
                target_kind="relation",
                target_id=str(
                    relation_id or getattr(relation, "relation_id", "") or ""
                ),
                details={
                    "source_record_id": str(
                        getattr(relation, "source_record_id", "") or ""
                    ),
                    "target_record_id": str(
                        getattr(relation, "target_record_id", "") or ""
                    ),
                    "relation_type": str(getattr(relation, "relation_type", "") or ""),
                },
            )
        )
        return relation_id

    def put_tier_transition(self, transition: Any) -> str:
        transition_id = self._store.put_tier_transition(transition)
        self._append(
            MemoryAuditEvent(
                event_type="memory.tier_transition.put",
                target_kind="tier_transition",
                target_id=str(
                    transition_id or getattr(transition, "transition_id", "") or ""
                ),
                scope=str(getattr(transition, "scope", "") or "") or None,
                record_type=str(getattr(transition, "record_type", "") or "") or None,
                details={
                    "record_id": str(getattr(transition, "record_id", "") or ""),
                    "from_tier": str(getattr(transition, "from_tier", "") or ""),
                    "to_tier": str(getattr(transition, "to_tier", "") or ""),
                    "transition_reason": str(
                        getattr(transition, "transition_reason", "") or ""
                    ),
                },
            )
        )
        return transition_id

    def promote_candidate(self, candidate_id: str, target_scope: str) -> Any:
        record = self._store.promote_candidate(candidate_id, target_scope)
        self._append(
            MemoryAuditEvent(
                event_type="memory.candidate.promote",
                target_kind="record",
                target_id=str(getattr(record, "id", "") or ""),
                scope=str(getattr(record, "scope", "") or target_scope) or None,
                record_type=str(getattr(record, "type", "") or "") or None,
                record_key=str(getattr(record, "key", "") or "") or None,
                details={"candidate_id": str(candidate_id or "")},
            )
        )
        return record

    def supersede_by_contradiction(
        self, old_record_id: str, new_record_id: str, reason: str = ""
    ) -> Any:
        record = self._store.supersede_by_contradiction(
            old_record_id, new_record_id, reason=reason
        )
        self._append(
            MemoryAuditEvent(
                event_type="memory.record.supersede",
                target_kind="record",
                target_id=str(new_record_id or ""),
                scope=str(getattr(record, "scope", "") or "") or None,
                record_type=str(getattr(record, "type", "") or "") or None,
                record_key=str(getattr(record, "key", "") or "") or None,
                details={
                    "old_record_id": str(old_record_id or ""),
                    "reason": str(reason or ""),
                },
            )
        )
        return record

    def invalidate(
        self,
        record_id: str,
        *,
        valid_to: str,
        reason: str,
    ) -> Any:
        current = None
        if hasattr(self._store, "get"):
            try:
                current = self._store.get(record_id)
            except Exception:
                current = None
        record = self._store.invalidate(record_id, valid_to=valid_to, reason=reason)
        self._append(
            MemoryAuditEvent(
                event_type="memory.record.invalidate",
                target_kind="record",
                target_id=str(record_id or ""),
                scope=str(getattr(current, "scope", "") or "") or None,
                record_type=str(getattr(current, "type", "") or "") or None,
                record_key=str(getattr(current, "key", "") or "") or None,
                details={
                    "valid_to": str(valid_to or ""),
                    "reason": str(reason or ""),
                },
            )
        )
        return record


__all__ = [
    "AuditedMemoryStore",
    "InMemoryMemoryAuditSink",
    "MemoryAuditEvent",
    "MemoryAuditSink",
    "SQLiteMemoryAuditSink",
    "default_memory_audit_db_path",
]
