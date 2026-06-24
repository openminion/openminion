"""SQLite candidate workflows for memory storage."""

import datetime
import json
from dataclasses import replace
from typing import Any

from ..base import CandidateListOptions
from ...errors import NotFoundError
from ...models import MemoryCandidate


def candidate_put(store: Any, candidate: MemoryCandidate) -> str:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    previous_ref_values: list[Any] = []
    with store._connect() as conn:
        existing = conn.execute(
            """
            SELECT evidence_json
            FROM memory_candidates
            WHERE candidate_id = ?
            """,
            (candidate.candidate_id,),
        ).fetchone()
        if existing is not None:
            previous_ref_values = store._decode_evidence_ref_values(
                existing["evidence_json"]
            )
        conn.execute(
            """
            INSERT OR REPLACE INTO memory_candidates (
                candidate_id, session_id, proposed_scope, type, key, title,
                content_json, tags_json, entities_json, source, confidence,
                evidence_json, meta_json, status, review_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.candidate_id,
                candidate.session_id,
                candidate.proposed_scope,
                candidate.type,
                candidate.key,
                candidate.title,
                json.dumps(candidate.content),
                json.dumps(candidate.tags),
                json.dumps(candidate.entities),
                candidate.source,
                candidate.confidence,
                json.dumps([vars(r) for r in candidate.evidence_refs]),
                json.dumps(candidate.meta),
                candidate.status,
                json.dumps(vars(candidate.review)) if candidate.review else None,
                candidate.created_at or now,
                candidate.updated_at or now,
            ),
        )
    if previous_ref_values:
        store._remove_artifact_refs(
            owner_id=candidate.candidate_id,
            ref_values=previous_ref_values,
        )
    store._add_artifact_refs(
        owner_id=candidate.candidate_id,
        ref_values=candidate.evidence_refs,
    )
    return candidate.candidate_id


def candidate_get(store: Any, candidate_id: str) -> MemoryCandidate | None:
    with store._connect() as conn:
        row = conn.execute(
            "SELECT * FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
    return None if row is None else store._create_candidate_from_row(row)


def candidate_delete(store: Any, candidate_id: str) -> None:
    candidate: MemoryCandidate | None = None
    with store._connect() as conn:
        row = conn.execute(
            "SELECT * FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row is not None:
            candidate = store._create_candidate_from_row(row)
        conn.execute(
            "DELETE FROM memory_candidates WHERE candidate_id = ?", (candidate_id,)
        )
    if candidate is not None:
        store._remove_artifact_refs(
            owner_id=candidate_id,
            ref_values=candidate.evidence_refs,
        )


def candidate_list(store: Any, options: CandidateListOptions) -> list[MemoryCandidate]:
    clauses: list[str] = []
    params: list[Any] = []

    if options.session_id is not None:
        clauses.append("session_id = ?")
        params.append(options.session_id)
    if options.proposed_scope is not None:
        clauses.append("proposed_scope = ?")
        params.append(options.proposed_scope)
    if not clauses:
        clauses.append("1 = 1")

    query = "SELECT * FROM memory_candidates WHERE " + " AND ".join(clauses)

    if options.status:
        query += " AND status = ?"
        params.append(options.status)

    query += " ORDER BY created_at ASC"

    if options.limit is not None:
        query += " LIMIT ?"
        params.append(options.limit)

    with store._connect() as conn:
        cursor = conn.execute(query, params)
        return [store._create_candidate_from_row(row) for row in cursor.fetchall()]


def candidate_update(
    store: Any,
    candidate_id: str,
    patch: dict[str, Any],
) -> MemoryCandidate:
    with store._connect() as conn:
        row = conn.execute(
            "SELECT * FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(
                f"Candidate {candidate_id} not found",
                details={"candidate_id": candidate_id},
            )

        current = store._create_candidate_from_row(row)
        review = patch.get("review", current.review)
        updated = replace(
            current,
            session_id=str(patch.get("session_id", current.session_id)),
            proposed_scope=str(patch.get("proposed_scope", current.proposed_scope)),
            type=patch.get("type", current.type),
            key=str(patch.get("key")) if patch.get("key") is not None else current.key,
            title=(
                str(patch.get("title"))
                if patch.get("title") is not None
                else current.title
            ),
            content=patch.get("content", current.content),
            tags=list(patch.get("tags", current.tags)),
            entities=list(patch.get("entities", current.entities)),
            source=str(patch.get("source", current.source)),
            confidence=float(patch.get("confidence", current.confidence)),
            status=str(patch.get("status", current.status)),
            review=review,
            meta=dict(patch.get("meta", current.meta)),
            created_at=current.created_at,
            updated_at=str(
                patch.get(
                    "updated_at",
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                )
            ),
        )

        conn.execute(
            """
            UPDATE memory_candidates
            SET session_id = ?, proposed_scope = ?, type = ?, key = ?, title = ?,
                content_json = ?, tags_json = ?, entities_json = ?, source = ?,
                confidence = ?, evidence_json = ?, meta_json = ?, status = ?,
                review_json = ?, created_at = ?, updated_at = ?
            WHERE candidate_id = ?
            """,
            (
                updated.session_id,
                updated.proposed_scope,
                updated.type,
                updated.key,
                updated.title,
                json.dumps(updated.content),
                json.dumps(updated.tags),
                json.dumps(updated.entities),
                updated.source,
                updated.confidence,
                json.dumps([vars(r) for r in updated.evidence_refs]),
                json.dumps(updated.meta),
                updated.status,
                json.dumps(vars(updated.review)) if updated.review else None,
                updated.created_at,
                updated.updated_at,
                candidate_id,
            ),
        )
        refreshed = conn.execute(
            "SELECT * FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
    return store._create_candidate_from_row(refreshed)


__all__ = [
    "candidate_delete",
    "candidate_get",
    "candidate_list",
    "candidate_put",
    "candidate_update",
]
