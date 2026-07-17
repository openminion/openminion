import datetime
import json
from typing import Any

from openminion.base.time import utc_now as _utc_now
from openminion.modules.memory.config import (
    MEMORY_GC_SUMMARY_COMPRESS_AFTER_DAYS,
    MEMORY_GC_SUMMARY_COMPRESS_MAX_CHARS,
    MEMORY_GC_SUMMARY_DELETE_AFTER_DAYS,
)
from openminion.modules.memory.models import MemoryScope
from openminion.modules.memory.storage.base import MemoryStore
from openminion.modules.memory.storage.postgres.sql import _build_search_text
from openminion.modules.memory.storage.postgres.store import (
    PostgresMemoryStore,
)
from openminion.modules.memory.runtime.purge import (
    GCResult,
    decode_evidence_ref_values as _decode_evidence_ref_values,
    purge_soft_deleted,
    remove_artifact_refs as _remove_artifact_refs,
)


def _parse_when(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _delete_fts_entry(conn, record_id: str) -> None:  # type: ignore[no-untyped-def]
    conn.execute("DELETE FROM memory_fts WHERE id = ?", (record_id,))


def _soft_delete_record(
    conn,
    record_id: str,
    *,
    now_iso: str,
) -> list[Any]:  # type: ignore[no-untyped-def]
    row = conn.execute(
        "SELECT evidence_json FROM memory_records WHERE id = ?",
        (record_id,),
    ).fetchone()
    conn.execute(
        "UPDATE memory_records SET is_deleted = 1, updated_at = ? WHERE id = ?",
        (now_iso, record_id),
    )
    _delete_fts_entry(conn, record_id)
    if row is None:
        return []
    return _decode_evidence_ref_values(row["evidence_json"])


def _soft_delete_record_postgres(
    conn,  # type: ignore[no-untyped-def]
    record_id: str,
    *,
    now_iso: str,
) -> list[Any]:
    from sqlalchemy import text

    row = (
        conn.execute(
            text("SELECT evidence_json FROM memory_records WHERE id = :id"),
            {"id": record_id},
        )
        .mappings()
        .first()
    )
    conn.execute(
        text(
            """
            UPDATE memory_records
               SET is_deleted = TRUE,
                   updated_at = :updated_at,
                   search_text = ''
             WHERE id = :id
            """
        ),
        {"updated_at": now_iso, "id": record_id},
    )
    if row is None:
        return []
    return _decode_evidence_ref_values(row.get("evidence_json"))


def _shorten_summary_text(text: str, *, max_chars: int) -> str:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return ""
    for sep in (". ", "? ", "! "):
        if sep in normalized:
            first = normalized.split(sep, 1)[0].strip()
            if first:
                normalized = first
                break
    return normalized[:max_chars].strip()


def apply_confidence_decay(
    store: MemoryStore,
    *,
    interval_days: int,
    decay_rate: float,
    min_confidence: float,
    disuse_threshold_days: int | None = None,
    disuse_decay_multiplier: float = 1.0,
    exempt_types: list[str] | tuple[str, ...] = ("pin",),
) -> tuple[int, int]:
    now = _utc_now()
    cutoff = now - datetime.timedelta(days=max(1, int(interval_days)))
    decayed = 0
    evicted = 0
    exempt = {str(item) for item in exempt_types}

    if isinstance(store, PostgresMemoryStore):
        from sqlalchemy import text

        with store.gc_connection() as conn:
            removed_edges: list[tuple[str, list[Any]]] = []
            rows = (
                conn.execute(
                    text(
                        """
                    SELECT id, scope, type, confidence, updated_at, last_hit_at
                    FROM memory_records
                    WHERE is_deleted = FALSE
                    """
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                scope = str(row.get("scope") or "")
                record_type = str(row.get("type") or "")
                try:
                    parsed_scope = MemoryScope.parse(scope)
                except ValueError:
                    parsed_scope = None
                if (
                    parsed_scope is not None and parsed_scope.is_session
                ) or record_type in exempt:
                    continue
                updated_at = _parse_when(str(row.get("updated_at") or ""))
                if updated_at is None or updated_at >= cutoff:
                    continue
                elapsed_seconds = (now - updated_at).total_seconds()
                interval_seconds = max(1, int(interval_days)) * 86400.0
                elapsed_intervals = max(
                    1.0, elapsed_seconds / max(1.0, interval_seconds)
                )
                effective_decay = float(decay_rate) * elapsed_intervals
                if disuse_threshold_days is not None and disuse_decay_multiplier > 1.0:
                    disuse_cutoff = now - datetime.timedelta(
                        days=max(1, int(disuse_threshold_days))
                    )
                    last_hit_at = _parse_when(str(row.get("last_hit_at") or ""))
                    if last_hit_at is None or last_hit_at < disuse_cutoff:
                        effective_decay *= float(disuse_decay_multiplier)
                new_confidence = max(
                    0.0, float(row.get("confidence") or 0.0) - effective_decay
                )
                conn.execute(
                    text(
                        """
                        UPDATE memory_records
                           SET confidence = :confidence,
                               updated_at = :updated_at
                         WHERE id = :id
                        """
                    ),
                    {
                        "confidence": new_confidence,
                        "updated_at": now.isoformat(),
                        "id": str(row["id"]),
                    },
                )
                decayed += 1
                if new_confidence < float(min_confidence):
                    removed_edges.append(
                        (
                            str(row["id"]),
                            _soft_delete_record_postgres(
                                conn,
                                str(row["id"]),
                                now_iso=now.isoformat(),
                            ),
                        )
                    )
                    evicted += 1
        for record_id, ref_values in removed_edges:
            _remove_artifact_refs(store, owner_id=record_id, ref_values=ref_values)
        return decayed, evicted

    with store._connect() as conn:
        conn.execute("BEGIN")
        removed_edges: list[tuple[str, list[Any]]] = []
        try:
            rows = conn.execute(
                """
                SELECT id, scope, type, confidence, updated_at, last_hit_at
                FROM memory_records
                WHERE is_deleted = 0
                """
            ).fetchall()
            for row in rows:
                scope = str(row["scope"] or "")
                record_type = str(row["type"] or "")
                try:
                    parsed_scope = MemoryScope.parse(scope)
                except ValueError:
                    parsed_scope = None
                if (
                    parsed_scope is not None and parsed_scope.is_session
                ) or record_type in exempt:
                    continue
                updated_at = _parse_when(str(row["updated_at"] or ""))
                if updated_at is None or updated_at >= cutoff:
                    continue
                elapsed_seconds = (now - updated_at).total_seconds()
                interval_seconds = max(1, int(interval_days)) * 86400.0
                elapsed_intervals = max(
                    1.0, elapsed_seconds / max(1.0, interval_seconds)
                )
                effective_decay = float(decay_rate) * elapsed_intervals
                if disuse_threshold_days is not None and disuse_decay_multiplier > 1.0:
                    disuse_cutoff = now - datetime.timedelta(
                        days=max(1, int(disuse_threshold_days))
                    )
                    last_hit_at = _parse_when(str(row["last_hit_at"] or ""))
                    if last_hit_at is not None and last_hit_at < disuse_cutoff:
                        effective_decay *= float(disuse_decay_multiplier)
                new_confidence = max(
                    0.0, float(row["confidence"] or 0.0) - effective_decay
                )
                conn.execute(
                    "UPDATE memory_records SET confidence = ?, updated_at = ? WHERE id = ?",
                    (new_confidence, now.isoformat(), str(row["id"])),
                )
                decayed += 1
                if new_confidence < float(min_confidence):
                    removed_edges.append(
                        (
                            str(row["id"]),
                            _soft_delete_record(
                                conn,
                                str(row["id"]),
                                now_iso=now.isoformat(),
                            ),
                        )
                    )
                    evicted += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    for record_id, ref_values in removed_edges:
        _remove_artifact_refs(store, owner_id=record_id, ref_values=ref_values)
    return decayed, evicted


def compress_old_summaries(
    store: MemoryStore,
    *,
    max_age_days: int = MEMORY_GC_SUMMARY_COMPRESS_AFTER_DAYS,
    delete_age_days: int = MEMORY_GC_SUMMARY_DELETE_AFTER_DAYS,
    max_summary_chars: int = MEMORY_GC_SUMMARY_COMPRESS_MAX_CHARS,
) -> tuple[int, int]:
    now = _utc_now()
    compress_before = now - datetime.timedelta(days=max(1, int(max_age_days)))
    delete_before = now - datetime.timedelta(days=max(1, int(delete_age_days)))
    compressed = 0
    deleted = 0

    if isinstance(store, PostgresMemoryStore):
        from sqlalchemy import text

        with store.gc_connection() as conn:
            removed_edges: list[tuple[str, list[Any]]] = []
            rows = (
                conn.execute(
                    text(
                        """
                    SELECT id, scope, type, key, title, content_json, tags_json, entities_json, created_at, updated_at
                    FROM memory_records
                    WHERE is_deleted = FALSE AND type = 'session_summary'
                    """
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                created_at = _parse_when(
                    str(row.get("created_at") or "")
                ) or _parse_when(str(row.get("updated_at") or ""))
                if created_at is None:
                    continue
                record_id = str(row["id"])
                if created_at <= delete_before:
                    removed_edges.append(
                        (
                            record_id,
                            _soft_delete_record_postgres(
                                conn,
                                record_id,
                                now_iso=now.isoformat(),
                            ),
                        )
                    )
                    deleted += 1
                    continue
                if created_at > compress_before:
                    continue

                content = row.get("content_json") or {}
                if not isinstance(content, dict):
                    continue
                shortened = _shorten_summary_text(
                    str(content.get("summary_text", "") or ""),
                    max_chars=max_summary_chars,
                )
                content = dict(content)
                content["summary_text"] = shortened
                tags = list(row.get("tags_json") or [])
                entities = list(row.get("entities_json") or [])
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
                            tags=tags,
                            entities=entities,
                        ),
                        "id": record_id,
                    },
                )
                compressed += 1
        for record_id, ref_values in removed_edges:
            _remove_artifact_refs(store, owner_id=record_id, ref_values=ref_values)
        return compressed, deleted

    with store._connect() as conn:
        conn.execute("BEGIN")
        removed_edges: list[tuple[str, list[Any]]] = []
        try:
            rows = conn.execute(
                """
                SELECT id, title, content_json, created_at, updated_at
                FROM memory_records
                WHERE is_deleted = 0 AND type = 'session_summary'
                """
            ).fetchall()
            for row in rows:
                created_at = _parse_when(str(row["created_at"] or "")) or _parse_when(
                    str(row["updated_at"] or "")
                )
                if created_at is None:
                    continue
                record_id = str(row["id"])
                if created_at <= delete_before:
                    removed_edges.append(
                        (
                            record_id,
                            _soft_delete_record(
                                conn,
                                record_id,
                                now_iso=now.isoformat(),
                            ),
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
                row_fts = conn.execute(
                    """
                    SELECT scope, type, key, title, tags_text, entities_text
                    FROM memory_fts
                    WHERE id = ?
                    """,
                    (record_id,),
                ).fetchone()
                conn.execute(
                    "DELETE FROM memory_fts WHERE id = ?",
                    (record_id,),
                )
                if row_fts is not None:
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
                compressed += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    for record_id, ref_values in removed_edges:
        _remove_artifact_refs(store, owner_id=record_id, ref_values=ref_values)
    return compressed, deleted


def enforce_scope_capacity(
    store: MemoryStore,
    *,
    max_records: int,
    exempt_types: list[str] | tuple[str, ...] = ("pin",),
) -> dict[str, int]:
    exempt = {str(item) for item in exempt_types}
    evicted: dict[str, int] = {}
    now_iso = _utc_now().isoformat()

    if isinstance(store, PostgresMemoryStore):
        from sqlalchemy import text

        with store.gc_connection() as conn:
            removed_edges: list[tuple[str, list[Any]]] = []
            scopes = [
                str(row["scope"])
                for row in conn.execute(
                    text(
                        """
                        SELECT DISTINCT scope
                        FROM memory_records
                        WHERE is_deleted = FALSE
                        """
                    )
                )
                .mappings()
                .all()
            ]
            for scope in scopes:
                rows = (
                    conn.execute(
                        text(
                            """
                        SELECT id, type, confidence, updated_at, created_at
                        FROM memory_records
                        WHERE is_deleted = FALSE AND scope = :scope
                        ORDER BY confidence ASC, updated_at ASC, created_at ASC
                        """
                        ),
                        {"scope": scope},
                    )
                    .mappings()
                    .all()
                )
                if len(rows) <= int(max_records):
                    continue
                active_count = len(rows)
                removable = [
                    row for row in rows if str(row.get("type") or "") not in exempt
                ]
                while active_count > int(max_records) and removable:
                    row = removable.pop(0)
                    removed_edges.append(
                        (
                            str(row["id"]),
                            _soft_delete_record_postgres(
                                conn,
                                str(row["id"]),
                                now_iso=now_iso,
                            ),
                        )
                    )
                    evicted[scope] = evicted.get(scope, 0) + 1
                    active_count -= 1
        for record_id, ref_values in removed_edges:
            _remove_artifact_refs(store, owner_id=record_id, ref_values=ref_values)
        return evicted

    with store._connect() as conn:
        conn.execute("BEGIN")
        removed_edges: list[tuple[str, list[Any]]] = []
        try:
            scopes = [
                str(row["scope"])
                for row in conn.execute(
                    "SELECT DISTINCT scope FROM memory_records WHERE is_deleted = 0"
                ).fetchall()
            ]
            for scope in scopes:
                rows = conn.execute(
                    """
                    SELECT id, type, confidence, updated_at
                    FROM memory_records
                    WHERE is_deleted = 0 AND scope = ?
                    ORDER BY confidence ASC, updated_at ASC, created_at ASC
                    """,
                    (scope,),
                ).fetchall()
                if len(rows) <= int(max_records):
                    continue
                active_count = len(rows)
                removable = [
                    row for row in rows if str(row["type"] or "") not in exempt
                ]
                while active_count > int(max_records) and removable:
                    row = removable.pop(0)
                    removed_edges.append(
                        (
                            str(row["id"]),
                            _soft_delete_record(
                                conn,
                                str(row["id"]),
                                now_iso=now_iso,
                            ),
                        )
                    )
                    evicted[scope] = evicted.get(scope, 0) + 1
                    active_count -= 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    for record_id, ref_values in removed_edges:
        _remove_artifact_refs(store, owner_id=record_id, ref_values=ref_values)
    return evicted


def evict_stale_insights(
    store: MemoryStore,
    *,
    staleness_days: int,
) -> int:
    cutoff = _utc_now() - datetime.timedelta(days=max(1, int(staleness_days)))
    now_iso = _utc_now().isoformat()
    evicted = 0

    if isinstance(store, PostgresMemoryStore):
        from sqlalchemy import text

        with store.gc_connection() as conn:
            removed_edges: list[tuple[str, list[Any]]] = []
            rows = (
                conn.execute(
                    text(
                        """
                    SELECT id, created_at, updated_at, last_hit_at
                    FROM memory_records
                    WHERE is_deleted = FALSE AND type = 'meta_insight'
                    """
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                last_seen = (
                    _parse_when(str(row.get("last_hit_at") or ""))
                    or _parse_when(str(row.get("created_at") or ""))
                    or _parse_when(str(row.get("updated_at") or ""))
                )
                if last_seen is None or last_seen >= cutoff:
                    continue
                removed_edges.append(
                    (
                        str(row["id"]),
                        _soft_delete_record_postgres(
                            conn,
                            str(row["id"]),
                            now_iso=now_iso,
                        ),
                    )
                )
                evicted += 1
        for record_id, ref_values in removed_edges:
            _remove_artifact_refs(store, owner_id=record_id, ref_values=ref_values)
        return evicted

    with store._connect() as conn:
        conn.execute("BEGIN")
        removed_edges: list[tuple[str, list[Any]]] = []
        try:
            rows = conn.execute(
                """
                SELECT id, created_at, updated_at, last_hit_at
                FROM memory_records
                WHERE is_deleted = 0 AND type = 'meta_insight'
                """
            ).fetchall()
            for row in rows:
                last_seen = (
                    _parse_when(str(row["last_hit_at"] or ""))
                    or _parse_when(str(row["created_at"] or ""))
                    or _parse_when(str(row["updated_at"] or ""))
                )
                if last_seen is None or last_seen >= cutoff:
                    continue
                removed_edges.append(
                    (
                        str(row["id"]),
                        _soft_delete_record(
                            conn,
                            str(row["id"]),
                            now_iso=now_iso,
                        ),
                    )
                )
                evicted += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    for record_id, ref_values in removed_edges:
        _remove_artifact_refs(store, owner_id=record_id, ref_values=ref_values)
    return evicted


def run_gc(
    store: MemoryStore,
    batch_size: int = 500,
    *,
    retention_config: Any | None = None,
) -> GCResult:
    """Purge soft-deleted and expired rows from all tables."""
    result = GCResult()

    if retention_config is not None:
        decayed, _evicted_by_decay = apply_confidence_decay(
            store,
            interval_days=int(
                getattr(retention_config, "confidence_decay_interval_days", 7)
            ),
            decay_rate=float(getattr(retention_config, "confidence_decay_rate", 0.05)),
            min_confidence=float(
                getattr(retention_config, "min_confidence_eviction", 0.3)
            ),
            disuse_threshold_days=int(
                getattr(retention_config, "disuse_threshold_days", 30)
            ),
            disuse_decay_multiplier=float(
                getattr(retention_config, "disuse_decay_multiplier", 2.0)
            ),
        )
        result.decayed_records = decayed
        compressed, _deleted_summaries = compress_old_summaries(
            store,
            max_age_days=int(retention_config.summary_compression_age_days),
            delete_age_days=int(retention_config.summary_delete_age_days),
            max_summary_chars=int(retention_config.summary_compression_max_chars),
        )
        result.compressed_summaries = compressed
        capacity_evicted = enforce_scope_capacity(
            store,
            max_records=int(getattr(retention_config, "max_records_per_scope", 500)),
        )
        result.capacity_evicted_records = sum(capacity_evicted.values())
    purge_result = purge_soft_deleted(store, batch_size=batch_size)
    result.deleted_records += purge_result.deleted_records
    result.deleted_candidates += purge_result.deleted_candidates
    result.cleaned_fts_rows += purge_result.cleaned_fts_rows
    result.cleaned_entity_rows += purge_result.cleaned_entity_rows
    return result
