from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _make_adapter(*, retrieve_ctl: object | None = None) -> MemoryServiceGatewayAdapter:
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    return MemoryServiceGatewayAdapter(
        service,
        agent_id="rmq-b-agent",
        retrieve_ctl=retrieve_ctl,
    )


def _pipeline(adapter: MemoryServiceGatewayAdapter):
    return adapter._pipeline  # noqa: SLF001


def test_candidate_similarity_without_embeddings_returns_zero() -> None:
    adapter = _make_adapter()
    assert (
        _pipeline(adapter)._candidate_similarity(  # noqa: SLF001
            {"text": "alpha beta", "score": 0.9},
            {"text": "alpha beta", "score": 0.8},
        )
        == 0.0
    )


def test_mmr_rerank_lambda_edges() -> None:
    adapter = _make_adapter()
    candidates = [
        {"text": "dup cluster one", "score": 0.9},
        {"text": "dup cluster one", "score": 0.8},
        {"text": "dup cluster one", "score": 0.7},
        {"text": "unique topic two", "score": 0.6},
    ]
    pure_score = _pipeline(adapter).mmr_rerank(candidates, k=3, lambda_=1.0)
    assert [item["score"] for item in pure_score] == [0.9, 0.8, 0.7]

    pure_diversity = _pipeline(adapter).mmr_rerank(candidates, k=2, lambda_=0.0)
    assert [item["score"] for item in pure_diversity] == [0.9, 0.8]


def test_gateway_record_hits_callsite_and_non_propagating_error() -> None:
    retrieve_ctl = Mock(name="retrieve_ctl")
    retrieve_ctl.retrieve.side_effect = [
        [{"text": "first", "meta": {"unit_id": "u1"}, "score": 0.6}],
        [{"text": "second", "meta": {"unit_id": "u2"}, "score": 0.5}],
    ]
    adapter = _make_adapter(retrieve_ctl=retrieve_ctl)
    adapter._config = SimpleNamespace(  # noqa: SLF001
        defaults=SimpleNamespace(
            k_conversational=1,
            k_knowledge=1,
            decay_halflife_days=30,
            recency_weight=0.3,
            mmr_enabled=True,
            mmr_lambda=0.6,
        )
    )
    _pipeline(adapter)._config = adapter._config  # noqa: SLF001

    adapter.build_retrieval_context_with_metadata(
        session_id="s-b", user_message="query"
    )
    retrieve_ctl.record_hits.assert_called_once()
    args, kwargs = retrieve_ctl.record_hits.call_args
    assert args and args[0] == ["u1", "u2"]
    assert "observed_at" in kwargs

    retrieve_ctl.record_hits.reset_mock()
    retrieve_ctl.retrieve.side_effect = [
        [{"text": "no-meta-a", "score": 0.6}],
        [{"text": "no-meta-b", "score": 0.5}],
    ]
    adapter.build_retrieval_context_with_metadata(
        session_id="s-b", user_message="query"
    )
    retrieve_ctl.record_hits.assert_not_called()

    retrieve_ctl.retrieve.side_effect = [
        [{"text": "again", "meta": {"unit_id": "u3"}, "score": 0.9}],
        [],
    ]
    retrieve_ctl.record_hits.side_effect = RuntimeError("write failed")
    # No exception should propagate out of context build.
    content, _meta = adapter.build_retrieval_context_with_metadata(
        session_id="s-b",
        user_message="query",
    )
    assert isinstance(content, str)
