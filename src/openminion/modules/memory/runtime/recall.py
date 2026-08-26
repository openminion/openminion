"""OpenMinion translation into Sophiagraph's existing retrieval owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sophiagraph.query import (
    GraphStageOptions,
    KeywordStageOptions,
    RecencyStageOptions,
    RetrievalRequest,
    TrustStageOptions,
    assemble_retrieval,
)

from openminion.base.time import utc_now_iso
from openminion.modules.memory.runtime.config_values import (
    coerce_float,
    coerce_int,
    config_value,
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

    def __init__(self, *, backend: Any) -> None:
        self._backend = backend

    @property
    def capabilities(self) -> RecallCapabilities:
        return RecallCapabilities(
            keyword=True,
            graph=True,
            recency=True,
            trust=True,
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
        result = assemble_retrieval(
            self._backend,
            RetrievalRequest(
                scopes=normalized_scopes,
                keyword=KeywordStageOptions(
                    query=normalized_query,
                    limit=candidate_limit,
                ),
                graph=GraphStageOptions(
                    depth=int(graph_depth),
                    max_expanded_records=candidate_limit,
                ),
                recency=RecencyStageOptions(),
                trust=TrustStageOptions(),
                limit=candidate_limit,
            ),
            now_iso=utc_now_iso(),
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
        )


__all__ = [
    "PrecisionRecallOptions",
    "RecallCapabilities",
    "RecallOutcome",
    "SophiagraphRecallAdapter",
]
