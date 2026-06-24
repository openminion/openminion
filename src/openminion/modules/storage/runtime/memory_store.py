from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from .sqlite import connect_database

from openminion.base.time import utc_now_iso as _utc_now_iso


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    session_id: str
    message_id: Optional[str]
    event_id: Optional[int]
    chunk_ref: str
    content: str
    content_type: str
    scope: str
    agent_id: str
    project_id: str
    idempotency_key: str
    importance: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MemoryVector:
    id: str
    memory_record_id: str
    provider: str
    model: str
    embedding: bytes
    embedding_dim: int
    sync_status: str
    error_message: str
    retry_count: int
    last_retry_at: str
    embedded_at: str
    created_at: str
    updated_at: str


def _row_to_memory_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        session_id=row["session_id"],
        message_id=row["message_id"],
        event_id=row["event_id"],
        chunk_ref=row["chunk_ref"],
        content=row["content"],
        content_type=row["content_type"],
        scope=row["scope"],
        agent_id=row["agent_id"],
        project_id=row["project_id"],
        idempotency_key=row["idempotency_key"],
        importance=row["importance"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_memory_vector(row: sqlite3.Row) -> MemoryVector:
    return MemoryVector(
        id=row["id"],
        memory_record_id=row["memory_record_id"],
        provider=row["provider"],
        model=row["model"],
        embedding=row["embedding"],
        embedding_dim=row["embedding_dim"],
        sync_status=row["sync_status"],
        error_message=row["error_message"],
        retry_count=row["retry_count"],
        last_retry_at=row["last_retry_at"],
        embedded_at=row["embedded_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class MemoryRecordStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def ensure_record_with_id(
        self,
        record_id: str,
        content: str,
        session_id: str = "",
        chunk_ref: str = "",
    ) -> MemoryRecord:
        existing = self.get_record(record_id)
        if existing:
            return existing
        now = _utc_now_iso()
        idempotency_key = f"ensure:{record_id}"
        self._conn.execute(
            """
            INSERT INTO memory_records(
                id, session_id, message_id, event_id, chunk_ref, content, content_type,
                scope, agent_id, project_id, idempotency_key, importance, created_at, updated_at
            ) VALUES (?, ?, NULL, NULL, ?, ?, 'text', 'session', '', '', ?, 1, ?, ?)
            """,
            (
                record_id,
                session_id or f"session-{record_id[:8]}",
                chunk_ref or f"chunk-{record_id}",
                content,
                idempotency_key,
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get_record(record_id)

    def upsert_record(
        self,
        *,
        session_id: str,
        message_id: Optional[str] = None,
        event_id: Optional[int] = None,
        chunk_ref: str,
        content: str,
        content_type: str = "text",
        scope: str = "session",
        agent_id: str = "",
        project_id: str = "",
        importance: int = 1,
    ) -> MemoryRecord:
        now = _utc_now_iso()
        idempotency_key = (
            f"{session_id}:{message_id or ''}:{event_id or ''}:{chunk_ref}"
        )

        existing = self._conn.execute(
            "SELECT id FROM memory_records WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()

        if existing:
            record_id = existing["id"]
            self._conn.execute(
                """
                UPDATE memory_records SET
                    content = ?, content_type = ?, importance = ?, updated_at = ?
                WHERE id = ?
                """,
                (content, content_type, importance, now, record_id),
            )
            self._conn.commit()
            return self.get_record(record_id)

        record_id = uuid4().hex
        self._conn.execute(
            """
            INSERT INTO memory_records(
                id, session_id, message_id, event_id, chunk_ref, content, content_type,
                scope, agent_id, project_id, idempotency_key, importance, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                session_id,
                message_id,
                event_id,
                chunk_ref,
                content,
                content_type,
                scope,
                agent_id,
                project_id,
                idempotency_key,
                importance,
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get_record(record_id)

    def get_record(self, record_id: str) -> Optional[MemoryRecord]:
        row = self._conn.execute(
            "SELECT * FROM memory_records WHERE id = ?",
            (record_id,),
        ).fetchone()
        return _row_to_memory_record(row) if row else None

    def list_by_session(
        self,
        *,
        session_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryRecord]:
        rows = self._conn.execute(
            """
            SELECT * FROM memory_records
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (session_id, limit, offset),
        ).fetchall()
        return [_row_to_memory_record(row) for row in rows]

    def list_pending_vectors(
        self,
        *,
        limit: int = 100,
    ) -> List[MemoryRecord]:
        rows = self._conn.execute(
            """
            SELECT mr.* FROM memory_records mr
            LEFT JOIN memory_vectors mv ON mr.id = mv.memory_record_id
            WHERE mv.id IS NULL
            ORDER BY mr.created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_memory_record(row) for row in rows]

    def delete_record(self, record_id: str) -> None:
        self._conn.execute("DELETE FROM memory_records WHERE id = ?", (record_id,))
        self._conn.commit()


class MemoryVectorStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def upsert_vector(
        self,
        *,
        memory_record_id: str,
        provider: str = "zvec",
        model: str = "",
        embedding: bytes,
        embedding_dim: int = 1536,
    ) -> MemoryVector:
        now = _utc_now_iso()
        vector_id = uuid4().hex

        self._conn.execute(
            """
            INSERT OR REPLACE INTO memory_vectors(
                id, memory_record_id, provider, model, embedding, embedding_dim,
                sync_status, error_message, retry_count, last_retry_at, embedded_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'embedded', '', 0, '', ?, ?, ?)
            """,
            (
                vector_id,
                memory_record_id,
                provider,
                model,
                embedding,
                embedding_dim,
                now,
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get_vector(vector_id)

    def get_vector(self, vector_id: str) -> Optional[MemoryVector]:
        row = self._conn.execute(
            "SELECT * FROM memory_vectors WHERE id = ?",
            (vector_id,),
        ).fetchone()
        return _row_to_memory_vector(row) if row else None

    def get_by_record(self, memory_record_id: str) -> Optional[MemoryVector]:
        row = self._conn.execute(
            "SELECT * FROM memory_vectors WHERE memory_record_id = ?",
            (memory_record_id,),
        ).fetchone()
        return _row_to_memory_vector(row) if row else None

    def update_sync_status(
        self,
        *,
        vector_id: str,
        status: str,
        error_message: str = "",
    ) -> None:
        now = _utc_now_iso()
        retry_increment = (
            "retry_count = retry_count + 1"
            if status == "failed"
            else "retry_count = retry_count"
        )

        self._conn.execute(
            f"""
            UPDATE memory_vectors SET
                sync_status = ?, error_message = ?, {retry_increment},
                last_retry_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, error_message, now, now, vector_id),
        )
        self._conn.commit()

    def list_by_status(
        self,
        *,
        status: str,
        limit: int = 100,
    ) -> List[MemoryVector]:
        rows = self._conn.execute(
            """
            SELECT * FROM memory_vectors
            WHERE sync_status = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
        return [_row_to_memory_vector(row) for row in rows]


def create_memory_record_store(db_path: str | Path) -> MemoryRecordStore:
    from .migrations import run_migrations

    conn = connect_database(db_path)
    run_migrations(conn)
    return MemoryRecordStore(conn)


def create_memory_vector_store(db_path: str | Path) -> MemoryVectorStore:
    from .migrations import run_migrations

    conn = connect_database(db_path)
    run_migrations(conn)
    return MemoryVectorStore(conn)


def create_memory_stores(
    db_path: str | Path,
) -> tuple[MemoryRecordStore, MemoryVectorStore]:
    from .migrations import run_migrations

    conn = connect_database(db_path)
    run_migrations(conn)
    return MemoryRecordStore(conn), MemoryVectorStore(conn)
