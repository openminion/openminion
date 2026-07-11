import json
import sqlite3
from typing import Any

from openminion.modules.memory.models import (
    ArtifactRef,
    CandidateReview,
    MemoryCandidate,
    MemoryNamespace,
    MemoryRecord,
    MemoryRelation,
    MemoryTierTransition,
)


def _decode_sqlite_evidence_ref_values(raw_json: str | None) -> list[Any]:
    payload = json.loads(str(raw_json or "[]"))
    return payload if isinstance(payload, list) else []


def _artifact_refs_from_sqlite_row(row: sqlite3.Row) -> list[ArtifactRef]:
    return [ArtifactRef(**item) for item in json.loads(row["evidence_json"])]


def _create_sqlite_record_from_row(row: sqlite3.Row) -> MemoryRecord:
    row_keys = set(row.keys())
    deleted_at = row["deleted_at"] if "deleted_at" in row_keys else None
    deleted_reason = row["deleted_reason"] if "deleted_reason" in row_keys else None
    event_time = row["event_time"] if "event_time" in row_keys else None
    valid_to = row["valid_to"] if "valid_to" in row_keys else None
    goal_id = row["goal_id"] if "goal_id" in row_keys else None
    created_at = row["created_at"]
    namespace_json = row["namespace_json"] if "namespace_json" in row_keys else None
    namespace_payload = json.loads(namespace_json) if namespace_json else None
    return MemoryRecord(
        id=row["id"],
        scope=row["scope"],
        namespace=(
            MemoryNamespace.from_dict(namespace_payload)
            if isinstance(namespace_payload, dict) and namespace_payload
            else None
        ),
        type=row["type"],
        content=json.loads(row["content_json"]),
        created_at=created_at,
        updated_at=row["updated_at"],
        key=row["key"],
        title=row["title"],
        tags=json.loads(row["tags_json"]),
        entities=json.loads(row["entities_json"]),
        source=row["source"],
        confidence=row["confidence"],
        evidence_refs=_artifact_refs_from_sqlite_row(row),
        expires_at=row["expires_at"],
        meta=json.loads(row["meta_json"]),
        last_hit_at=row["last_hit_at"],
        event_time=event_time or created_at,
        valid_to=valid_to,
        goal_id=goal_id,
        tier=row["tier"] if row["tier"] else "working",
        access_count=int(row["access_count"] or 0),
        supersedes_id=row["supersedes_id"],
        superseded_by_id=row["superseded_by_id"],
        supersession_reason=row["supersession_reason"],
        is_deleted=bool(row["is_deleted"]),
        deleted_at=deleted_at,
        deleted_reason=deleted_reason,
    )


def _create_sqlite_relation_from_row(row: sqlite3.Row) -> MemoryRelation:
    return MemoryRelation(
        relation_id=row["relation_id"],
        source_record_id=row["source_record_id"],
        target_record_id=row["target_record_id"],
        relation_type=row["relation_type"],
        created_at=row["created_at"],
        meta=json.loads(row["meta_json"] or "{}"),
    )


def _create_sqlite_candidate_from_row(row: sqlite3.Row) -> MemoryCandidate:
    content = json.loads(row["content_json"])
    tags = json.loads(row["tags_json"])
    entities = json.loads(row["entities_json"])
    review_json = row["review_json"]
    review = CandidateReview(**json.loads(review_json)) if review_json else None
    return MemoryCandidate(
        candidate_id=row["candidate_id"],
        session_id=row["session_id"],
        proposed_scope=row["proposed_scope"],
        type=row["type"],
        key=row["key"],
        title=row["title"],
        content=content,
        tags=tags,
        entities=entities,
        source=row["source"],
        confidence=row["confidence"],
        evidence_refs=_artifact_refs_from_sqlite_row(row),
        status=row["status"],
        review=review,
        meta=json.loads(row["meta_json"] or "{}"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _create_sqlite_tier_transition_from_row(row: sqlite3.Row) -> MemoryTierTransition:
    return MemoryTierTransition(
        transition_id=row["transition_id"],
        record_id=row["record_id"],
        scope=row["scope"],
        record_type=row["record_type"],
        from_tier=row["from_tier"],
        to_tier=row["to_tier"],
        transition_reason=row["transition_reason"],
        transition_at=row["transition_at"],
        access_count=int(row["access_count"] or 0),
        meta=json.loads(row["meta_json"] or "{}"),
    )


__all__ = [
    "_create_sqlite_candidate_from_row",
    "_create_sqlite_record_from_row",
    "_create_sqlite_relation_from_row",
    "_create_sqlite_tier_transition_from_row",
    "_decode_sqlite_evidence_ref_values",
]
