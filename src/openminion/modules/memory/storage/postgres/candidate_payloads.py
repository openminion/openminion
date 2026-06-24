import datetime
from dataclasses import replace
from typing import Any

from ...models import MemoryCandidate
from .sql import _json_dumps


def _candidate_insert_params(candidate: MemoryCandidate, now: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "session_id": candidate.session_id,
        "proposed_scope": candidate.proposed_scope,
        "record_type": candidate.type,
        "key": candidate.key,
        "title": candidate.title,
        "content_json": _json_dumps(candidate.content),
        "tags_json": _json_dumps(candidate.tags),
        "entities_json": _json_dumps(candidate.entities),
        "source": candidate.source,
        "confidence": candidate.confidence,
        "evidence_json": _json_dumps([vars(item) for item in candidate.evidence_refs]),
        "meta_json": _json_dumps(candidate.meta),
        "status": candidate.status,
        "review_json": _json_dumps(vars(candidate.review))
        if candidate.review
        else "null",
        "created_at": candidate.created_at or now,
        "updated_at": candidate.updated_at or now,
    }


def _patched_candidate(
    current: MemoryCandidate, patch: dict[str, Any]
) -> MemoryCandidate:
    return replace(
        current,
        session_id=str(patch.get("session_id", current.session_id)),
        proposed_scope=str(patch.get("proposed_scope", current.proposed_scope)),
        type=patch.get("type", current.type),
        key=str(patch.get("key")) if patch.get("key") is not None else current.key,
        title=str(patch.get("title"))
        if patch.get("title") is not None
        else current.title,
        content=patch.get("content", current.content),
        tags=list(patch.get("tags", current.tags)),
        entities=list(patch.get("entities", current.entities)),
        source=str(patch.get("source", current.source)),
        confidence=float(patch.get("confidence", current.confidence)),
        status=str(patch.get("status", current.status)),
        review=patch.get("review", current.review),
        meta=dict(patch.get("meta", current.meta)),
        created_at=current.created_at,
        updated_at=str(
            patch.get(
                "updated_at",
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
            )
        ),
    )


__all__ = ["_candidate_insert_params", "_patched_candidate"]
