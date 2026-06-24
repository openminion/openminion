"""SQLite write workflows for memory storage."""

from __future__ import annotations

import datetime
import json
import sqlite3
import uuid
from typing import TYPE_CHECKING, Any

from ...constants import MEMORY_CANDIDATE_STATUS_APPROVED
from ...errors import (
    InvalidArgumentError,
    NotFoundError,
    PromotionDeniedError,
    StoreWriteError,
)
from ...models import MemoryCandidate, MemoryRecord, MemoryType

if TYPE_CHECKING:
    from .store import SQLiteMemoryStore


def _sqlite_insert_record(
    conn: sqlite3.Connection,
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
    evidence_refs: list[Any],
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
    conn.execute(
        """
        INSERT INTO memory_records (
            id, scope, type, key, title, content_json, tags_json, entities_json,
            source, confidence, evidence_json, meta_json, last_hit_at, event_time, valid_to, tier, access_count, expires_at, created_at, updated_at,
            supersedes_id, superseded_by_id, supersession_reason, is_deleted
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            scope,
            record_type,
            key,
            title,
            json.dumps(content),
            json.dumps(tags),
            json.dumps(entities),
            source,
            confidence,
            json.dumps([vars(item) for item in evidence_refs]),
            json.dumps(meta),
            last_hit_at,
            event_time,
            valid_to,
            tier,
            int(access_count),
            expires_at,
            created_at,
            updated_at,
            supersedes_id,
            superseded_by_id,
            supersession_reason,
            int(is_deleted),
        ),
    )


def _sqlite_insert_fts_row(
    conn: sqlite3.Connection,
    *,
    record_id: str,
    scope: str,
    record_type: MemoryType,
    key: str | None,
    title: str | None,
    content: dict[str, Any] | str,
    tags: list[str],
    entities: list[str],
) -> None:
    conn.execute(
        """
        INSERT INTO memory_fts (id, scope, type, key, title, content_text, tags_text, entities_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            scope,
            record_type,
            key,
            title,
            json.dumps(content) if isinstance(content, dict) else content,
            " ".join(tags),
            " ".join(entities),
        ),
    )


def _sqlite_upsert_entities(
    conn: sqlite3.Connection,
    *,
    record_id: str,
    scope: str,
    record_type: MemoryType,
    entities: list[str],
    created_at: str,
) -> None:
    for entity in entities:
        conn.execute(
            """
            INSERT OR IGNORE INTO memory_entities (entity, record_id, scope, type, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (entity, record_id, scope, record_type, created_at),
        )


def _sqlite_upsert_payload(
    row: sqlite3.Row | None,
    record_patch: dict[str, Any],
) -> dict[str, Any]:
    if row is None:
        return {
            "content": record_patch.get("content", {}),
            "tags": record_patch.get("tags", []),
            "entities": record_patch.get("entities", []),
            "source": record_patch.get("source", "agent_inferred"),
            "confidence": record_patch.get("confidence", 0.5),
            "evidence_refs": record_patch.get("evidence_refs", []),
            "meta": record_patch.get("meta", {}),
            "expires_at": record_patch.get("expires_at"),
            "title": record_patch.get("title"),
        }

    content = json.loads(row["content_json"])
    patch_content = record_patch.get("content", {})
    if isinstance(content, dict) and isinstance(patch_content, dict):
        content.update(patch_content)
    else:
        content = record_patch.get("content", content)

    evidence = record_patch.get("evidence_refs", [])
    evidence_refs = evidence if evidence else json.loads(row["evidence_json"])

    return {
        "content": content,
        "tags": record_patch.get("tags", json.loads(row["tags_json"])),
        "entities": record_patch.get("entities", json.loads(row["entities_json"])),
        "source": record_patch.get("source", row["source"]),
        "confidence": record_patch.get("confidence", row["confidence"]),
        "evidence_refs": evidence_refs,
        "meta": record_patch.get("meta", json.loads(row["meta_json"])),
        "expires_at": record_patch.get("expires_at", row["expires_at"]),
        "title": record_patch.get("title", row["title"]),
    }


def _sqlite_find_target_key_collision(
    conn: sqlite3.Connection,
    candidate: MemoryCandidate,
    target_scope: str,
) -> sqlite3.Row | None:
    if not candidate.key:
        return None
    return conn.execute(
        """
        SELECT id, evidence_json
        FROM memory_records
        WHERE scope = ? AND type = ? AND key = ? AND is_deleted = 0
          AND superseded_by_id IS NULL
        """,
        (target_scope, candidate.type, candidate.key),
    ).fetchone()


def _load_sqlite_record(store: SQLiteMemoryStore, record_id: str) -> MemoryRecord:
    with store._connect() as conn:
        row = conn.execute(
            "SELECT * FROM memory_records WHERE id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(
                f"record not found: {record_id}", details={"record_id": record_id}
            )
        return store._create_record_from_row(row)


def upsert(
    store: SQLiteMemoryStore,
    scope: str,
    record_type: MemoryType,
    key: str,
    record_patch: dict[str, Any],
) -> MemoryRecord:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    removed_owner_id: str | None = None
    removed_ref_values: list[Any] = []
    result_id: str | None = None
    with store._write_lock:
        with store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT id, title, content_json, tags_json, entities_json, source,
                           confidence, evidence_json, meta_json, expires_at
                    FROM memory_records
                    WHERE scope = ? AND type = ? AND key = ? AND is_deleted = 0
                      AND superseded_by_id IS NULL
                    """,
                    (scope, record_type, key),
                ).fetchone()
                supersedes_id = str(row["id"]) if row else None
                if row is not None:
                    removed_owner_id = supersedes_id
                    removed_ref_values = store._decode_evidence_ref_values(
                        row["evidence_json"]
                    )
                payload = _sqlite_upsert_payload(row, record_patch)
                result_id = uuid.uuid4().hex
                _sqlite_insert_record(
                    conn,
                    record_id=result_id,
                    scope=scope,
                    record_type=record_type,
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
                    tier="working",
                    access_count=0,
                    expires_at=payload["expires_at"],
                    created_at=now,
                    updated_at=now,
                    supersedes_id=supersedes_id,
                    superseded_by_id=None,
                    supersession_reason=None,
                    is_deleted=row is not None,
                )
                if supersedes_id is not None:
                    store._apply_supersession(
                        conn,
                        old_record_id=supersedes_id,
                        new_record_id=result_id,
                        now_iso=now,
                        valid_to_iso=now,
                        reason="keyed_upsert",
                    )
                _sqlite_insert_fts_row(
                    conn,
                    record_id=result_id,
                    scope=scope,
                    record_type=record_type,
                    key=key,
                    title=payload["title"],
                    content=payload["content"],
                    tags=payload["tags"],
                    entities=payload["entities"],
                )
                _sqlite_upsert_entities(
                    conn,
                    record_id=result_id,
                    scope=scope,
                    record_type=record_type,
                    entities=payload["entities"],
                    created_at=now,
                )
                conn.execute("COMMIT")
            except Exception:  # noqa: BLE001 — transactional boundary: roll back any sqlite or in-block Python error and re-raise
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    if result_id is None:
        raise StoreWriteError("failed to upsert memory record")
    result = _load_sqlite_record(store, result_id)
    if removed_owner_id is not None:
        store._remove_artifact_refs(
            owner_id=removed_owner_id,
            ref_values=removed_ref_values,
        )
    if not result.is_deleted:
        store._add_artifact_refs(owner_id=result.id, ref_values=result.evidence_refs)
    return result


def promote_candidate(
    store: SQLiteMemoryStore,
    candidate_id: str,
    target_scope: str,
) -> MemoryRecord:
    promoted_candidate: MemoryCandidate | None = None
    superseded_owner_id: str | None = None
    superseded_ref_values: list[Any] = []
    new_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with store._write_lock:
        with store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM memory_candidates WHERE candidate_id = ?",
                    (candidate_id,),
                ).fetchone()
                if row is None:
                    raise NotFoundError(
                        f"Candidate {candidate_id} not found",
                        details={"candidate_id": candidate_id},
                    )
                if row["status"] != MEMORY_CANDIDATE_STATUS_APPROVED:
                    raise PromotionDeniedError(
                        f"Candidate {candidate_id} is not approved for promotion",
                        details={"candidate_id": candidate_id},
                    )
                promoted_candidate = store._create_candidate_from_row(row)
                existing = _sqlite_find_target_key_collision(
                    conn,
                    promoted_candidate,
                    target_scope,
                )
                if existing is not None:
                    superseded_owner_id = str(existing["id"])
                    superseded_ref_values = store._decode_evidence_ref_values(
                        existing["evidence_json"]
                    )
                _sqlite_insert_record(
                    conn,
                    record_id=new_id,
                    scope=target_scope,
                    record_type=promoted_candidate.type,
                    key=promoted_candidate.key,
                    title=promoted_candidate.title,
                    content=promoted_candidate.content,
                    tags=list(promoted_candidate.tags),
                    entities=list(promoted_candidate.entities),
                    source=promoted_candidate.source,
                    confidence=promoted_candidate.confidence,
                    evidence_refs=list(promoted_candidate.evidence_refs),
                    meta={},
                    last_hit_at=None,
                    event_time=now,
                    valid_to=None,
                    tier="working",
                    access_count=0,
                    expires_at=None,
                    created_at=now,
                    updated_at=now,
                    supersedes_id=superseded_owner_id,
                    superseded_by_id=None,
                    supersession_reason=None,
                    is_deleted=existing is not None,
                )
                if superseded_owner_id is not None:
                    store._apply_supersession(
                        conn,
                        old_record_id=superseded_owner_id,
                        new_record_id=new_id,
                        now_iso=now,
                        valid_to_iso=now,
                        reason="keyed_upsert",
                    )
                _sqlite_insert_fts_row(
                    conn,
                    record_id=new_id,
                    scope=target_scope,
                    record_type=promoted_candidate.type,
                    key=promoted_candidate.key,
                    title=promoted_candidate.title,
                    content=promoted_candidate.content,
                    tags=list(promoted_candidate.tags),
                    entities=list(promoted_candidate.entities),
                )
                _sqlite_upsert_entities(
                    conn,
                    record_id=new_id,
                    scope=target_scope,
                    record_type=promoted_candidate.type,
                    entities=list(promoted_candidate.entities),
                    created_at=now,
                )
                conn.execute(
                    """
                    UPDATE memory_candidates
                    SET status = 'promoted', updated_at = ?
                    WHERE candidate_id = ?
                    """,
                    (now, candidate_id),
                )
                conn.execute("COMMIT")
            except Exception:  # noqa: BLE001 — transactional boundary: roll back any sqlite or in-block Python error and re-raise
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    result = _load_sqlite_record(store, new_id)
    store._add_artifact_refs(owner_id=result.id, ref_values=result.evidence_refs)
    if superseded_owner_id is not None:
        store._remove_artifact_refs(
            owner_id=superseded_owner_id,
            ref_values=superseded_ref_values,
        )
    if promoted_candidate is not None:
        store._remove_artifact_refs(
            owner_id=promoted_candidate.candidate_id,
            ref_values=promoted_candidate.evidence_refs,
        )
    return result


def history(
    store: SQLiteMemoryStore,
    scope: str,
    record_type: MemoryType,
    key: str,
) -> list[MemoryRecord]:
    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM memory_records
            WHERE scope = ? AND type = ? AND key = ?
            ORDER BY created_at DESC
            """,
            (scope, record_type, key),
        ).fetchall()
        return [store._create_record_from_row(row) for row in rows]


def supersede_by_contradiction(
    store: SQLiteMemoryStore,
    old_record_id: str,
    new_record_id: str,
    reason: str = "",
) -> MemoryRecord:
    if old_record_id == new_record_id:
        raise InvalidArgumentError("old and new records must differ")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    old_record: MemoryRecord | None = None
    result: MemoryRecord | None = None
    with store._write_lock, store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old_row = store._get_required_record(conn, old_record_id)
            new_row = store._get_required_record(conn, new_record_id)
            old_record = store._create_record_from_row(old_row)
            store._apply_supersession(
                conn,
                old_record_id=old_record_id,
                new_record_id=new_record_id,
                now_iso=now,
                valid_to_iso=str(new_row["created_at"] or now),
                reason=reason,
            )
            if old_row["key"] and old_row["key"] != new_row["key"]:
                conn.execute(
                    "UPDATE memory_records SET key = ?, updated_at = ? WHERE id = ?",
                    (old_row["key"], now, new_record_id),
                )
                conn.execute(
                    "UPDATE memory_fts SET key = ? WHERE id = ?",
                    (old_row["key"], new_record_id),
                )
            row = store._get_required_record(conn, new_record_id)
            conn.execute("COMMIT")
            result = store._create_record_from_row(row)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    if result is None:
        raise StoreWriteError("failed to supersede memory record")
    if old_record is not None:
        store._remove_artifact_refs(
            owner_id=old_record_id,
            ref_values=old_record.evidence_refs,
        )
    store._add_artifact_refs(owner_id=result.id, ref_values=result.evidence_refs)
    return result


def apply_outcome_feedback(
    store: SQLiteMemoryStore,
    record_ids: list[str],
    *,
    outcome: str,
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
    now_iso = str(observed_at or "").strip()
    if not now_iso:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    normalized_command_id = str(command_id or "").strip()
    feedback_delta_value = float(feedback_delta)

    with store._write_lock, store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for record_id in normalized_ids:
                try:
                    row = store._get_required_record(conn, record_id)
                except NotFoundError:
                    continue
                if bool(row["is_deleted"]) or row["superseded_by_id"]:
                    continue
                try:
                    meta = json.loads(str(row["meta_json"] or "{}"))
                except json.JSONDecodeError:
                    meta = {}
                if not isinstance(meta, dict):
                    meta = {}
                try:
                    existing_feedback = store._clamp01(
                        float(meta.get("feedback_score", 0.0) or 0.0)
                    )
                except (TypeError, ValueError):
                    existing_feedback = 0.0
                meta["feedback_score"] = store._clamp01(
                    existing_feedback + feedback_delta_value
                )
                success_count = int(meta.get("outcome_success_count", 0) or 0)
                failure_count = int(meta.get("outcome_failure_count", 0) or 0)
                if outcome == "success":
                    meta["outcome_success_count"] = success_count + 1
                    meta.setdefault("outcome_failure_count", failure_count)
                else:
                    meta["outcome_failure_count"] = failure_count + 1
                    meta.setdefault("outcome_success_count", success_count)
                meta["last_outcome_at"] = now_iso
                meta["last_outcome_status"] = outcome
                meta["last_outcome_command_id"] = normalized_command_id
                conn.execute(
                    """
                        UPDATE memory_records
                        SET meta_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                    (
                        json.dumps(meta, sort_keys=True),
                        now_iso,
                        record_id,
                    ),
                )
                updated += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return updated


__all__ = [
    "apply_outcome_feedback",
    "history",
    "promote_candidate",
    "supersede_by_contradiction",
    "upsert",
]
