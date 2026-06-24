import json
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Mapping

from openminion.modules.storage.record_store import RecordStore, RecordStoreSQLite

from ..schemas import (
    CheckpointFailedPayload,
    CheckpointStats,
    CheckpointStructuredState,
    CompressionCheckpoint,
    StructuredConstraint,
    StructuredDecision,
    StructuredOpenLoop,
    StructuredToolDigest,
)

_DDL = """
CREATE TABLE IF NOT EXISTS compression_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    from_event_id TEXT,
    to_event_id TEXT NOT NULL,
    summary_text TEXT NOT NULL DEFAULT '',
    recent_window_event_ids TEXT NOT NULL DEFAULT '[]',
    structured_json TEXT NOT NULL DEFAULT '{}',
    stats_json TEXT NOT NULL DEFAULT '{}',
    version TEXT NOT NULL DEFAULT '1.6'
);

CREATE TABLE IF NOT EXISTS checkpoint_latest_pointer (
    session_id TEXT PRIMARY KEY,
    checkpoint_id TEXT NOT NULL,
    FOREIGN KEY (checkpoint_id) REFERENCES compression_checkpoints(checkpoint_id)
);

CREATE TABLE IF NOT EXISTS checkpoint_failed_events (
    failure_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    error_code TEXT NOT NULL,
    from_event_id TEXT,
    until_event_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);
"""


def _serialize_structured(structured: CheckpointStructuredState) -> str:
    return json.dumps(asdict(structured), sort_keys=True)


def _deserialize_structured(raw: str) -> CheckpointStructuredState:
    data = json.loads(raw)
    decisions = [StructuredDecision(**d) for d in data.get("decisions", [])]
    constraints = [StructuredConstraint(**c) for c in data.get("constraints", [])]
    open_loops = [StructuredOpenLoop(**o) for o in data.get("open_loops", [])]
    entities = data.get("entities", {})
    tool_digests = [StructuredToolDigest(**t) for t in data.get("tool_digests", [])]
    return CheckpointStructuredState(
        decisions=decisions,
        constraints=constraints,
        open_loops=open_loops,
        entities=entities,
        tool_digests=tool_digests,
    )


def _deserialize_stats(raw: str) -> CheckpointStats:
    data = json.loads(raw)
    return CheckpointStats(**data)


def _row_to_checkpoint(row: Mapping[str, object]) -> CompressionCheckpoint:
    return CompressionCheckpoint(
        checkpoint_id=row["checkpoint_id"],
        session_id=row["session_id"],
        created_at=row["created_at"],
        from_event_id=row["from_event_id"],
        to_event_id=row["to_event_id"],
        summary_text=row["summary_text"],
        recent_window_event_ids=json.loads(row["recent_window_event_ids"]),
        structured=_deserialize_structured(row["structured_json"]),
        stats=_deserialize_stats(row["stats_json"]),
        version=row["version"],
    )


def _create_checkpoint_schema(store: RecordStore) -> None:
    for statement in [part.strip() for part in _DDL.split(";") if part.strip()]:
        store.execute_count(statement)


class _CheckpointStoreMixin:
    """Backend-neutral checkpoint store behavior."""

    @contextmanager
    def _tx(self) -> object:
        with self._record_store.transaction():
            yield self._record_store

    def close(self) -> None:
        self._record_store.close()

    def save_checkpoint(self, checkpoint: CompressionCheckpoint) -> str:
        """Persist a checkpoint and update the latest-pointer. Returns checkpoint_id."""
        with self._tx() as store:
            store.execute_count(
                """
                INSERT INTO compression_checkpoints (
                    checkpoint_id, session_id, created_at, from_event_id, to_event_id,
                    summary_text, recent_window_event_ids, structured_json, stats_json, version
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(checkpoint_id) DO UPDATE SET
                    session_id=excluded.session_id,
                    created_at=excluded.created_at,
                    from_event_id=excluded.from_event_id,
                    to_event_id=excluded.to_event_id,
                    summary_text=excluded.summary_text,
                    recent_window_event_ids=excluded.recent_window_event_ids,
                    structured_json=excluded.structured_json,
                    stats_json=excluded.stats_json,
                    version=excluded.version
                """,
                (
                    checkpoint.checkpoint_id,
                    checkpoint.session_id,
                    checkpoint.created_at,
                    checkpoint.from_event_id,
                    checkpoint.to_event_id,
                    checkpoint.summary_text,
                    json.dumps(checkpoint.recent_window_event_ids),
                    _serialize_structured(checkpoint.structured),
                    json.dumps(asdict(checkpoint.stats)),
                    checkpoint.version,
                ),
            )
            store.execute_count(
                """
                INSERT INTO checkpoint_latest_pointer (session_id, checkpoint_id)
                VALUES (?,?)
                ON CONFLICT(session_id) DO UPDATE SET checkpoint_id = excluded.checkpoint_id
                """,
                (checkpoint.session_id, checkpoint.checkpoint_id),
            )
        return checkpoint.checkpoint_id

    def record_failure(self, failure: CheckpointFailedPayload) -> str:
        """Persist a checkpoint failure event. Returns failure_id."""
        with self._tx() as store:
            store.execute_count(
                """
                INSERT INTO checkpoint_failed_events (
                    failure_id, session_id, created_at, reason, error_code,
                    from_event_id, until_event_id, details_json
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    failure.failure_id,
                    failure.session_id,
                    failure.created_at,
                    failure.reason,
                    failure.error_code,
                    failure.from_event_id,
                    failure.until_event_id,
                    json.dumps(failure.details),
                ),
            )
        return failure.failure_id

    def delete_checkpoint(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint by ID. Returns True if deleted."""
        with self._tx() as store:
            store.execute_count(
                "DELETE FROM checkpoint_latest_pointer WHERE checkpoint_id=?",
                (checkpoint_id,),
            )
            count = store.execute_count(
                "DELETE FROM compression_checkpoints WHERE checkpoint_id=?",
                (checkpoint_id,),
            )
            return count > 0

    def get_checkpoint(self, checkpoint_id: str) -> CompressionCheckpoint | None:
        """Fetch a checkpoint by ID."""
        rows = self._record_store.query_dicts(
            "SELECT * FROM compression_checkpoints WHERE checkpoint_id=?",
            (checkpoint_id,),
        )
        return _row_to_checkpoint(rows[0]) if rows else None

    def get_latest_checkpoint(self, session_id: str) -> CompressionCheckpoint | None:
        """Fetch the most recent checkpoint for a session via the pointer table."""
        rows = self._record_store.query_dicts(
            """
            SELECT c.* FROM compression_checkpoints c
            JOIN checkpoint_latest_pointer p ON c.checkpoint_id = p.checkpoint_id
            WHERE p.session_id=?
            """,
            (session_id,),
        )
        if rows:
            return _row_to_checkpoint(rows[0])
        rows = self._record_store.query_dicts(
            "SELECT * FROM compression_checkpoints WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        )
        return _row_to_checkpoint(rows[0]) if rows else None

    def list_checkpoints(self, session_id: str) -> list[CompressionCheckpoint]:
        """Return all checkpoints for a session ordered by created_at ascending."""
        rows = self._record_store.query_dicts(
            "SELECT * FROM compression_checkpoints WHERE session_id=? ORDER BY created_at ASC",
            (session_id,),
        )
        return [_row_to_checkpoint(r) for r in rows]

    def list_failures(self, session_id: str) -> list[CheckpointFailedPayload]:
        """Return all failure events for a session."""
        rows = self._record_store.query_dicts(
            "SELECT * FROM checkpoint_failed_events WHERE session_id=? ORDER BY created_at ASC",
            (session_id,),
        )
        return [
            CheckpointFailedPayload(
                failure_id=row["failure_id"],
                session_id=row["session_id"],
                reason=row["reason"],
                error_code=row["error_code"],
                created_at=row["created_at"],
                from_event_id=row["from_event_id"],
                until_event_id=row["until_event_id"],
                details=json.loads(row["details_json"]),
            )
            for row in rows
        ]


class SQLiteCheckpointStore(_CheckpointStoreMixin):
    """SQLite-backed store for ``CompressionCheckpoint`` objects."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._record_store: RecordStore = RecordStoreSQLite(db_path, wal=True)
        _create_checkpoint_schema(self._record_store)


class PostgresCheckpointStore(_CheckpointStoreMixin):
    """Postgres-backed store for ``CompressionCheckpoint`` objects."""

    def __init__(self, *, record_store: RecordStore) -> None:
        self._record_store = record_store
        _create_checkpoint_schema(self._record_store)


class CheckpointStore(SQLiteCheckpointStore):
    """Backward-compatible SQLite alias for existing callers."""
