from __future__ import annotations

from openminion.modules.retrieve.runtime.retrieval import resolve_retrieval_strategy
from openminion.modules.retrieve.errors import RetrieveCtlError
import pytest


def _resolve(
    *,
    requested_strategy: str = "auto",
    purpose: str = "act",
    scope: dict | None = None,
    default_strategy: str = "contextual",
):
    return resolve_retrieval_strategy(
        requested_strategy=requested_strategy,
        purpose=purpose,
        scope=scope or {},
        default_strategy=default_strategy,
    )


def test_explicit_contextual_passes_through() -> None:
    assert _resolve(requested_strategy="contextual") == "contextual"


def test_explicit_raptor_passes_through() -> None:
    assert _resolve(requested_strategy="raptor") == "raptor"


def test_explicit_longrag_doc_group_passes_through() -> None:
    assert _resolve(requested_strategy="longrag_doc_group") == "longrag_doc_group"


def test_explicit_semantic_is_unavailable() -> None:
    with pytest.raises(RetrieveCtlError, match="SEMANTIC_UNAVAILABLE"):
        _resolve(requested_strategy="semantic")


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


def test_auto_query_keyword_handbook_no_longer_routes_to_longrag() -> None:
    assert (
        _resolve(
            requested_strategy="auto",
            purpose="act",
            default_strategy="contextual",
        )
        == "contextual"
    )


def test_auto_query_keyword_research_multi_hop_no_longer_routes_to_raptor() -> None:
    assert (
        _resolve(
            requested_strategy="auto",
            purpose="act",
            default_strategy="contextual",
        )
        == "contextual"
    )


def test_auto_filters_tags_spec_no_longer_routes_to_longrag() -> None:
    assert (
        _resolve(
            requested_strategy="auto",
            purpose="act",
            default_strategy="contextual",
        )
        == "contextual"
    )
