import datetime
from typing import Any
import uuid

from ...constants import MEMORY_CANDIDATE_STATUS_APPROVED
from ...models import MemoryCandidate, MemoryRecord, MemoryType
from ..base import CandidateListOptions
from .candidate_records import (
    _find_target_key_collision,
    _load_record,
    _retarget_superseding_record_key,
)
from .candidate_payloads import _candidate_insert_params, _patched_candidate
from ...errors import (
    InvalidArgumentError,
    NotFoundError,
    PromotionDeniedError,
    StoreWriteError,
)


def candidate_put(store: Any, candidate: MemoryCandidate) -> str:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    existing = store.candidate_get(candidate.candidate_id)
    with store._engine.begin() as conn:
        store._execute(
            """
            INSERT INTO memory_candidates (
                candidate_id, session_id, proposed_scope, type, key, title,
                content_json, tags_json, entities_json, source, confidence,
                evidence_json, meta_json, status, review_json, created_at, updated_at
            ) VALUES (
                :candidate_id, :session_id, :proposed_scope, :record_type, :key,
                :title, CAST(:content_json AS JSONB), CAST(:tags_json AS JSONB),
                CAST(:entities_json AS JSONB), :source, :confidence,
                CAST(:evidence_json AS JSONB), CAST(:meta_json AS JSONB), :status,
                CAST(:review_json AS JSONB), :created_at, :updated_at
            )
            ON CONFLICT(candidate_id) DO UPDATE SET
                session_id = EXCLUDED.session_id,
                proposed_scope = EXCLUDED.proposed_scope,
                type = EXCLUDED.type,
                key = EXCLUDED.key,
                title = EXCLUDED.title,
                content_json = EXCLUDED.content_json,
                tags_json = EXCLUDED.tags_json,
                entities_json = EXCLUDED.entities_json,
                source = EXCLUDED.source,
                confidence = EXCLUDED.confidence,
                evidence_json = EXCLUDED.evidence_json,
                meta_json = EXCLUDED.meta_json,
                status = EXCLUDED.status,
                review_json = EXCLUDED.review_json,
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at
            """,
            _candidate_insert_params(candidate, now),
            connection=conn,
        )
    if existing is not None:
        store._remove_artifact_refs(
            owner_id=candidate.candidate_id,
            ref_values=existing.evidence_refs,
        )
    store._add_artifact_refs(
        owner_id=candidate.candidate_id,
        ref_values=candidate.evidence_refs,
    )
    return candidate.candidate_id


def candidate_get(store: Any, candidate_id: str) -> MemoryCandidate | None:
    row = store._fetchone(
        "SELECT * FROM memory_candidates WHERE candidate_id = :candidate_id",
        {"candidate_id": candidate_id},
    )
    return None if row is None else store._create_candidate_from_row(row)


def candidate_delete(store: Any, candidate_id: str) -> None:
    candidate = store.candidate_get(candidate_id)
    store._execute(
        "DELETE FROM memory_candidates WHERE candidate_id = :candidate_id",
        {"candidate_id": candidate_id},
    )
    if candidate is not None:
        store._remove_artifact_refs(
            owner_id=candidate_id,
            ref_values=candidate.evidence_refs,
        )


def candidate_list(store: Any, options: CandidateListOptions) -> list[MemoryCandidate]:
    query = ["SELECT * FROM memory_candidates WHERE 1 = 1"]
    params: dict[str, Any] = {}
    if options.session_id is not None:
        query.append("AND session_id = :session_id")
        params["session_id"] = options.session_id
    if options.proposed_scope is not None:
        query.append("AND proposed_scope = :proposed_scope")
        params["proposed_scope"] = options.proposed_scope
    if options.status:
        query.append("AND status = :status")
        params["status"] = options.status
    query.append("ORDER BY created_at ASC")
    if options.limit is not None:
        query.append("LIMIT :limit")
        params["limit"] = int(options.limit)
    return [
        store._create_candidate_from_row(row)
        for row in store._fetchall(" ".join(query), params)
    ]


def candidate_update(
    store: Any,
    candidate_id: str,
    patch: dict[str, Any],
) -> MemoryCandidate:
    current = store.candidate_get(candidate_id)
    if current is None:
        raise NotFoundError(f"Candidate {candidate_id} not found")
    store.candidate_put(_patched_candidate(current, patch))
    refreshed = store.candidate_get(candidate_id)
    if refreshed is None:
        raise RuntimeError(
            f"Candidate {candidate_id} missing after update"
        )  # allow-bare-raise: internal invariant — post-write read-back guard
    return refreshed


def promote_candidate(store: Any, candidate_id: str, target_scope: str) -> MemoryRecord:
    promoted_candidate: MemoryCandidate | None = None
    superseded_owner_id: str | None = None
    superseded_ref_values: list[Any] = []
    new_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with store._lock:
        with store._engine.begin() as conn:
            row = store._fetchone(
                "SELECT * FROM memory_candidates WHERE candidate_id = :candidate_id",
                {"candidate_id": candidate_id},
                connection=conn,
            )
            if row is None:
                raise NotFoundError(f"Candidate {candidate_id} not found")
            if row["status"] != MEMORY_CANDIDATE_STATUS_APPROVED:
                raise PromotionDeniedError(
                    f"Candidate {candidate_id} is not approved for promotion",
                    details={"candidate_id": candidate_id},
                )
            promoted_candidate = store._create_candidate_from_row(row)
            existing = _find_target_key_collision(
                store, conn, promoted_candidate, target_scope
            )
            if existing is not None:
                superseded_owner_id = str(existing["id"])
                superseded_ref_values = store._decode_evidence_ref_values(
                    existing.get("evidence_json")
                )
            store._insert_record(
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
            store._upsert_entities(
                conn,
                record_id=new_id,
                scope=target_scope,
                record_type=promoted_candidate.type,
                entities=list(promoted_candidate.entities),
                created_at=now,
            )
            store._execute(
                """
                UPDATE memory_candidates
                   SET status = 'promoted', updated_at = :updated_at
                 WHERE candidate_id = :candidate_id
                """,
                {"updated_at": now, "candidate_id": candidate_id},
                connection=conn,
            )
    result = _load_record(
        store, new_id, missing_error=NotFoundError(f"record not found: {new_id}")
    )
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


def history(store: Any, scope: str, type: MemoryType, key: str) -> list[MemoryRecord]:
    rows = store._fetchall(
        """
        SELECT *
          FROM memory_records
         WHERE scope = :scope
           AND type = :record_type
           AND key = :key
         ORDER BY created_at DESC
        """,
        {"scope": scope, "record_type": type, "key": key},
    )
    return [store._create_record_from_row(row) for row in rows]


def supersede_by_contradiction(
    store: Any, old_record_id: str, new_record_id: str, reason: str = ""
) -> MemoryRecord:
    if old_record_id == new_record_id:
        raise InvalidArgumentError("old and new records must differ")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    old_record: MemoryRecord | None = None
    with store._lock:
        with store._engine.begin() as conn:
            old_row = store._get_required_record(conn, old_record_id)
            new_row = store._get_required_record(conn, new_record_id)
            old_record = store._create_record_from_row(old_row)
            store._apply_supersession(
                conn,
                old_record_id=old_record_id,
                new_record_id=new_record_id,
                now_iso=now,
                valid_to_iso=str(new_row.get("created_at") or now),
                reason=reason,
            )
            if old_row.get("key") and old_row.get("key") != new_row.get("key"):
                _retarget_superseding_record_key(store, conn, old_row, new_row, now)
    result = _load_record(
        store,
        new_record_id,
        missing_error=StoreWriteError("failed to supersede memory record"),
    )
    if old_record is not None:
        store._remove_artifact_refs(
            owner_id=old_record_id,
            ref_values=old_record.evidence_refs,
        )
    store._add_artifact_refs(owner_id=result.id, ref_values=result.evidence_refs)
    return result


__all__ = [
    "candidate_delete",
    "candidate_get",
    "candidate_list",
    "candidate_put",
    "candidate_update",
    "history",
    "promote_candidate",
    "supersede_by_contradiction",
]
