import datetime
import json
import sqlite3
from typing import Any

from sqlalchemy import text

from openminion.base.time import utc_now
from openminion.modules.memory.config import (
    MEMORY_GC_SUMMARY_COMPRESS_AFTER_DAYS,
    MEMORY_GC_SUMMARY_COMPRESS_MAX_CHARS,
    MEMORY_GC_SUMMARY_DELETE_AFTER_DAYS,
)
from openminion.modules.memory.runtime.gc_records import (
    parse_when,
    remove_collected_artifact_refs,
    soft_delete_postgres_record,
    soft_delete_sqlite_record,
)
from openminion.modules.memory.storage.base import MemoryStore
from openminion.modules.memory.storage.postgres.sql import _build_search_text
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore


def compress_old_summaries(
    store: MemoryStore,
    *,
    max_age_days: int = MEMORY_GC_SUMMARY_COMPRESS_AFTER_DAYS,
    delete_age_days: int = MEMORY_GC_SUMMARY_DELETE_AFTER_DAYS,
    max_summary_chars: int = MEMORY_GC_SUMMARY_COMPRESS_MAX_CHARS,
) -> tuple[int, int]:
    now = utc_now()
    compress_before = now - datetime.timedelta(days=max(1, int(max_age_days)))
    delete_before = now - datetime.timedelta(days=max(1, int(delete_age_days)))

    if isinstance(store, PostgresMemoryStore):
        compressed, deleted, removed_edges = _compress_postgres_summaries(
            store,
            now=now,
            compress_before=compress_before,
            delete_before=delete_before,
            max_summary_chars=max_summary_chars,
        )
        remove_collected_artifact_refs(store, removed_edges)
        return compressed, deleted

    compressed, deleted, removed_edges = _compress_sqlite_summaries(
        store,
        now=now,
        compress_before=compress_before,
        delete_before=delete_before,
        max_summary_chars=max_summary_chars,
    )
    remove_collected_artifact_refs(store, removed_edges)
    return compressed, deleted


def _shorten_summary_text(text_value: str, *, max_chars: int) -> str:
    normalized = " ".join(str(text_value or "").split()).strip()
    if not normalized:
        return ""
    for sep in (". ", "? ", "! "):
        if sep in normalized:
            first = normalized.split(sep, 1)[0].strip()
            if first:
                normalized = first
                break
    return normalized[:max_chars].strip()


def _summary_created_at(row: Any, *, postgres: bool) -> datetime.datetime | None:
    if postgres:
        return parse_when(str(row.get("created_at") or "")) or parse_when(
            str(row.get("updated_at") or "")
        )
    return parse_when(str(row["created_at"] or "")) or parse_when(
        str(row["updated_at"] or "")
    )


def _compress_postgres_summaries(
    store: PostgresMemoryStore,
    *,
    now: datetime.datetime,
    compress_before: datetime.datetime,
    delete_before: datetime.datetime,
    max_summary_chars: int,
) -> tuple[int, int, list[tuple[str, list[Any]]]]:
    compressed = 0
    deleted = 0
    removed_edges: list[tuple[str, list[Any]]] = []
    with store.gc_connection() as conn:
        rows = (
            conn.execute(
                text(
                    """
                    SELECT id, scope, type, key, title, content_json, tags_json,
                           entities_json, created_at, updated_at
                    FROM memory_records
                    WHERE is_deleted = FALSE AND type = 'session_summary'
                    """
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            created_at = _summary_created_at(row, postgres=True)
            if created_at is None:
                continue
            record_id = str(row["id"])
            if created_at <= delete_before:
                removed_edges.append(
                    (
                        record_id,
                        soft_delete_postgres_record(conn, record_id, now_iso=now.isoformat()),
                    )
                )
                deleted += 1
                continue
            if created_at > compress_before:
                continue
            content = row.get("content_json") or {}
            if not isinstance(content, dict):
                continue
            content = dict(content)
            content["summary_text"] = _shorten_summary_text(
                str(content.get("summary_text", "") or ""),
                max_chars=max_summary_chars,
            )
            conn.execute(
                text(
                    """
                    UPDATE memory_records
                       SET content_json = CAST(:content_json AS JSONB),
                           updated_at = :updated_at,
                           search_text = :search_text
                     WHERE id = :id
                    """
                ),
                {
                    "content_json": json.dumps(content, sort_keys=True),
                    "updated_at": now.isoformat(),
                    "search_text": _build_search_text(
                        scope=str(row.get("scope") or ""),
                        record_type=str(row.get("type") or ""),
                        key=row.get("key"),
                        title=row.get("title"),
                        content=content,
                        tags=list(row.get("tags_json") or []),
                        entities=list(row.get("entities_json") or []),
                    ),
                    "id": record_id,
                },
            )
            compressed += 1
    return compressed, deleted, removed_edges


def _compress_sqlite_summaries(
    store: MemoryStore,
    *,
    now: datetime.datetime,
    compress_before: datetime.datetime,
    delete_before: datetime.datetime,
    max_summary_chars: int,
) -> tuple[int, int, list[tuple[str, list[Any]]]]:
    compressed = 0
    deleted = 0
    removed_edges: list[tuple[str, list[Any]]] = []
    with store._connect() as conn:
        conn.execute("BEGIN")
        try:
            rows = conn.execute(
                """
                SELECT id, title, content_json, created_at, updated_at
                FROM memory_records
                WHERE is_deleted = 0 AND type = 'session_summary'
                """
            ).fetchall()
            for row in rows:
                created_at = _summary_created_at(row, postgres=False)
                if created_at is None:
                    continue
                record_id = str(row["id"])
                if created_at <= delete_before:
                    removed_edges.append(
                        (
                            record_id,
                            soft_delete_sqlite_record(conn, record_id, now_iso=now.isoformat()),
                        )
                    )
                    deleted += 1
                    continue
                if created_at > compress_before:
                    continue
                content = json.loads(str(row["content_json"] or "{}"))
                if not isinstance(content, dict):
                    continue
                shortened = _shorten_summary_text(
                    str(content.get("summary_text", "") or ""),
                    max_chars=max_summary_chars,
                )
                content["summary_text"] = shortened
                conn.execute(
                    "UPDATE memory_records SET content_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(content, sort_keys=True), now.isoformat(), record_id),
                )
                _replace_sqlite_summary_fts(conn, record_id=record_id, shortened=shortened)
                compressed += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return compressed, deleted, removed_edges


def _replace_sqlite_summary_fts(
    conn: sqlite3.Connection,
    *,
    record_id: str,
    shortened: str,
) -> None:
    row_fts = conn.execute(
        """
        SELECT scope, type, key, title, tags_text, entities_text
        FROM memory_fts
        WHERE id = ?
        """,
        (record_id,),
    ).fetchone()
    conn.execute("DELETE FROM memory_fts WHERE id = ?", (record_id,))
    if row_fts is None:
        return
    conn.execute(
        """
        INSERT INTO memory_fts(id, scope, type, key, title, content_text, tags_text, entities_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            row_fts["scope"],
            row_fts["type"],
            row_fts["key"],
            row_fts["title"],
            shortened,
            row_fts["tags_text"],
            row_fts["entities_text"],
        ),
    )
