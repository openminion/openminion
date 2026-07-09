from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import RankingConfig, from_base_config
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _memory_config(*, ranking: RankingConfig):
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    retention = replace(cfg.retention, gc_enabled=False)
    return replace(cfg, ranking=ranking, retention=retention)


def _retrieve_config(tmp_path: Path) -> dict:
    return {
        "version": 1,
        "retrievectl": {
            "storage": {
                "sqlite_path": str(tmp_path / "retrievectl.db"),
                "blob_root": str(tmp_path / "blob"),
                "wal_mode": False,
            },
            "defaults": {
                "strategy": "contextual",
                "contextual_enabled": True,
                "embeddings_enabled": False,
                "lexical_candidate_count": 25,
                "snippet_tokens": 120,
                "chunk_target_tokens": 30,
                "chunk_min_tokens": 15,
                "chunk_max_tokens": 35,
                "doc_group_target_tokens": 40,
                "doc_group_min_tokens": 25,
                "doc_group_max_tokens": 60,
                "raptor_internal_k": 2,
                "raptor_leaf_k": 4,
            },
        },
    }


def test_capsule_order_changes_when_ranking_weights_change(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    store.put(
        MemoryRecord(
            id="older-trusted",
            scope="agent:rank-agent",
            type="fact",
            title="Older trusted deploy note",
            content="Deploy checklist uses trusted rollback verification.",
            tags=["deploy"],
            created_at="2025-01-01T00:00:00+00:00",
            updated_at="2025-01-01T00:00:00+00:00",
            confidence=0.95,
        )
    )
    store.put(
        MemoryRecord(
            id="fresh-note",
            scope="agent:rank-agent",
            type="fact",
            title="Fresh deploy note",
            content="Deploy checklist uses trusted rollback verification.",
            tags=["deploy"],
            created_at="2026-03-27T00:00:00+00:00",
            updated_at="2026-03-27T00:00:00+00:00",
            confidence=0.20,
        )
    )

    default_adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="rank-agent",
        memory_config=_memory_config(ranking=RankingConfig()),
    )
    weighted_adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="rank-agent",
        memory_config=_memory_config(
            ranking=RankingConfig(
                w_relevance=0.2,
                w_recency=0.0,
                w_feedback=0.0,
                w_type_bonus=0.0,
                w_confidence=0.8,
                w_outcome_utility=0.0,
            )
        ),
    )

    default_context, _ = default_adapter.build_context_with_metadata(
        session_id="rank-session",
        user_message="what deploy checklist should I follow?",
    )
    weighted_context, _ = weighted_adapter.build_context_with_metadata(
        session_id="rank-session",
        user_message="what deploy checklist should I follow?",
    )

    assert default_context.index("Fresh deploy note") < default_context.index(
        "Older trusted deploy note"
    )
    assert weighted_context.index("Older trusted deploy note") < weighted_context.index(
        "Fresh deploy note"
    )


def test_retrieval_context_and_retrieve_hits_carry_score_breakdown(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    service.write_record(
        scope="agent:rank-agent",
        record_type="fact",
        title="Memory deploy rule",
        content="Deploy checklist uses rollback verification and smoke tests.",
        tags=["deploy"],
    )

    retrieve_ctl = RetrieveCtl(_retrieve_config(tmp_path))
    retrieve_ctl.ingest_source(
        source_type="doc",
        source_ref="doc://deploy/rule",
        text="Rollback verification and smoke tests are required before deployment.",
        scope="project",
        tags=["deploy", "ops"],
        title="Deploy guide",
    )
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="rank-agent",
        project_id="rank-project",
        memory_config=_memory_config(ranking=RankingConfig()),
        retrieve_ctl=retrieve_ctl,
        ranking_config=RankingConfig(),
    )

    _content, _meta = adapter.build_retrieval_context_with_metadata(
        session_id="retrieval-rank",
        user_message="what deploy rule should I follow?",
    )

    hits = adapter._last_retrieved_items["retrieval-rank"]  # noqa: SLF001
    assert hits
    assert "score_breakdown" in hits[0]["meta"]
    assert hits[0]["meta"]["score_breakdown"]["unified_score"] == hits[0]["score"]
