from __future__ import annotations

from openminion.modules.brain.loop.recursive.retrieval import (
    _resolve_retrieval_strategy,
)
from openminion.modules.brain.loop.recursive.schemas import RLMConstraints


# `self` is unused inside the resolver — pass `None` and call the unbound
# function directly to avoid spinning up an `RLMService` for a pure function.
def _resolve(
    *,
    query: str = "",
    purpose: str = "act",
    constraints: RLMConstraints | None = None,
):
    return _resolve_retrieval_strategy(
        None, query=query, purpose=purpose, constraints=constraints
    )


# Explicit RLMConstraints.retrieval_strategy pass-through


def test_explicit_constraint_contextual() -> None:
    assert (
        _resolve(constraints=RLMConstraints(retrieval_strategy="contextual"))
        == "contextual"
    )


def test_explicit_constraint_raptor() -> None:
    assert _resolve(constraints=RLMConstraints(retrieval_strategy="raptor")) == "raptor"


def test_explicit_constraint_longrag_doc_group() -> None:
    assert (
        _resolve(constraints=RLMConstraints(retrieval_strategy="longrag_doc_group"))
        == "longrag_doc_group"
    )


# Auto path: always contextual, regardless of query/purpose text


def test_auto_constraints_returns_contextual() -> None:
    assert (
        _resolve(constraints=RLMConstraints(retrieval_strategy="auto")) == "contextual"
    )


def test_no_constraints_returns_contextual() -> None:
    assert _resolve(constraints=None) == "contextual"


# RQHC-03 negative regressions: query keywords must NOT classify


def test_auto_query_keyword_research_multi_hop_no_longer_routes_to_raptor() -> None:
    assert (
        _resolve(
            query="research multi-hop",
            constraints=RLMConstraints(retrieval_strategy="auto"),
        )
        == "contextual"
    )


def test_auto_query_keyword_spec_policy_no_longer_routes_to_longrag() -> None:
    assert (
        _resolve(
            query="spec policy reference",
            constraints=RLMConstraints(retrieval_strategy="auto"),
        )
        == "contextual"
    )
