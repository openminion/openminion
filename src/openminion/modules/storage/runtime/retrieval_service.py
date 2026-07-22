from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass(frozen=True)
class RetrievalResult:
    record_id: str
    content: str
    score: float
    source: str
    provenance: dict


@dataclass(frozen=True)
class RetrievalDiagnostics:
    records_considered: int
    records_selected: int
    query: str
    filters: dict
    score_bands: dict
    vector_enabled: bool
    fallback_reason: Optional[str]


class HybridRetrievalRanker:
    """Rank retrieval results with vector scores when present, else recency."""

    def __init__(
        self,
        vector_index: Any = None,
    ) -> None:
        self._vector_index = vector_index

    def rank(
        self,
        query: str,
        records: list[Any],
        top_k: int = 10,
        scope_filter: Optional[str] = None,
        agent_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> tuple[list[RetrievalResult], RetrievalDiagnostics]:
        filters = {}
        if scope_filter:
            filters["scope"] = scope_filter
        if agent_id:
            filters["agent_id"] = agent_id
        if project_id:
            filters["project_id"] = project_id

        vector_results = []
        if self._vector_index:
            try:
                vector_results = self._vector_index.search(
                    query_vector=None,
                    top_k=top_k * 2,
                    filters=filters if filters else None,
                )
            except Exception:
                vector_results = []

        vector_enabled = len(vector_results) > 0
        fallback_reason = None if vector_enabled else "vector_index_unavailable"
        vector_scores = {vec_id: float(score) for vec_id, score, _ in vector_results}

        scored_records: list[tuple[Any, float, float]] = []

        for record in records:
            if scope_filter and record.scope != scope_filter:
                continue
            if agent_id and record.agent_id != agent_id:
                continue
            if project_id and record.project_id != project_id:
                continue

            recency_score = self._compute_recency_score(record.created_at)
            vector_score = vector_scores.get(record.id, 0.0)
            final_score = vector_score if vector_enabled else recency_score
            scored_records.append((record, final_score, recency_score))

        scored_records.sort(key=lambda item: (item[1], item[2]), reverse=True)

        results: list[RetrievalResult] = []
        for record, score, recency_score in scored_records[:top_k]:
            vector_score = vector_scores.get(record.id, 0.0)
            results.append(
                RetrievalResult(
                    record_id=record.id,
                    content=record.content,
                    score=score,
                    source="vector" if vector_enabled else "recency",
                    provenance={
                        "session_id": record.session_id,
                        "message_id": record.message_id,
                        "event_id": record.event_id,
                        "chunk_ref": record.chunk_ref,
                        "rank_strategy": "vector" if vector_enabled else "recency",
                        "vector_score": vector_score,
                        "recency_score": recency_score,
                    },
                )
            )

        score_bands = self._compute_score_bands(scored_records[:top_k])

        diagnostics = RetrievalDiagnostics(
            records_considered=len(records),
            records_selected=len(results),
            query=query,
            filters=filters,
            score_bands=score_bands,
            vector_enabled=vector_enabled,
            fallback_reason=fallback_reason,
        )

        return results, diagnostics

    def _compute_recency_score(self, created_at: str) -> float:
        try:
            if created_at.endswith("Z"):
                created_at = created_at[:-1] + "+00:00"
            dt = datetime.fromisoformat(created_at)
            now = datetime.now(timezone.utc)
            age_seconds = (now - dt).total_seconds()

            day_seconds = 86400
            if age_seconds < day_seconds:
                return 1.0
            elif age_seconds < 7 * day_seconds:
                return 0.8
            elif age_seconds < 30 * day_seconds:
                return 0.5
            else:
                return 0.2
        except Exception:
            return 0.5

    def _compute_score_bands(self, scored: list[tuple[Any, float, float]]) -> dict:
        if not scored:
            return {"high": 0, "medium": 0, "low": 0}

        scores = [score for _, score, _ in scored]
        max_score = max(scores) if scores else 1.0

        high = sum(1 for s in scores if s >= max_score * 0.8)
        medium = sum(1 for s in scores if max_score * 0.4 <= s < max_score * 0.8)
        low = sum(1 for s in scores if s < max_score * 0.4)

        return {"high": high, "medium": medium, "low": low}


class RetrievalService:
    """Main retrieval service with vector-on/vector-off modes."""

    def __init__(
        self,
        memory_store: Any,
        vector_index_adapter: Optional[Any] = None,
        ranker: Optional[HybridRetrievalRanker] = None,
        enabled: bool = True,
    ) -> None:
        self._record_store = memory_store
        self._vector_index = vector_index_adapter
        self._ranker = ranker or HybridRetrievalRanker(
            vector_index=vector_index_adapter
        )
        self._enabled = enabled

    def retrieve(
        self,
        query: str,
        session_id: Optional[str] = None,
        top_k: int = 10,
        scope: Optional[str] = None,
        agent_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> tuple[list[RetrievalResult], RetrievalDiagnostics]:
        if not self._enabled:
            return [], RetrievalDiagnostics(
                records_considered=0,
                records_selected=0,
                query=query,
                filters={},
                score_bands={},
                vector_enabled=False,
                fallback_reason="retrieval_disabled",
            )

        if session_id:
            records = self._record_store.list_by_session(
                session_id=session_id,
                limit=top_k * 3,
            )
        else:
            records = self._record_store.list_pending_vectors(limit=top_k * 3)

        return self._ranker.rank(
            query=query,
            records=records,
            top_k=top_k,
            scope_filter=scope,
            agent_id=agent_id,
            project_id=project_id,
        )

    def sync_pending(self) -> int:
        if not self._enabled or not self._vector_index:
            return 0
        return self._vector_index.sync_pending_records()

    def is_enabled(self) -> bool:
        return self._enabled


def create_retrieval_service(
    db_path: str,
    vector_index_adapter: Optional[Any] = None,
    enabled: bool = True,
) -> RetrievalService:
    from .memory_store import create_memory_record_store

    record_store = create_memory_record_store(db_path)
    return RetrievalService(
        memory_store=record_store,
        vector_index_adapter=vector_index_adapter,
        enabled=enabled,
    )
