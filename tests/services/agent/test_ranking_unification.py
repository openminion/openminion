from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import RankingConfig, from_base_config
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.runtime.scorer import (
    RankingWeights,
    compute_unified_score,
    extract_signals,
    recency_score,
    score_record,
    score_records,
)
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _record(
    record_id: str,
    *,
    record_type: str = "fact",
    created_at: str = "2026-03-20T00:00:00+00:00",
    confidence: float = 0.6,
    bm25_score: float = 0.7,
    feedback_score: float = 0.0,
    hit_count: int = 0,
    outcome_success_count: int = 0,
    outcome_failure_count: int = 0,
) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope="agent:rank-agent",
        type=record_type,
        title=record_id,
        content=f"{record_type}:{record_id}",
        created_at=created_at,
        updated_at=created_at,
        confidence=confidence,
        meta={
            "bm25_score": bm25_score,
            "feedback_score": feedback_score,
            "hit_count": hit_count,
            "outcome_success_count": outcome_success_count,
            "outcome_failure_count": outcome_failure_count,
        },
    )


def _memory_config(*, ranking: RankingConfig | None = None):
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    return replace(cfg, ranking=ranking or cfg.ranking)


def test_unified_scorer_helpers_cover_signal_and_weight_basics() -> None:
    assert recency_score(0.0, 30.0) == 1.0
    assert recency_score(30.0, 30.0) == 0.5

    record = _record(
        "signal-test",
        feedback_score=0.3,
        hit_count=2,
        confidence=0.9,
        outcome_success_count=3,
    )
    signals = extract_signals(record, ranking_config=RankingConfig())
    assert 0.0 <= signals.relevance <= 1.0
    assert 0.0 <= signals.recency <= 1.0
    assert 0.0 <= signals.feedback <= 1.0
    assert 0.0 <= signals.type_bonus <= 1.0
    assert signals.confidence == 0.9
    assert signals.outcome_utility > 0.5

    score = compute_unified_score(
        signals,
        RankingWeights(
            relevance=1.0 / 6.0,
            recency=1.0 / 6.0,
            feedback=1.0 / 6.0,
            type_bonus=1.0 / 6.0,
            confidence=1.0 / 6.0,
            outcome_utility=1.0 / 6.0,
        ),
    )
    assert 0.0 <= score <= 1.0


def test_feedback_signal_uses_hits_only_and_outcome_utility_owns_feedback_score() -> (
    None
):
    record = _record(
        "utility-ownership",
        feedback_score=0.9,
        hit_count=0,
        outcome_success_count=4,
    )

    signals = extract_signals(record, ranking_config=RankingConfig())

    assert signals.feedback == 0.0
    assert signals.outcome_utility > 0.7


def test_score_record_populates_score_breakdown() -> None:
    record = _record(
        "breakdown-test",
        record_type="correction",
        bm25_score=0.8,
        outcome_success_count=2,
    )

    scored = score_record(record, ranking_config=RankingConfig())

    breakdown = scored.meta["score_breakdown"]
    assert breakdown["type_bonus"] > 0.0
    assert "outcome_utility" in breakdown
    assert breakdown["unified_score"] > 0.0


def test_meta_insight_type_bonus_uses_configured_normalization() -> None:
    ranking = RankingConfig(
        type_boost_correction=1.02,
        type_boost_user_preference=1.01,
        type_boost_pin=1.01,
        type_boost_project_convention=1.01,
        type_boost_meta_insight=1.05,
    )
    fact = _record("fact", record_type="fact", bm25_score=0.8, confidence=0.8)
    insight = _record(
        "insight",
        record_type="meta_insight",
        bm25_score=0.8,
        confidence=0.8,
    )

    fact_scored = score_record(fact, ranking_config=ranking)
    insight_scored = score_record(insight, ranking_config=ranking)

    assert insight_scored.meta["score_breakdown"]["type_bonus"] > 0.0
    assert (
        insight_scored.meta["score_breakdown"]["type_bonus"]
        > fact_scored.meta["score_breakdown"]["type_bonus"]
    )
    assert float(insight_scored.meta["unified_score"]) > float(
        fact_scored.meta["unified_score"]
    )


def test_score_records_prefers_recent_feedback_rich_and_typed_records() -> None:
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(days=1)).isoformat()
    recent = (now - timedelta(days=2)).isoformat()
    old = (now - timedelta(days=180)).isoformat()
    records = [
        _record(
            "old-fact",
            created_at=old,
            bm25_score=0.8,
            confidence=0.8,
        ),
        _record(
            "fresh-fact",
            created_at=fresh,
            bm25_score=0.8,
            confidence=0.4,
        ),
        _record(
            "feedback-fact",
            created_at=recent,
            bm25_score=0.75,
            feedback_score=0.6,
            hit_count=3,
        ),
        _record(
            "correction",
            record_type="correction",
            created_at=recent,
            bm25_score=0.7,
        ),
    ]

    ranked = score_records(records, ranking_config=RankingConfig())

    ordered_ids = [record.id for record in ranked]
    assert ordered_ids.index("fresh-fact") < ordered_ids.index("old-fact")
    assert ordered_ids.index("feedback-fact") < ordered_ids.index("old-fact")
    assert ordered_ids[0] == "correction"


def test_gateway_rerank_long_term_records_uses_unified_configuration() -> None:
    service = MemoryService(store=InMemoryMemoryStore())
    ranking = RankingConfig(
        w_relevance=0.65,
        w_recency=0.0,
        w_feedback=0.10,
        w_type_bonus=0.0,
        w_confidence=0.25,
        w_outcome_utility=0.0,
    )
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="rank-agent",
        memory_config=_memory_config(ranking=ranking),
    )
    records = [
        _record(
            "older-high-confidence",
            created_at="2025-01-01T00:00:00+00:00",
            confidence=0.95,
            bm25_score=0.8,
        ),
        _record(
            "fresh-low-confidence",
            created_at="2026-03-27T00:00:00+00:00",
            confidence=0.20,
            bm25_score=0.8,
        ),
    ]

    ranked = adapter._rerank_long_term_records(records, use_search_scores=True)  # noqa: SLF001

    assert [record.id for record in ranked] == [
        "older-high-confidence",
        "fresh-low-confidence",
    ]
    assert "score_breakdown" in ranked[0].meta


def test_score_records_biases_toward_records_valid_at_temporal_anchor() -> None:
    stale = _record(
        "stale",
        created_at="2026-03-01T00:00:00+00:00",
        bm25_score=0.8,
    )
    stale = replace(
        stale, event_time=stale.created_at, valid_to="2026-03-10T00:00:00+00:00"
    )
    current = _record(
        "current",
        created_at="2026-03-15T00:00:00+00:00",
        bm25_score=0.8,
    )
    current = replace(current, event_time=current.created_at, valid_to=None)

    ranked = score_records(
        [stale, current],
        ranking_config=RankingConfig(),
        temporal_anchor=datetime(2026, 3, 5, tzinfo=timezone.utc),
    )

    assert [record.id for record in ranked] == ["stale", "current"]
