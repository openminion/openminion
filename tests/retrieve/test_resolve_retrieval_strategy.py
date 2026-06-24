from __future__ import annotations

from openminion.modules.retrieve.runtime.retrieval import resolve_retrieval_strategy
from openminion.modules.retrieve.schemas import RetrievalFilters


def _resolve(
    *,
    requested_strategy: str = "auto",
    purpose: str = "act",
    query: str = "",
    scope: dict | None = None,
    filters: RetrievalFilters | None = None,
    default_strategy: str = "contextual",
    vector_adapter_enabled: bool = True,
    embeddings_enabled: bool = True,
):
    return resolve_retrieval_strategy(
        requested_strategy=requested_strategy,
        purpose=purpose,
        query=query,
        scope=scope or {},
        filters=filters or RetrievalFilters(),
        default_strategy=default_strategy,
        vector_adapter_enabled=vector_adapter_enabled,
        embeddings_enabled=embeddings_enabled,
    )


# Explicit caller pass-through


def test_explicit_contextual_passes_through() -> None:
    assert _resolve(requested_strategy="contextual") == "contextual"


def test_explicit_raptor_passes_through() -> None:
    assert _resolve(requested_strategy="raptor") == "raptor"


def test_explicit_longrag_doc_group_passes_through() -> None:
    assert _resolve(requested_strategy="longrag_doc_group") == "longrag_doc_group"


def test_explicit_semantic_falls_back_when_vector_disabled() -> None:
    assert (
        _resolve(requested_strategy="semantic", vector_adapter_enabled=False)
        == "contextual"
    )


def test_explicit_semantic_kept_when_vector_and_embeddings_enabled() -> None:
    assert (
        _resolve(
            requested_strategy="semantic",
            vector_adapter_enabled=True,
            embeddings_enabled=True,
        )
        == "semantic"
    )


# Auto path: structural inputs that remain


def test_auto_with_verify_purpose_returns_contextual() -> None:
    assert _resolve(requested_strategy="auto", purpose="verify") == "contextual"


def test_auto_with_doc_heavy_scope_returns_raptor() -> None:
    assert _resolve(requested_strategy="auto", scope={"doc_heavy": True}) == "raptor"


def test_auto_uses_default_strategy_fallthrough() -> None:
    assert _resolve(requested_strategy="auto", default_strategy="raptor") == "raptor"


def test_auto_invalid_default_falls_back_to_contextual() -> None:
    assert (
        _resolve(requested_strategy="auto", default_strategy="invalid") == "contextual"
    )


# RQHC-03 negative regressions: query keywords / filters.tags must NOT classify


def test_auto_query_keyword_handbook_no_longer_routes_to_longrag() -> None:
    assert (
        _resolve(
            requested_strategy="auto",
            query="please find the policy handbook",
            purpose="act",
            default_strategy="contextual",
        )
        == "contextual"
    )


def test_auto_query_keyword_research_multi_hop_no_longer_routes_to_raptor() -> None:
    assert (
        _resolve(
            requested_strategy="auto",
            query="research multi-hop comparison",
            purpose="act",
            default_strategy="contextual",
        )
        == "contextual"
    )


def test_auto_filters_tags_spec_no_longer_routes_to_longrag() -> None:
    assert (
        _resolve(
            requested_strategy="auto",
            filters=RetrievalFilters(tags=["spec"]),
            purpose="act",
            default_strategy="contextual",
        )
        == "contextual"
    )
