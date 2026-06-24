from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from openminion.base.time import utc_now
from openminion.modules.storage.runtime.retrieval_service import HybridRetrievalRanker


@dataclass(frozen=True)
class _Record:
    id: str
    content: str
    scope: str
    agent_id: str
    project_id: str
    created_at: str
    session_id: str = "session-1"
    message_id: str = "message-1"
    event_id: str = "event-1"
    chunk_ref: str = "chunk-1"


class _VectorIndex:
    def __init__(self, results: list[tuple[str, float, dict]]) -> None:
        self._results = results
        self.calls: list[dict | None] = []

    def search(
        self,
        *,
        query_vector: object | None,
        top_k: int,
        filters: dict | None = None,
    ) -> list[tuple[str, float, dict]]:
        self.calls.append(filters)
        return list(self._results)


def _iso(days_ago: int) -> str:
    return (utc_now() - timedelta(days=days_ago)).isoformat()


def test_rank_uses_vector_scores_without_keyword_overlap() -> None:
    records = [
        _Record(
            id="alpha",
            content="generic procedural text",
            scope="project",
            agent_id="agent-1",
            project_id="project-1",
            created_at=_iso(10),
        ),
        _Record(
            id="beta",
            content="query words appear here but should not drive ranking",
            scope="project",
            agent_id="agent-1",
            project_id="project-1",
            created_at=_iso(1),
        ),
    ]
    vector_index = _VectorIndex(
        [
            ("alpha", 0.9, {}),
            ("beta", 0.1, {}),
        ]
    )

    results, diagnostics = HybridRetrievalRanker(vector_index=vector_index).rank(
        query="query words appear here",
        records=records,
        scope_filter="project",
        agent_id="agent-1",
        project_id="project-1",
        top_k=2,
    )

    assert [item.record_id for item in results] == ["alpha", "beta"]
    assert diagnostics.vector_enabled is True
    assert diagnostics.fallback_reason is None
    assert vector_index.calls == [
        {"scope": "project", "agent_id": "agent-1", "project_id": "project-1"}
    ]
    assert results[0].source == "vector"
    assert results[0].provenance["rank_strategy"] == "vector"
    assert "keyword_score" not in results[0].provenance


def test_rank_falls_back_to_recency_when_vector_index_is_unavailable() -> None:
    records = [
        _Record(
            id="older",
            content="same content one",
            scope="global",
            agent_id="agent-1",
            project_id="project-1",
            created_at=_iso(30),
        ),
        _Record(
            id="newer",
            content="same content two",
            scope="global",
            agent_id="agent-1",
            project_id="project-1",
            created_at=_iso(0),
        ),
    ]

    results, diagnostics = HybridRetrievalRanker(vector_index=None).rank(
        query="same content one",
        records=records,
        top_k=2,
    )

    assert [item.record_id for item in results] == ["newer", "older"]
    assert diagnostics.vector_enabled is False
    assert diagnostics.fallback_reason == "vector_index_unavailable"
    assert all(item.source == "recency" for item in results)
    assert all(item.provenance["rank_strategy"] == "recency" for item in results)
