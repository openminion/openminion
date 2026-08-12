from __future__ import annotations

from openminion.modules.brain.loop.recursive.retrieval import (
    _resolve_retrieval_strategy,
)
from openminion.modules.brain.loop.recursive.schemas import RLMConstraints


def _resolve(
    *,
    constraints: RLMConstraints | None = None,
):
    return _resolve_retrieval_strategy(None, constraints=constraints)


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


def test_auto_constraints_returns_contextual() -> None:
    assert (
        _resolve(constraints=RLMConstraints(retrieval_strategy="auto")) == "contextual"
    )


def test_no_constraints_returns_contextual() -> None:
    assert _resolve(constraints=None) == "contextual"
