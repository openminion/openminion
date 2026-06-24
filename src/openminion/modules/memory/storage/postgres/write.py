from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any, Literal
import uuid

from ...models import ArtifactRef, MemoryRecord, MemoryType
from .sql import _build_search_text, _json_dumps
from .write_payloads import _feedback_update_values, _upsert_payload
from ...errors import NotFoundError, StoreWriteError

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _get_required_record(
    store: Any,
    connection: Connection,
    record_id: str,
) -> dict[str, Any]:
    row = store._fetchone(
        "SELECT * FROM memory_records WHERE id = :id",
        {"id": record_id},
        connection=connection,
    )
    if row is None:
        raise NotFoundError(f"record not found: {record_id}")
    return row


def _apply_supersession(
    store: Any,
    connection: Connection,
    *,
    old_record_id: str,
    new_record_id: str,
    now_iso: str,
    valid_to_iso: str,
    reason: str = "",
) -> None:
    store._execute(
        """
        UPDATE memory_records SET superseded_by_id = :new_record_id,
               supersession_reason = :reason, valid_to = :valid_to,
               is_deleted = TRUE, updated_at = :now_iso
         WHERE id = :old_record_id
        """,
        {
            "new_record_id": new_record_id,
            "reason": reason or None,
            "valid_to": valid_to_iso,
            "now_iso": now_iso,
            "old_record_id": old_record_id,
        },
        connection=connection,
    )
    store._execute(
        """
        UPDATE memory_records SET supersedes_id = :old_record_id,
               superseded_by_id = NULL, supersession_reason = NULL,
               is_deleted = FALSE, updated_at = :now_iso
         WHERE id = :new_record_id
        """,
        {
            "old_record_id": old_record_id,
            "new_record_id": new_record_id,
            "now_iso": now_iso,
        },
        connection=connection,
    )


def _upsert_entities(
    store: Any,
    connection: Connection,
    *,
    record_id: str,
    scope: str,
    record_type: MemoryType,
    entities: list[str],
    created_at: str,
) -> None:
    for entity in entities:
        normalized = str(entity or "").strip()
        if not normalized:
            continue
        store._execute(
            """
            INSERT INTO memory_entities(entity, record_id, scope, type, created_at)
            VALUES (:entity, :record_id, :scope, :record_type, :created_at)
            ON CONFLICT(entity, record_id) DO NOTHING
            """,
            {
                "entity": normalized,
                "record_id": record_id,
                "scope": scope,
                "record_type": record_type,
                "created_at": created_at,
            },
            connection=connection,
        )


def _insert_record(
    store: Any,
    connection: Connection,
    *,
    record_id: str,
    scope: str,
    record_type: MemoryType,
    key: str | None,
    title: str | None,
    content: dict[str, Any] | str,
    tags: list[str],
    entities: list[str],
    source: str,
    confidence: float,
    evidence_refs: list[ArtifactRef],
    meta: dict[str, Any],
    last_hit_at: str | None,
    event_time: str | None,
    valid_to: str | None,
    tier: str,
    access_count: int,
    expires_at: str | None,
    created_at: str,
    updated_at: str,
    supersedes_id: str | None,
    superseded_by_id: str | None,
    supersession_reason: str | None,
    is_deleted: bool,
) -> None:
    store._execute(
        """
        INSERT INTO memory_records (
            id, scope, type, key, title, content_json, tags_json, entities_json, source,
            confidence, evidence_json, meta_json, last_hit_at, event_time, valid_to, tier, access_count, expires_at, created_at,
            updated_at, supersedes_id, superseded_by_id, supersession_reason,
            is_deleted, search_text
        ) VALUES (
            :id, :scope, :record_type, :key, :title, CAST(:content_json AS JSONB),
            CAST(:tags_json AS JSONB), CAST(:entities_json AS JSONB), :source,
            :confidence, CAST(:evidence_json AS JSONB), CAST(:meta_json AS JSONB),
            :last_hit_at, :event_time, :valid_to, :tier, :access_count, :expires_at, :created_at, :updated_at, :supersedes_id,
            :superseded_by_id, :supersession_reason, :is_deleted, :search_text
        )
        """,
        {
            "id": record_id,
            "scope": scope,
            "record_type": record_type,
            "key": key,
            "title": title,
            "content_json": _json_dumps(content),
            "tags_json": _json_dumps(tags),
            "entities_json": _json_dumps(entities),
            "source": source,
            "confidence": confidence,
            "evidence_json": _json_dumps([vars(item) for item in evidence_refs]),
            "meta_json": _json_dumps(meta),
            "last_hit_at": last_hit_at,
            "event_time": event_time,
            "valid_to": valid_to,
            "tier": tier,
            "access_count": int(access_count),
            "expires_at": expires_at,
            "created_at": created_at,
            "updated_at": updated_at,
            "supersedes_id": supersedes_id,
            "superseded_by_id": superseded_by_id,
            "supersession_reason": supersession_reason,
            "is_deleted": bool(is_deleted),
            "search_text": _build_search_text(
                scope=scope,
                record_type=record_type,
                key=key,
                title=title,
                content=content,
                tags=tags,
                entities=entities,
            ),
        },
        connection=connection,
    )


def put(store: Any, record: MemoryRecord) -> str:
    with store._lock:
        with store._engine.begin() as conn:
            store._insert_record(
                conn,
                record_id=record.id,
                scope=record.scope,
                record_type=record.type,
                key=record.key,
                title=record.title,
                content=record.content,
                tags=list(record.tags),
                entities=list(record.entities),
                source=record.source,
                confidence=record.confidence,
                evidence_refs=list(record.evidence_refs),
                meta=dict(record.meta),
                last_hit_at=record.last_hit_at,
                event_time=record.event_time,
                valid_to=record.valid_to,
                tier=record.tier,
                access_count=int(record.access_count),
                expires_at=record.expires_at,
                created_at=record.created_at,
                updated_at=record.updated_at,
                supersedes_id=record.supersedes_id,
                superseded_by_id=record.superseded_by_id,
                supersession_reason=record.supersession_reason,
                is_deleted=record.is_deleted,
            )
            store._upsert_entities(
                conn,
                record_id=record.id,
                scope=record.scope,
                record_type=record.type,
                entities=list(record.entities),
                created_at=record.created_at,
            )
    if not record.is_deleted:
        store._add_artifact_refs(owner_id=record.id, ref_values=record.evidence_refs)
    return record.id


def upsert(
    store: Any,
    scope: str,
    type: MemoryType,
    key: str,
    record_patch: dict[str, Any],
) -> MemoryRecord:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    removed_owner_id: str | None = None
    removed_ref_values: list[Any] = []
    result_id = uuid.uuid4().hex
    with store._lock:
        with store._engine.begin() as conn:
            row = store._fetchone(
                """
                SELECT *
                  FROM memory_records
                 WHERE scope = :scope
                   AND type = :record_type
                   AND key = :key
                   AND is_deleted = FALSE
                   AND superseded_by_id IS NULL
                """,
                {"scope": scope, "record_type": type, "key": key},
                connection=conn,
            )
            supersedes_id = str(row["id"]) if row else None
            payload = _upsert_payload(store, row, record_patch)
            if row:
                removed_owner_id = supersedes_id
                removed_ref_values = store._decode_evidence_ref_values(
                    row.get("evidence_json")
                )
            store._insert_record(
                conn,
                record_id=result_id,
                scope=scope,
                record_type=type,
                key=key,
                title=payload["title"],
                content=payload["content"],
                tags=payload["tags"],
                entities=payload["entities"],
                source=payload["source"],
                confidence=payload["confidence"],
                evidence_refs=payload["evidence_refs"],
                meta=payload["meta"],
                last_hit_at=None,
                event_time=now,
                valid_to=None,
                tier=str(row.get("tier") or "working") if row else "working",
                access_count=int(row.get("access_count") or 0) if row else 0,
                expires_at=payload["expires_at"],
                created_at=now,
                updated_at=now,
                supersedes_id=supersedes_id,
                superseded_by_id=None,
                supersession_reason=None,
                is_deleted=bool(row),
            )
            if row:
                store._apply_supersession(
                    conn,
                    old_record_id=supersedes_id or "",
                    new_record_id=result_id,
                    now_iso=now,
                    valid_to_iso=now,
                    reason="keyed_upsert",
                )
            store._upsert_entities(
                conn,
                record_id=result_id,
                scope=scope,
                record_type=type,
                entities=payload["entities"],
                created_at=now,
            )
    row = store._fetchone(
        "SELECT * FROM memory_records WHERE id = :id", {"id": result_id}
    )
    if row is None:
        raise StoreWriteError("failed to upsert memory record")
    result = store._create_record_from_row(row)
    if removed_owner_id is not None:
        store._remove_artifact_refs(
            owner_id=removed_owner_id,
            ref_values=removed_ref_values,
        )
    if not result.is_deleted:
        store._add_artifact_refs(owner_id=result.id, ref_values=result.evidence_refs)
    return result


def delete(
    store: Any,
    record_id: str,
    *,
    reason: str | None = None,
    deleted_at: str | None = None,
) -> None:
    """Postgres-side soft-delete with optional audit metadata."""

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    existing = store.get(record_id)
    updates = ["is_deleted = TRUE", "updated_at = :updated_at"]
    params: dict[str, Any] = {"updated_at": now, "id": record_id}
    if reason is not None:
        updates.extend(["deleted_at = :deleted_at", "deleted_reason = :deleted_reason"])
        params["deleted_at"] = deleted_at if deleted_at is not None else now
        params["deleted_reason"] = reason
    store._execute(
        f"UPDATE memory_records SET {', '.join(updates)} WHERE id = :id",
        params,
    )
    if existing is not None:
        store._remove_artifact_refs(
            owner_id=record_id, ref_values=existing.evidence_refs
        )


def invalidate(
    store: Any,
    record_id: str,
    *,
    valid_to: str,
    reason: str,
) -> MemoryRecord:
    del reason
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with store._lock:
        with store._engine.begin() as conn:
            row = store._get_required_record(conn, record_id)
            store._execute(
                """
                UPDATE memory_records
                   SET valid_to = :valid_to,
                       updated_at = :updated_at
                 WHERE id = :id
                """,
                {
                    "valid_to": valid_to,
                    "updated_at": now,
                    "id": record_id,
                },
                connection=conn,
            )
            updated = dict(row)
            updated["valid_to"] = valid_to
            updated["updated_at"] = now
    return store._create_record_from_row(updated)


def tombstone(store: Any, scope: str, type: MemoryType, key: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rows = store._fetchall(
        """
        SELECT * FROM memory_records
         WHERE scope = :scope AND type = :record_type AND key = :key
           AND is_deleted = FALSE AND superseded_by_id IS NULL
        """,
        {"scope": scope, "record_type": type, "key": key},
    )
    store._execute(
        """
        UPDATE memory_records SET is_deleted = TRUE, updated_at = :updated_at
         WHERE scope = :scope AND type = :record_type AND key = :key
           AND is_deleted = FALSE AND superseded_by_id IS NULL
        """,
        {"updated_at": now, "scope": scope, "record_type": type, "key": key},
    )
    for row in rows:
        record = store._create_record_from_row(row)
        store._remove_artifact_refs(owner_id=record.id, ref_values=record.evidence_refs)


def apply_outcome_feedback(
    store: Any,
    record_ids: list[str],
    *,
    outcome: Literal["success", "failed", "timeout"],
    command_id: str,
    observed_at: str,
    feedback_delta: float,
) -> int:
    normalized_ids: list[str] = []
    seen: set[str] = set()
    for record_id in record_ids:
        normalized = str(record_id or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_ids.append(normalized)
    if not normalized_ids:
        return 0
    updated = 0
    with store._lock:
        with store._engine.begin() as conn:
            for record_id in normalized_ids:
                try:
                    row = store._get_required_record(conn, record_id)
                except ValueError:
                    continue
                if bool(row.get("is_deleted")) or row.get("superseded_by_id"):
                    continue
                feedback_values = _feedback_update_values(
                    row,
                    outcome=outcome,
                    command_id=command_id,
                    observed_at=observed_at,
                    feedback_delta=feedback_delta,
                )
                store._execute(
                    """
                    UPDATE memory_records SET meta_json = CAST(:meta_json AS JSONB),
                           updated_at = :updated_at
                     WHERE id = :id
                    """,
                    {
                        "meta_json": _json_dumps(feedback_values["meta"]),
                        "updated_at": feedback_values["updated_at"],
                        "id": record_id,
                    },
                    connection=conn,
                )
                updated += 1
    return updated


__all__ = [
    "_apply_supersession",
    "_get_required_record",
    "_insert_record",
    "_upsert_entities",
    "apply_outcome_feedback",
    "delete",
    "put",
    "tombstone",
    "upsert",
]
