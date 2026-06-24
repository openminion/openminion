from __future__ import annotations

from pathlib import Path

from openminion.modules.memory.config import RankingConfig
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _retrieve_config(tmp_path: Path) -> dict:
    # ranking overrides moved off retrieve.defaults
    # onto memory ranking config (see `_PHASE_B_RANKING` below).
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
                "recency_weight": 0.3,
                "k_conversational": 1,
                "k_knowledge": 2,
            },
        },
    }


# canonical ranking overrides for phase-b test (diversity).
_PHASE_B_RANKING = RankingConfig(
    recency_half_life_days=2.0,  # was retrieve.defaults.recency_half_life_hours=48
    mmr_enabled=True,
    mmr_lambda=0.6,
)


def _extract_bullets(content: str) -> list[str]:
    return [
        line.strip()[2:].strip()
        for line in content.splitlines()
        if line.strip().startswith("• ")
    ]


def test_phase_b_diversity_limits_near_duplicates(tmp_path: Path) -> None:
    memory_service = MemoryService(store=SQLiteMemoryStore(tmp_path / "memory.db"))
    retrieve_ctl = RetrieveCtl(_retrieve_config(tmp_path))
    adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="rmq-b-diversity",
        retrieve_ctl=retrieve_ctl,
        ranking_config=_PHASE_B_RANKING,
    )
    try:
        for i in range(5):
            retrieve_ctl.ingest_source(
                source_type="doc",
                source_ref=f"doc://phase-b/dup-{i}",
                text=f"duplicate cluster phrase alpha alpha variation-{i}",
                scope="project",
                tags=["phase-b", "dup"],
                title=f"dup-{i}",
            )
        retrieve_ctl.store.execute(
            "UPDATE retrievectl_docs SET scope_key = ? WHERE source_ref LIKE ?",
            ("agent:rmq-b-diversity", "doc://phase-b/%"),
        )
        retrieve_ctl.store.commit()

        content, _meta = adapter.build_retrieval_context_with_metadata(
            session_id="s-b-div",
            user_message="cluster phrase alpha",
        )
        bullets = _extract_bullets(content)
        assert bullets
        duplicate_lines = [
            line for line in bullets if "duplicate cluster phrase alpha" in line
        ]
        assert len(duplicate_lines) <= 2
    finally:
        retrieve_ctl.close()
