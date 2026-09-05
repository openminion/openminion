from __future__ import annotations

from pathlib import Path

from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions
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
                # `recency_half_life_hours` dropped;
                # the test does not depend on recency behavior. Set via
                # `ranking_config=RankingConfig(...)` if needed later.
            },
        },
    }


def test_rcb_ingestion_bridge_integration(tmp_path: Path) -> None:
    memory_store = SQLiteMemoryStore(tmp_path / "memory.db")
    memory_service = MemoryService(store=memory_store)
    retrieve_ctl = RetrieveCtl(_retrieve_config(tmp_path))
    adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="rcb-ing-agent",
        retrieve_ctl=retrieve_ctl,
    )

    try:
        adapter.record_turn(
            session_id="rcb-session-1",
            run_id="run-1",
            request_id="req-1",
            channel="chat",
            target="user",
            user_message="remember: my project uses python 312",
            assistant_message="",
        )

        agent_records = memory_service.list(
            ListQueryOptions(scopes=["agent:rcb-ing-agent"], limit=20)
        )
        assert any(
            "my project uses python 312" in str(getattr(record, "content", "")).lower()
            for record in agent_records
        )
        record = next(
            record
            for record in agent_records
            if "my project uses python 312" in str(record.content).lower()
        )

        retrieved_items = retrieve_ctl.retrieve(
            query="python 312",
            purpose="act",
            scope={"agent": True},
            k=8,
            strategy="contextual",
        )
        assert any(
            str(item.get("ref_type", "")) == "mem"
            and "python 312" in str(item.get("text", "")).lower()
            for item in retrieved_items
        )
        retrieved = next(item for item in retrieved_items if item["ref_type"] == "mem")
        assert retrieved["meta"]["memory_id"] == record.id
        assert retrieved["ref_id"].startswith(f"mem:{record.id}#u=")
        stored_scope_key = retrieve_ctl.store.execute(
            "SELECT scope_key FROM retrievectl_docs WHERE source_ref = ?",
            (f"mem:{record.id}",),
        ).fetchone()["scope_key"]
        assert stored_scope_key == "agent:rcb-ing-agent"
        stored_title = retrieve_ctl.store.execute(
            "SELECT title FROM retrievectl_docs WHERE source_ref = ?",
            (f"mem:{record.id}",),
        ).fetchone()["title"]
        assert stored_title == record.title
    finally:
        retrieve_ctl.close()
