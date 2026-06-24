from typing import Any

from openminion.modules.memory.models import (
    ArtifactRef,
    CandidateReview,
    MemoryCandidate,
    MemoryRecord,
    MemoryTierTransition,
)

from .sql import _json_loads


def _decode_evidence_ref_values(raw_payload: Any) -> list[Any]:
    payload = _json_loads(raw_payload, [])
    return payload if isinstance(payload, list) else []


def _artifact_refs_from_row(row: dict[str, Any]) -> list[ArtifactRef]:
    return [
        ArtifactRef(**item) for item in list(_json_loads(row.get("evidence_json"), []))
    ]


def _create_record_from_row(row: dict[str, Any]) -> MemoryRecord:
    return MemoryRecord(
        id=str(row["id"]),
        scope=str(row["scope"]),
        type=row["type"],
        content=_json_loads(row.get("content_json"), row.get("content_json") or ""),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        key=row.get("key"),
        title=row.get("title"),
        tags=list(_json_loads(row.get("tags_json"), [])),
        entities=list(_json_loads(row.get("entities_json"), [])),
        source=row["source"],
        confidence=float(row["confidence"]),
        evidence_refs=_artifact_refs_from_row(row),
        expires_at=row.get("expires_at"),
        meta=dict(_json_loads(row.get("meta_json"), {})),
        last_hit_at=row.get("last_hit_at"),
        event_time=row.get("event_time") or str(row["created_at"]),
        valid_to=row.get("valid_to"),
        goal_id=row.get("goal_id"),
        tier=str(row.get("tier") or "working"),
        access_count=int(row.get("access_count") or 0),
        supersedes_id=row.get("supersedes_id"),
        superseded_by_id=row.get("superseded_by_id"),
        supersession_reason=row.get("supersession_reason"),
        is_deleted=bool(row.get("is_deleted")),
        deleted_at=row.get("deleted_at"),
        deleted_reason=row.get("deleted_reason"),
    )


def _create_tier_transition_from_row(row: dict[str, Any]) -> MemoryTierTransition:
    return MemoryTierTransition(
        transition_id=str(row["transition_id"]),
        record_id=str(row["record_id"]),
        scope=str(row["scope"]),
        record_type=row["record_type"],
        from_tier=str(row["from_tier"]),
        to_tier=str(row["to_tier"]),
        transition_reason=str(row["transition_reason"]),
        transition_at=str(row["transition_at"]),
        access_count=int(row.get("access_count") or 0),
        meta=dict(_json_loads(row.get("meta_json"), {})),
    )


def _create_candidate_from_row(row: dict[str, Any]) -> MemoryCandidate:
    review_payload = _json_loads(row.get("review_json"), None)
    review = CandidateReview(**review_payload) if review_payload else None
    return MemoryCandidate(
        candidate_id=str(row["candidate_id"]),
        session_id=str(row["session_id"]),
        proposed_scope=str(row["proposed_scope"]),
        type=row["type"],
        key=row.get("key"),
        title=row.get("title"),
        content=_json_loads(row.get("content_json"), row.get("content_json") or ""),
        tags=list(_json_loads(row.get("tags_json"), [])),
        entities=list(_json_loads(row.get("entities_json"), [])),
        source=row["source"],
        confidence=float(row["confidence"]),
        evidence_refs=_artifact_refs_from_row(row),
        status=row["status"],
        review=review,
        meta=dict(_json_loads(row.get("meta_json"), {})),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


__all__ = [
    "_artifact_refs_from_row",
    "_create_candidate_from_row",
    "_create_record_from_row",
    "_create_tier_transition_from_row",
    "_decode_evidence_ref_values",
]
