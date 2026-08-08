from __future__ import annotations

from typing import Any, Mapping

from ..schemas import RetrievalStrategy


def no_result_reason(
    *,
    normalized_query: str,
    scored_candidate_count: int,
    candidate_count: int,
    selected_count: int,
    item_count: int,
    purpose: str,
) -> str | None:
    if not normalized_query:
        return "empty_query"
    if scored_candidate_count <= 0:
        return "no_candidates"
    if str(purpose or "").strip().lower() == "verify" and candidate_count <= 0:
        return "verify_threshold"
    if selected_count <= 0:
        return "no_selected_candidates"
    if item_count <= 0:
        return "no_renderable_items"
    return None


def strategy_resolution_reason(
    *,
    requested_strategy: str,
    resolved_strategy: RetrievalStrategy,
    scope: Mapping[str, Any],
    purpose: str,
    vector_adapter_available: bool,
    embeddings_enabled: bool,
) -> str:
    requested = str(requested_strategy or "auto").strip().lower() or "auto"
    if requested == str(resolved_strategy):
        return "explicit"
    if requested == "semantic":
        if not vector_adapter_available:
            return "vector_adapter_missing"
        if not embeddings_enabled:
            return "embeddings_disabled"
    if requested == "auto":
        if str(purpose or "").strip().lower() == "verify":
            return "verify_contextual"
        if bool(scope.get("doc_heavy")):
            return "doc_heavy_scope"
        return "default_strategy"
    return "fallback_contextual"


def diagnostic_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    meta = candidate.get("meta")
    score_breakdown: Any = {}
    if isinstance(meta, Mapping):
        score_breakdown = meta.get("score_breakdown", {})
    return {
        "unit_id": str(candidate.get("unit_id", "")),
        "doc_id": str(candidate.get("doc_id", "")),
        "source_type": str(candidate.get("source_type", "")),
        "source_ref": str(candidate.get("source_ref", "")),
        "title": str(candidate.get("title", "")),
        "score": float(candidate.get("score", 0.0) or 0.0),
        "vector_score": float(candidate.get("vector_score", 0.0) or 0.0),
        "unit_kind": str(candidate.get("unit_kind", "chunk")),
        "level": str(candidate.get("level", "none")),
        "tags": [str(tag) for tag in candidate.get("tags", [])],
        "score_breakdown": dict(score_breakdown)
        if isinstance(score_breakdown, Mapping)
        else {},
    }
