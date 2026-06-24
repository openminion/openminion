from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import RankingConfig, from_base_config
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import SearchQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _memory_config(*, ranking: RankingConfig) -> object:
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    return replace(cfg, ranking=ranking)


def test_outcome_weighted_ranking_prefers_positive_outcome_history(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    positive = MemoryRecord(
        id="mem-positive",
        scope="agent:phase10-agent",
        type="fact",
        title="Positive deploy rule",
        content="Deploys require a rollback rehearsal before rollout.",
        created_at="2026-03-20T00:00:00+00:00",
        updated_at="2026-03-20T00:00:00+00:00",
        confidence=0.6,
        meta={
            "feedback_score": 0.8,
            "outcome_success_count": 4,
            "outcome_failure_count": 0,
        },
    )
    negative = MemoryRecord(
        id="mem-negative",
        scope="agent:phase10-agent",
        type="fact",
        title="Negative deploy rule",
        content="Deploys require a rollback rehearsal before rollout.",
        created_at="2026-03-20T00:00:00+00:00",
        updated_at="2026-03-20T00:00:00+00:00",
        confidence=0.6,
        meta={
            "feedback_score": 0.0,
            "outcome_success_count": 0,
            "outcome_failure_count": 4,
        },
    )
    neutral = MemoryRecord(
        id="mem-neutral",
        scope="agent:phase10-agent",
        type="fact",
        title="Neutral deploy rule",
        content="Deploys require a rollback rehearsal before rollout.",
        created_at="2026-03-20T00:00:00+00:00",
        updated_at="2026-03-20T00:00:00+00:00",
        confidence=0.6,
        meta={},
    )
    store.put(positive)
    store.put(negative)
    store.put(neutral)

    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="phase10-agent",
        memory_config=_memory_config(ranking=RankingConfig()),
    )
    hits = service.search(
        SearchQueryOptions(
            query="what deploy rule should I follow?",
            scopes=["agent:phase10-agent"],
            limit=5,
        )
    )
    ranked = adapter._rerank_long_term_records(hits, use_search_scores=True)  # noqa: SLF001

    ordered_ids = [record.id for record in ranked]
    assert ordered_ids.index("mem-positive") < ordered_ids.index("mem-neutral")
    assert ordered_ids.index("mem-neutral") < ordered_ids.index("mem-negative")
    assert ranked[0].meta["score_breakdown"]["outcome_utility"] > 0.7
    assert ranked[-1].meta["score_breakdown"]["outcome_utility"] < 0.3


def test_outcome_weighted_ranking_keeps_legacy_neutral_records_rankable(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    neutral = MemoryRecord(
        id="mem-neutral",
        scope="agent:phase10-agent",
        type="fact",
        title="Neutral note",
        content="The deploy checklist requires smoke tests before rollout.",
        created_at="2026-03-20T00:00:00+00:00",
        updated_at="2026-03-20T00:00:00+00:00",
        confidence=0.6,
        meta={},
    )
    weak_positive = MemoryRecord(
        id="mem-weak-positive",
        scope="agent:phase10-agent",
        type="fact",
        title="Weak positive note",
        content="The deploy checklist requires smoke tests before rollout.",
        created_at="2026-03-20T00:00:00+00:00",
        updated_at="2026-03-20T00:00:00+00:00",
        confidence=0.6,
        meta={"feedback_score": 0.6, "outcome_success_count": 1},
    )
    store.put(neutral)
    store.put(weak_positive)

    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="phase10-agent",
        memory_config=_memory_config(ranking=RankingConfig()),
    )
    hits = service.search(
        SearchQueryOptions(
            query="what deploy checklist should I follow?",
            scopes=["agent:phase10-agent"],
            limit=5,
        )
    )
    ranked = adapter._rerank_long_term_records(hits, use_search_scores=True)  # noqa: SLF001

    neutral_record = next(record for record in ranked if record.id == "mem-neutral")
    weak_positive_record = next(
        record for record in ranked if record.id == "mem-weak-positive"
    )

    assert neutral_record.meta["score_breakdown"]["outcome_utility"] == 0.5
    assert weak_positive_record.meta["score_breakdown"]["outcome_utility"] > 0.5
    assert {record.id for record in ranked} == {"mem-neutral", "mem-weak-positive"}
