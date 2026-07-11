from __future__ import annotations

import logging

from openminion.modules.memory.runtime.retrieval_pipeline import RetrievalPipeline


def _make_pipeline() -> RetrievalPipeline:
    return RetrievalPipeline(
        retrieve_ctl=None,
        config=None,
        ranking_config=None,
        logger=logging.getLogger("openminion.tests"),
        agent_id="pipeline-test-agent",
        retrieval_max_chars=2000,
        trace_fn=None,
    )


def test_rank_and_format_keeps_unified_scores_without_lexical_diversity_fallback() -> (
    None
):
    class _RetrieveCtl:
        def retrieve(self, **kwargs):  # type: ignore[no-untyped-def]
            filters = kwargs.get("filters", {})
            types = filters.get("types", [])
            if "mem" in types:
                return [
                    {
                        "text": "cluster alpha",
                        "score": 0.9,
                        "unified_score": 0.9,
                        "meta": {
                            "unit_id": "u1",
                            "score_breakdown": {
                                "relevance": 0.9,
                                "recency": 0.5,
                                "feedback": 0.0,
                                "type_bonus": 0.0,
                                "confidence": 0.6,
                                "unified_score": 0.9,
                            },
                        },
                    },
                    {
                        "text": "cluster alpha",
                        "score": 0.8,
                        "unified_score": 0.8,
                        "meta": {
                            "unit_id": "u2",
                            "score_breakdown": {
                                "relevance": 0.8,
                                "recency": 0.4,
                                "feedback": 0.0,
                                "type_bonus": 0.0,
                                "confidence": 0.6,
                                "unified_score": 0.8,
                            },
                        },
                    },
                ]
            return [
                {
                    "text": "unique beta",
                    "score": 0.7,
                    "unified_score": 0.7,
                    "meta": {
                        "unit_id": "u3",
                        "score_breakdown": {
                            "relevance": 0.7,
                            "recency": 0.4,
                            "feedback": 0.0,
                            "type_bonus": 0.0,
                            "confidence": 0.6,
                            "unified_score": 0.7,
                        },
                    },
                }
            ]

    pipeline = RetrievalPipeline(
        retrieve_ctl=_RetrieveCtl(),
        config=None,
        ranking_config=type("Ranking", (), {"mmr_enabled": True, "mmr_lambda": 0.0})(),
        logger=logging.getLogger("openminion.tests"),
        agent_id="pipeline-test-agent",
        retrieval_max_chars=2000,
        trace_fn=None,
    )

    _, _, retrieve_hits, _ = pipeline.rank_and_format(
        [],
        session_id="session-1",
        user_message="cluster alpha",
    )

    assert [item["text"] for item in retrieve_hits[:2]] == [
        "cluster alpha",
        "cluster alpha",
    ]
    assert float(retrieve_hits[0]["score"]) == 0.9
    assert (
        retrieve_hits[0]["meta"]["score_breakdown"]["unified_score"]
        == retrieve_hits[0]["score"]
    )


def test_mmr_rerank_stays_score_ordered_without_embeddings_when_lambda_zero() -> None:
    pipeline = _make_pipeline()
    reranked = pipeline.mmr_rerank(
        [
            {"text": "cluster alpha", "score": 0.9},
            {"text": "cluster alpha", "score": 0.8},
            {"text": "unique beta", "score": 0.7},
        ],
        k=2,
        lambda_=0.0,
    )

    texts = [item["text"] for item in reranked]
    assert "cluster alpha" in texts
    assert texts == ["cluster alpha", "cluster alpha"]


def test_default_conversational_retrieval_does_not_expand_query() -> None:

    class _RetrieveCtl:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def retrieve(self, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(dict(kwargs))
            filters = kwargs.get("filters") or {}
            types = filters.get("types", [])
            if "mem" in types or "episode" in types:
                return [{"text": "primary", "score": 0.7, "meta": {"unit_id": "u1"}}]
            return []

    retrieve_ctl = _RetrieveCtl()
    pipeline = RetrievalPipeline(
        retrieve_ctl=retrieve_ctl,
        config=None,  # default config -> _config_default falls back; expansion off
        ranking_config=None,
        logger=logging.getLogger("openminion.tests"),
        agent_id="pipeline-test-agent",
        retrieval_max_chars=2000,
        trace_fn=None,
    )

    conversational, _hit_counts = pipeline._retrieve_split(  # noqa: SLF001
        retrieve_ctl,
        query="task",
        session_id="s",
        agent_id="a",
        project_id=None,
        k_conversational=4,
        k_knowledge=4,
    )

    conversational_calls = [
        call
        for call in retrieve_ctl.calls
        if "mem" in (call.get("filters") or {}).get("types", [])
    ]
    assert len(conversational_calls) == 1, (
        f"expected exactly one conversational retrieve call (no expansion), "
        f"got {len(conversational_calls)}: queries="
        f"{[call.get('query') for call in conversational_calls]}"
    )
    assert conversational_calls[0]["query"] == "task"
    assert conversational_calls[0]["strategy"] == "contextual"
    assert [item.get("meta", {}).get("unit_id") for item in conversational] == ["u1"]


def test_candidate_similarity_without_embeddings_returns_zero() -> None:
    pipeline = _make_pipeline()

    similarity = pipeline._candidate_similarity(  # noqa: SLF001
        {"text": "todo buy milk", "score": 0.8},
        {"text": "task buy milk", "score": 0.7},
    )

    assert similarity == 0.0
