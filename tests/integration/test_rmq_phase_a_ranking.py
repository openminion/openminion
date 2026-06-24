from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from openminion.modules.memory.config import RankingConfig
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _retrieve_config(tmp_path: Path) -> dict:
    return {
        "version": 1,
        "retrievectl": {
            "storage": {
                "sqlite_path": str(tmp_path / "retrievectl.db"),
                "blob_root": str(tmp_path / "retrieve-blobs"),
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
                "recency_weight": 1.0,
                "k_conversational": 1,
                "k_knowledge": 4,
            },
        },
    }


# canonical ranking overrides for phase-a test (recency boost
# on fresh/stale).
_PHASE_A_RANKING = RankingConfig(
    recency_half_life_days=2.0,  # was retrieve.defaults.recency_half_life_hours=48
    mmr_enabled=False,
)


def _extract_bullets(content: str) -> list[str]:
    return [
        line.strip()[2:].strip()
        for line in content.splitlines()
        if line.strip().startswith("• ")
    ]


def test_phase_a_recency_boost_ranks_fresh_before_stale(tmp_path: Path) -> None:
    memory_service = MemoryService(store=SQLiteMemoryStore(tmp_path / "memory.db"))
    retrieve_ctl = RetrieveCtl(_retrieve_config(tmp_path))
    adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="rmq-a-ranking",
        retrieve_ctl=retrieve_ctl,
        ranking_config=_PHASE_A_RANKING,
    )
    try:
        fresh_at = datetime.now(timezone.utc).isoformat()
        stale_at = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        retrieve_ctl.ingest_source(
            source_type="doc",
            source_ref="doc://phase-a/fresh",
            text="shared token phase-a fresh-fact marker",
            scope="project",
            tags=["phase-a"],
            title="fresh-fact marker",
            created_at=fresh_at,
        )
        retrieve_ctl.ingest_source(
            source_type="doc",
            source_ref="doc://phase-a/stale",
            text="shared token phase-a stale-fact marker",
            scope="project",
            tags=["phase-a"],
            title="stale-fact marker",
            created_at=stale_at,
        )
        retrieve_ctl.store.execute(
            "UPDATE retrievectl_docs SET scope_key = ? WHERE source_ref IN (?, ?)",
            (
                "agent:rmq-a-ranking",
                "doc://phase-a/fresh",
                "doc://phase-a/stale",
            ),
        )
        retrieve_ctl.store.commit()

        content, _meta = adapter.build_retrieval_context_with_metadata(
            session_id="s-a-ranking",
            user_message="shared token phase-a marker",
        )
        bullets = _extract_bullets(content)
        assert bullets
        fresh_idx = next(i for i, line in enumerate(bullets) if "fresh-fact" in line)
        stale_idx = next(i for i, line in enumerate(bullets) if "stale-fact" in line)
        assert fresh_idx < stale_idx
    finally:
        retrieve_ctl.close()
