"""OpenMinion translation into Sophiagraph's existing retrieval owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from sophiagraph.query import (
    EmbeddingListOptions,
    GraphStageOptions,
    KeywordStageOptions,
    RecencyStageOptions,
    RetrievalRequest,
    TrustStageOptions,
    VectorStageOptions,
    assemble_retrieval,
)
from sophiagraph.query.retrieval_types import RetrievalOmission
from sophiagraph.vectors import SimilarityMetric, nearest_neighbors

from openminion.base.time import utc_now_iso
from openminion.modules.memory.runtime.config_values import (
    coerce_float,
    coerce_int,
    config_value,
)
from openminion.modules.memory.runtime.retrieval_eligibility import (
    retrieval_eligibility,
)


RecallStatus = Literal["ok", "disabled", "unsupported"]


@dataclass(frozen=True)
class RecallCapabilities:
    keyword: bool
    graph: bool
    recency: bool
    trust: bool
    vector: bool = False
    rerank: bool = False


@dataclass(frozen=True)
class RecallOutcome:
    status: RecallStatus
    hits: tuple[Any, ...] = ()
    reason: str = ""
    candidate_count: int = 0
    threshold_drops: int = 0
    omissions: tuple[RetrievalOmission, ...] = ()


@dataclass(frozen=True)
class PrecisionRecallOptions:
    mode: str = "legacy"
    candidate_multiplier: int = 3
    minimum_score: float = 0.048
    max_items: int = 5
    max_tokens: int = 500
    graph_depth: int = 1

    @classmethod
    def from_config(cls, config: Any | None) -> PrecisionRecallOptions:
        return cls(
            mode=str(
                config_value(config, "precision_mode", "legacy") or "legacy"
            ).strip(),
            candidate_multiplier=coerce_int(
                config_value(config, "precision_candidate_multiplier", 3),
                3,
                minimum=1,
            ),
            minimum_score=coerce_float(
                config_value(config, "precision_min_score", 0.048),
                0.048,
                maximum=float("inf"),
            ),
            max_items=coerce_int(
                config_value(config, "precision_max_items", 5),
                5,
                minimum=1,
            ),
            max_tokens=coerce_int(
                config_value(config, "precision_max_tokens", 500),
                500,
                minimum=64,
            ),
            graph_depth=coerce_int(
                config_value(config, "precision_graph_depth", 1),
                1,
                minimum=1,
            ),
        )


class SophiagraphRecallAdapter:
    """Run supported Sophiagraph retrieval stages."""

    def __init__(
        self,
        *,
        backend: Any,
        minimum_confidence: float = 0.0,
        vector_adapter: Any | None = None,
    ) -> None:
        self._backend = backend
        self._minimum_confidence = float(minimum_confidence)
        self._vector_adapter = vector_adapter

    def _vector_space(self) -> str:
        adapter = self._vector_adapter
        if adapter is None or not bool(getattr(adapter, "semantic_ready", False)):
            return ""
        identity = getattr(adapter, "vector_space_identity", None)
        vector_space = str(getattr(identity, "key", "") or "").strip()
        if not vector_space:
            return ""
        embeddings = self._backend.list_embeddings(
            EmbeddingListOptions(vector_space=vector_space, limit=1)
        )
        return vector_space if embeddings else ""

    @property
    def capabilities(self) -> RecallCapabilities:
        return RecallCapabilities(
            keyword=True,
            graph=True,
            recency=True,
            trust=True,
            vector=bool(self._vector_space()),
        )

    def retrieve(
        self,
        *,
        query: str,
        scopes: list[str],
        limit: int,
        candidate_multiplier: int,
        minimum_score: float,
        graph_depth: int,
    ) -> RecallOutcome:
        normalized_query = str(query or "").strip()
        normalized_scopes = [
            str(scope or "").strip() for scope in scopes if str(scope or "").strip()
        ]
        if not normalized_query or not normalized_scopes:
            return RecallOutcome(status="ok")

        final_limit = max(1, int(limit))
        candidate_limit = min(
            60,
            final_limit * max(1, int(candidate_multiplier)),
        )
        vector_space = self._vector_space()
        vector_options = None
        vector_search = None
        if vector_space:
            vector_adapter = self._vector_adapter
            if vector_adapter is None:
                return RecallOutcome(status="unsupported", reason="vector_unavailable")
            try:
                query_embedding = vector_adapter.embedding_provider.embed(
                    normalized_query
                )
            except (OSError, RuntimeError, ValueError) as exc:
                return RecallOutcome(
                    status="unsupported",
                    reason=f"semantic_embedding_unavailable:{type(exc).__name__}",
                )
            vector_options = VectorStageOptions(
                query_embedding=query_embedding.vector,
                vector_space=vector_space,
                limit=candidate_limit,
            )
            vector_search = _CandidateVectorSearch()
        result = assemble_retrieval(
            self._backend,
            RetrievalRequest(
                scopes=normalized_scopes,
                keyword=KeywordStageOptions(
                    query=normalized_query,
                    limit=candidate_limit,
                ),
                vector=vector_options,
                graph=GraphStageOptions(
                    depth=int(graph_depth),
                    max_expanded_records=candidate_limit,
                ),
                recency=RecencyStageOptions(),
                trust=TrustStageOptions(),
                limit=candidate_limit,
                eligibility_callback=lambda record, _stage: retrieval_eligibility(
                    record,
                    minimum_confidence=self._minimum_confidence,
                ),
            ),
            now_iso=utc_now_iso(),
            vector_adapter=vector_search,
        )
        current_hits = [
            hit
            for hit in result.hits
            if not bool(getattr(hit.record, "is_deleted", False))
            and not str(getattr(hit.record, "superseded_by_id", "") or "")
            and not (
                bool(getattr(hit.record, "valid_to", None))
                and hit.record.is_invalidated_at()
            )
        ]
        selected = [hit for hit in current_hits if hit.score >= float(minimum_score)]
        return RecallOutcome(
            status="ok",
            hits=tuple(selected[:final_limit]),
            candidate_count=len(current_hits),
            threshold_drops=max(0, len(current_hits) - len(selected)),
            omissions=tuple(result.omissions),
        )


class _CandidateVectorSearch:
    def search(
        self,
        *,
        query_embedding: list[float],
        vector_space: str,
        candidates: list[tuple[str, list[float]]],
        limit: int,
        metric: str,
    ) -> list[tuple[str, float]]:
        del vector_space
        return cast(
            list[tuple[str, float]],
            nearest_neighbors(
                SimilarityMetric(metric),
                query_embedding,
                candidates,
                k=limit,
            ),
        )


__all__ = [
    "PrecisionRecallOptions",
    "RecallCapabilities",
    "RecallOutcome",
    "SophiagraphRecallAdapter",
]
